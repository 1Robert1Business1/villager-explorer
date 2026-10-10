"""Villager images: fetch, validate, normalise, cache, and never show a broken image.

Every image reaches the browser as bytes this module has already decoded and
re-encoded. The browser is never handed a remote URL, so it can never draw a
broken-image icon: anything that goes wrong on the way becomes the placeholder.

Pipeline for one villager
-------------------------
1. Candidate URLs in order of preference: the ~7 KB New Horizons icon, then the
   main artwork (~0.8 MB). Non-NH villagers only have the artwork.
2. Each URL is fetched server-side (timeouts, size cap, one retry on 429/5xx),
   decoded by Pillow (which rejects HTML error pages, truncated files and
   decompression bombs), fitted into a THUMB_SIZE square and re-encoded as a
   palette PNG. Artwork shrinks from ~800 KB to ~11 KB. (PNG rather than WebP
   because st.image re-encodes anything that isn't PNG/JPEG on every rerun; a
   quantised PNG is the same size as WebP for this flat art, and passes through
   untouched.)
3. Results live in a process-wide ThumbnailCache shared by every session:
   successes for good, failures for FAILURE_TTL seconds so a dead URL isn't
   re-requested on every rerun, but a transient blip heals itself.
4. If every candidate fails, or hasn't arrived within the page's time budget,
   the card gets the placeholder.

TLS: requests verifies against the OS trust store via truststore, so this works
behind antivirus or corporate TLS inspection without ever disabling
verification. On Linux (Streamlit
Community Cloud) that is simply the system CA bundle.

Each worker thread gets its own Session, and so its own truststore SSLContext.
This is a security requirement, not tidiness: don't share truststore contexts
across threads (see sethmlarson/truststore#209). tests/test_images.py guards it.
"""

from __future__ import annotations

import io
import logging
import ssl
import threading
import time
from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

import requests
import streamlit as st
import truststore
from PIL import Image, ImageDraw
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

THUMB_SIZE = 240  # px square: 2x the card's display size, so it stays sharp on HiDPI
CONNECT_TIMEOUT = 3.05
READ_TIMEOUT = 8
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024  # largest real artwork is ~2.2 MB
MAX_SOURCE_PIXELS = 25_000_000  # refuse to decode anything implausibly large
FAILURE_TTL = 300  # seconds before a failed URL is tried again
MAX_CACHED = 1200  # thumbnails are ~10-15 KB, so this caps memory at roughly 15 MB
MAX_WORKERS = 12
# Identifies the project (not a person); never put a personal email or name here.
USER_AGENT = "villager-explorer/1.0 (+https://github.com/1Robert1Business1/villager-explorer)"

# The only hosts the server will fetch from. Together with https-only and no
# redirects, this keeps the image fetcher from being turned against anything
# else (SSRF), e.g. a poisoned dataset refresh pointing at internal addresses,
# or a redirect to a cloud metadata endpoint.
IMAGE_HOSTS = frozenset({"dodo.ac"})

PLACEHOLDER_PATH = Path(__file__).resolve().parent.parent / "assets" / "placeholder.png"

log = logging.getLogger(__name__)


class ImageFetchError(Exception):
    """Any reason a URL did not yield a usable image."""


# ---------------------------------------------------------------------- network


class _TruststoreAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return super().init_poolmanager(*args, **kwargs)


def build_session() -> requests.Session:
    retry = Retry(
        total=1,
        connect=1,  # connection timeouts/refusals/DNS blips are worth one retry
        read=0,
        status=1,
        # Everything else, notably TLS certificate failures, fails immediately. A bad
        # certificate is never transient; retrying only doubles the requests and logs.
        other=0,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=False,  # never let a server stall the page
    )
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    adapter = _TruststoreAdapter(max_retries=retry, pool_connections=4, pool_maxsize=MAX_WORKERS)
    session.mount("https://", adapter)  # no http:// adapter: plaintext is never used
    return session


def check_url_allowed(url: str, allowed_hosts: frozenset[str] = IMAGE_HOSTS) -> None:
    """Raise ImageFetchError unless `url` is https on an allowlisted host (no creds, default port)."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ImageFetchError(f"refusing non-https URL scheme {parts.scheme!r}")
    if parts.username or parts.password or parts.port not in (None, 443):
        raise ImageFetchError("refusing URL with credentials or a non-default port")
    if (parts.hostname or "").lower() not in allowed_hosts:
        raise ImageFetchError(f"refusing host {parts.hostname!r} (not allowlisted)")


def fetch_bytes(
    session: requests.Session, url: str, allowed_hosts: frozenset[str] = IMAGE_HOSTS
) -> bytes:
    """Download `url`, refusing disallowed hosts, redirects, error statuses and oversized bodies."""
    check_url_allowed(url, allowed_hosts)
    try:
        with session.get(
            url,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            stream=True,
            allow_redirects=False,  # a redirect could lead off the allowlist
        ) as resp:
            if resp.is_redirect or resp.is_permanent_redirect:
                raise ImageFetchError(f"refusing redirect (HTTP {resp.status_code})")
            if resp.status_code != 200:
                raise ImageFetchError(f"HTTP {resp.status_code}")
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
                raise ImageFetchError(f"too large ({declared} bytes)")
            body = bytearray()
            for chunk in resp.iter_content(64 * 1024):
                body.extend(chunk)
                if len(body) > MAX_DOWNLOAD_BYTES:
                    raise ImageFetchError("too large (streamed)")
            return bytes(body)
    except requests.RequestException as exc:
        raise ImageFetchError(type(exc).__name__) from exc


# ---------------------------------------------------------------------- imaging


def _encode_png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.quantize(256, method=Image.Quantize.FASTOCTREE).save(out, format="PNG", optimize=True)
    return out.getvalue()


def to_thumbnail(data: bytes, size: int = THUMB_SIZE) -> bytes:
    """Decode arbitrary image bytes and return a `size`-square PNG with the art centred.

    Scales up or down to fit, preserving aspect ratio, so 128 px icons and
    1300 px-tall artwork produce cards that line up.
    """
    try:
        with Image.open(io.BytesIO(data)) as src:
            if src.width * src.height > MAX_SOURCE_PIXELS:
                raise ImageFetchError(f"refusing to decode {src.width}x{src.height}")
            src.load()  # force a full decode: catches truncated files, not just bad headers
            img = src.convert("RGBA")
    except ImageFetchError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        # UnidentifiedImageError (an HTML error page, say) is an OSError subclass.
        raise ImageFetchError(f"not a decodable image: {type(exc).__name__}") from exc

    img = img.crop(img.getbbox() or (0, 0, img.width, img.height))  # trim transparent margins
    scale = min(size / img.width, size / img.height)
    fitted = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.LANCZOS,
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return _encode_png(canvas)


def _drawn_placeholder(size: int = THUMB_SIZE) -> bytes:
    """Last-resort placeholder if even the asset file is missing or unreadable."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pad = size // 10
    ImageDraw.Draw(img).ellipse((pad, pad, size - pad, size - pad), fill=(214, 226, 205, 255))
    return _encode_png(img)


@st.cache_data(show_spinner=False)
def placeholder_bytes() -> bytes:
    """Shown when every image candidate for a villager has failed."""
    try:
        return to_thumbnail(PLACEHOLDER_PATH.read_bytes())
    except (OSError, ImageFetchError):
        return _drawn_placeholder()


@st.cache_data(show_spinner=False)
def loading_placeholder_bytes() -> bytes:
    """The placeholder at low opacity: "on its way", distinct from "unavailable"."""
    with Image.open(io.BytesIO(placeholder_bytes())) as img:
        rgba = img.convert("RGBA")
    rgba.putalpha(rgba.getchannel("A").point(lambda a: a * 35 // 100))
    return _encode_png(rgba)


# ------------------------------------------------------------------------ cache


class Status(Enum):
    MISSING = "missing"  # never tried, or failure has expired
    PENDING = "pending"  # fetch in flight
    FAILED = "failed"  # failed recently; don't retry yet


@dataclass(frozen=True)
class Resolved:
    """What a card should show: thumbnail bytes, or None for the placeholder."""

    image: bytes | None
    source: str | None  # the URL that produced it
    state: str  # "ok" | "failed" (every candidate failed) | "pending" (still loading)


class ThumbnailCache:
    """Thread-safe URL -> thumbnail cache with LRU eviction and expiring failures.

    Fetches run on a small shared pool, deduplicated, so concurrent sessions
    asking for the same image trigger one download.
    """

    def __init__(
        self,
        loader: Callable[[str], bytes],
        *,
        failure_ttl: float = FAILURE_TTL,
        max_entries: int = MAX_CACHED,
        max_workers: int = MAX_WORKERS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._failure_ttl = failure_ttl
        self._max_entries = max_entries
        self._clock = clock
        self._ok: OrderedDict[str, bytes] = OrderedDict()
        self._failed: dict[str, float] = {}  # url -> time of failure
        self._inflight: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="thumb")
        # Observability, surfaced on the diagnostics page and in logs.
        self._loads_ok = 0
        self._failure_reasons: Counter[str] = Counter()
        self._prefetch_ms: deque[float] = deque(maxlen=50)

    # -- inspection (never blocks, never fetches)

    def peek(self, url: str) -> bytes | Status:
        with self._lock:
            if url in self._ok:
                self._ok.move_to_end(url)
                return self._ok[url]
            if url in self._inflight:
                return Status.PENDING
            failed_at = self._failed.get(url)
            if failed_at is not None and self._clock() - failed_at < self._failure_ttl:
                return Status.FAILED
            return Status.MISSING

    def resolve(self, candidates: Sequence[str]) -> Resolved:
        """First available thumbnail among `candidates`, walking past known failures."""
        for url in candidates:
            hit = self.peek(url)
            if isinstance(hit, bytes):
                return Resolved(hit, url, "ok")
            if hit is Status.FAILED:
                continue
            return Resolved(None, None, "pending")
        return Resolved(None, None, "failed")

    # -- fetching

    def _schedule(self, url: str) -> Future | None:
        with self._lock:
            if url in self._ok:
                return None
            if url in self._inflight:
                return self._inflight[url]
            failed_at = self._failed.get(url)
            if failed_at is not None and self._clock() - failed_at < self._failure_ttl:
                return None
            future = self._pool.submit(self._load, url)
            self._inflight[url] = future
            return future

    def _load(self, url: str) -> None:
        try:
            thumb = self._loader(url)
        except Exception as exc:  # noqa: BLE001 - any failure becomes the placeholder
            reason = f"{type(exc).__name__}: {exc}"[:120]
            # Logged once per URL per FAILURE_TTL, so a dead link can't flood the logs.
            log.warning("Image unavailable, showing placeholder: %s (%s)", url, reason)
            with self._lock:
                self._failed[url] = self._clock()
                self._inflight.pop(url, None)
                self._failure_reasons[reason] += 1
            return
        with self._lock:
            self._loads_ok += 1
            self._ok[url] = thumb
            self._ok.move_to_end(url)
            while len(self._ok) > self._max_entries:
                self._ok.popitem(last=False)
            self._failed.pop(url, None)
            self._inflight.pop(url, None)

    def prefetch(self, chains: Iterable[Sequence[str]], budget: float) -> int:
        """Load the first working candidate of each chain, spending at most `budget` seconds.

        Walks each chain in rounds: fetch every chain's current candidate in
        parallel; chains whose candidate failed advance to the next one. Work
        still running at the deadline carries on in the background and shows up
        on the next rerun. Returns how many chains are still unresolved.
        """
        started = time.perf_counter()
        try:
            return self._prefetch(chains, budget)
        finally:
            with self._lock:
                self._prefetch_ms.append((time.perf_counter() - started) * 1000)

    def _prefetch(self, chains: Iterable[Sequence[str]], budget: float) -> int:
        deadline = self._clock() + budget
        chains = [list(c) for c in chains if c]
        while True:
            waiting: list[Future] = []
            unresolved = 0
            for chain in chains:
                result = self.resolve(chain)
                if result.state != "pending":
                    continue
                unresolved += 1
                url = next(u for u in chain if self.peek(u) is not Status.FAILED)
                future = self._schedule(url)
                if future is not None:
                    waiting.append(future)
            remaining = deadline - self._clock()
            if not waiting or remaining <= 0:
                return unresolved
            wait(waiting, timeout=remaining)

    def stats(self) -> dict:
        """A snapshot for the diagnostics page."""
        with self._lock:
            now = self._clock()
            timings = sorted(self._prefetch_ms)
            return {
                "cached_thumbnails": len(self._ok),
                "cached_bytes": sum(len(b) for b in self._ok.values()),
                "in_flight": len(self._inflight),
                "failing_now": sum(1 for t in self._failed.values() if now - t < self._failure_ttl),
                "loads_ok": self._loads_ok,
                "failure_reasons": dict(self._failure_reasons.most_common(10)),
                "page_prefetch_ms": {
                    "count": len(timings),
                    "median": round(timings[len(timings) // 2]) if timings else None,
                    "max": round(timings[-1]) if timings else None,
                },
            }


_thread_local = threading.local()


def thread_session() -> requests.Session:
    """This thread's own Session. Never share one across threads (see module docstring)."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = _thread_local.session = build_session()
    return session


def load_thumbnail(url: str) -> bytes:
    return to_thumbnail(fetch_bytes(thread_session(), url))


@st.cache_resource(show_spinner=False)
def get_thumbnail_cache() -> ThumbnailCache:
    """One cache and worker pool per server process, shared by all browser sessions."""
    return ThumbnailCache(load_thumbnail)


SELF_TEST_GOOD = "https://dodo.ac/np/images/4/4f/Ace_NH_Villager_Icon.png"
SELF_TEST_BAD = "https://untrusted-root.badssl.com/"  # a chain no trust store accepts
# The bad host must get as far as the TLS handshake, or the test would "pass"
# on an allowlist refusal without exercising verification at all.
SELF_TEST_HOSTS = IMAGE_HOSTS | {"untrusted-root.badssl.com"}
SELF_TEST_COOLDOWN = 60  # seconds; the page is public, so it mustn't be a traffic generator

_self_test_lock = threading.Lock()
_self_test_last: tuple[float, dict] | None = None


def tls_self_test_throttled() -> dict:
    """tls_self_test at most once per SELF_TEST_COOLDOWN per process; otherwise the last result."""
    global _self_test_last
    with _self_test_lock:  # also serialises concurrent clicks from different sessions
        now = time.monotonic()
        if _self_test_last and now - _self_test_last[0] < SELF_TEST_COOLDOWN:
            age = round(now - _self_test_last[0])
            return {**_self_test_last[1], "cached_result_age_s": age}
        result = tls_self_test()
        _self_test_last = (time.monotonic(), result)
        return result


def tls_self_test(concurrency: int = MAX_WORKERS) -> dict:
    """Exercise the real fetch path the way the grid does (many threads at once).

    Verdict:
    * "failed": an untrusted certificate was accepted. A real security failure.
    * "inconclusive": nothing was accepted, but some probes never reached a TLS
      handshake (test host unreachable, timeouts). Nothing proven either way.
    * "passed": the image host loaded every time and every untrusted-root probe
      was rejected by TLS verification itself. Proof that verification is on,
      under concurrency, on whatever platform and trust store this runs on.
    """

    def probe(url: str) -> tuple[str, bool, str]:
        try:
            fetch_bytes(thread_session(), url, allowed_hosts=SELF_TEST_HOSTS)
            return url, True, "ok"
        except ImageFetchError as exc:
            return url, False, str(exc)

    urls = [SELF_TEST_GOOD] * (concurrency * 2) + [SELF_TEST_BAD] * concurrency
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(probe, urls))
    good = [r for r in results if r[0] == SELF_TEST_GOOD]
    bad = [r for r in results if r[0] == SELF_TEST_BAD]
    accepted = sum(ok for _, ok, _ in bad)
    rejected_by_tls = sum(why == "SSLError" for _, ok, why in bad)
    verdict, reason = classify_tls_probe(
        good_ok=sum(ok for _, ok, _ in good), good_total=len(good),
        bad_accepted=accepted, bad_rejected_by_tls=rejected_by_tls, bad_total=len(bad),
    )
    return {
        "verdict": verdict,
        "reason": reason,
        "passed": verdict == "passed",
        "image_host_ok": f"{sum(ok for _, ok, _ in good)}/{len(good)}",
        "untrusted_cert_accepted": f"{accepted}/{len(bad)}",
        "untrusted_cert_rejected_by_tls": f"{rejected_by_tls}/{len(bad)}",
        "untrusted_cert_errors": sorted({why for _, ok, why in bad if not ok}),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def classify_tls_probe(
    *, good_ok: int, good_total: int, bad_accepted: int, bad_rejected_by_tls: int, bad_total: int
) -> tuple[str, str]:
    """Shared by the self-test and the network tests, so both judge outcomes the same way."""
    if bad_accepted:
        return "failed", f"{bad_accepted} of {bad_total} untrusted certificates were ACCEPTED"
    unreached = bad_total - bad_rejected_by_tls
    if unreached or good_ok < good_total:
        return "inconclusive", (
            f"test hosts partly unreachable: {unreached} of {bad_total} untrusted-host probes "
            f"never reached a TLS handshake, {good_total - good_ok} of {good_total} image fetches "
            "failed. None were accepted, but nothing is proven; try again later"
        )
    return "passed", f"all {bad_total} untrusted certificates rejected by TLS; image host OK"


def image_candidates(icon_url: str | None, image_url: str | None) -> list[str]:
    """Preferred image first: the light NH icon, then the full artwork."""
    return [u for u in dict.fromkeys((icon_url, image_url)) if isinstance(u, str) and u]
