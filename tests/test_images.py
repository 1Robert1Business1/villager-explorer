"""Every way an image can fail must end in the placeholder, never an exception or a broken <img>."""

import io
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
import truststore
from PIL import Image

import src.images as images
from src.images import (
    ImageFetchError,
    Status,
    ThumbnailCache,
    fetch_bytes,
    image_candidates,
    placeholder_bytes,
    to_thumbnail,
)


def png(width, height, mode="RGBA"):
    buf = io.BytesIO()
    Image.new(mode, (width, height), (200, 100, 50, 255)[: len(mode)]).save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ to_thumbnail


@pytest.mark.parametrize("size", [(128, 128), (735, 1300), (30, 10)])
def test_thumbnail_is_square_png_whatever_the_source(size):
    # PNG specifically: st.image passes PNG through untouched but re-encodes other
    # formats on every rerun.
    out = Image.open(io.BytesIO(to_thumbnail(png(*size))))
    assert out.format == "PNG"
    assert out.size == (images.THUMB_SIZE, images.THUMB_SIZE)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"<!DOCTYPE html><html><body>404 Not Found</body></html>",  # an error page served as 200
        png(64, 64)[:60],  # truncated download
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,  # valid signature, garbage body
    ],
    ids=["empty", "html", "truncated", "corrupt"],
)
def test_undecodable_bytes_raise_fetch_error(data):
    with pytest.raises(ImageFetchError):
        to_thumbnail(data)


def test_refuses_implausibly_large_images(monkeypatch):
    monkeypatch.setattr(images, "MAX_SOURCE_PIXELS", 100)
    with pytest.raises(ImageFetchError, match="refusing"):
        to_thumbnail(png(20, 20))


def test_placeholder_is_a_valid_thumbnail():
    out = Image.open(io.BytesIO(placeholder_bytes()))
    assert out.size == (images.THUMB_SIZE, images.THUMB_SIZE)


# ------------------------------------------------------------------ fetch_bytes


IMAGE_URL = "https://dodo.ac/np/images/x/xx/Test.png"  # allowlisted, so tests reach the session


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.is_redirect = status in (301, 302, 303, 307, 308) and "Location" in self.headers
        self.is_permanent_redirect = status in (301, 308) and "Location" in self.headers

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i : i + n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize(
    "result, message",
    [
        (FakeResponse(404), "HTTP 404"),
        (FakeResponse(521), "HTTP 521"),  # what villagerdb.com (TidyTuesday's image host) returns
        (requests.ConnectionError("dns"), "ConnectionError"),
        (requests.Timeout("slow"), "Timeout"),
        (FakeResponse(200, headers={"Content-Length": str(10**9)}), "too large"),
    ],
    ids=["404", "521", "dns", "timeout", "huge"],
)
def test_fetch_failures_raise_fetch_error(result, message):
    with pytest.raises(ImageFetchError, match=message):
        fetch_bytes(FakeSession(result), IMAGE_URL)


def test_fetch_stops_reading_oversized_body_without_content_length(monkeypatch):
    monkeypatch.setattr(images, "MAX_DOWNLOAD_BYTES", 1000)
    with pytest.raises(ImageFetchError, match="too large"):
        fetch_bytes(FakeSession(FakeResponse(200, b"x" * 5000)), IMAGE_URL)


# ------------------------------------------------------------ SSRF protections


@pytest.mark.parametrize(
    "url",
    [
        "http://dodo.ac/np/images/a.png",  # plaintext
        "https://evil.example/a.png",  # not allowlisted
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "https://localhost/admin",
        "https://dodo.ac.evil.example/a.png",  # allowlisted name as a prefix
        "https://user:pw@dodo.ac/a.png",  # embedded credentials
        "https://dodo.ac:8443/a.png",  # non-default port
        "file:///etc/passwd",
    ],
)
def test_disallowed_urls_are_refused_before_any_request(url):
    session = FakeSession(FakeResponse(200, png(10, 10)))
    with pytest.raises(ImageFetchError, match="refusing"):
        fetch_bytes(session, url)
    assert session.calls == []  # nothing left the process


def test_redirects_are_not_followed():
    session = FakeSession(FakeResponse(302, headers={"Location": "https://169.254.169.254/"}))
    with pytest.raises(ImageFetchError, match="refusing redirect"):
        fetch_bytes(session, IMAGE_URL)
    assert session.calls[0][1]["allow_redirects"] is False


def test_tls_failures_are_not_retried_but_connection_timeouts_are():
    from urllib3.exceptions import ConnectTimeoutError, MaxRetryError, SSLError

    retry = images.build_session().get_adapter("https://dodo.ac/").max_retries
    with pytest.raises(MaxRetryError) as exc:
        retry.increment(method="GET", url="/", error=SSLError("certificate verify failed"))
    assert isinstance(exc.value.reason, SSLError)
    # A connection timeout still gets its one retry.
    assert retry.increment(method="GET", url="/", error=ConnectTimeoutError("slow")) is not None


def test_session_has_no_plaintext_http_adapter():
    session = images.build_session()
    assert isinstance(session.get_adapter("https://dodo.ac/"), images._TruststoreAdapter)
    assert not isinstance(session.get_adapter("http://dodo.ac/"), images._TruststoreAdapter)


# --------------------------------------------------------------- ThumbnailCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_cache(behaviour, clock=None, **kw):
    """behaviour: url -> bytes, or an Exception to raise."""
    calls = []

    def loader(url):
        calls.append(url)
        outcome = behaviour[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return ThumbnailCache(loader, clock=clock or Clock(), **kw), calls


def test_dead_icon_falls_back_to_artwork():
    cache, _ = make_cache({"icon": ImageFetchError("HTTP 404"), "art": b"ART"})
    assert cache.prefetch([["icon", "art"]], budget=5) == 0
    resolved = cache.resolve(["icon", "art"])
    assert (resolved.image, resolved.source, resolved.state) == (b"ART", "art", "ok")


def test_all_candidates_dead_resolves_to_placeholder_state():
    cache, _ = make_cache({"icon": ImageFetchError("x"), "art": RuntimeError("anything at all")})
    cache.prefetch([["icon", "art"]], budget=5)
    resolved = cache.resolve(["icon", "art"])
    assert resolved.image is None and resolved.state == "failed"


def test_failures_are_not_refetched_until_ttl_expires():
    clock = Clock()
    cache, calls = make_cache({"dead": ImageFetchError("x")}, clock=clock, failure_ttl=300)
    cache.prefetch([["dead"]], budget=5)
    cache.prefetch([["dead"]], budget=5)
    assert calls == ["dead"]
    assert cache.peek("dead") is Status.FAILED
    clock.now = 301
    assert cache.peek("dead") is Status.MISSING
    cache.prefetch([["dead"]], budget=5)
    assert calls == ["dead", "dead"]


def test_successes_are_cached_and_lru_bounded():
    cache, calls = make_cache({"a": b"A", "b": b"B", "c": b"C"}, max_entries=2)
    cache.prefetch([["a"], ["b"]], budget=5)
    cache.prefetch([["a"], ["b"]], budget=5)
    assert sorted(calls) == ["a", "b"]
    cache.peek("a")  # touch a, so b is least recently used
    cache.prefetch([["c"]], budget=5)
    assert cache.peek("b") is Status.MISSING
    assert cache.peek("a") == b"A" and cache.peek("c") == b"C"


def test_slow_images_render_as_pending_and_finish_in_background():
    release = threading.Event()

    def slow_loader(url):
        release.wait(5)
        return b"SLOW"

    cache = ThumbnailCache(slow_loader)
    assert cache.prefetch([["slow"]], budget=0.05) == 1
    assert cache.resolve(["slow"]).state == "pending"
    release.set()
    cache.prefetch([["slow"]], budget=5)
    assert cache.resolve(["slow"]).image == b"SLOW"


def test_failures_are_logged_and_counted_by_reason(caplog):
    cache, _ = make_cache({"dead": ImageFetchError("HTTP 404"), "ok": b"OK"})
    with caplog.at_level("WARNING", logger="src.images"):
        cache.prefetch([["dead"], ["ok"]], budget=5)
    assert "dead" in caplog.text and "HTTP 404" in caplog.text
    stats = cache.stats()
    assert stats["loads_ok"] == 1 and stats["cached_thumbnails"] == 1
    assert stats["failure_reasons"] == {"ImageFetchError: HTTP 404": 1}
    assert stats["page_prefetch_ms"]["count"] == 1


def test_each_worker_thread_gets_its_own_tls_session():
    # Regression guard: a truststore SSLContext shared across threads let concurrent
    # requests skip certificate verification (see src/images.py docstring).
    with ThreadPoolExecutor(4) as pool:
        ids = set(pool.map(lambda _: (threading.get_ident(), id(images.thread_session())), range(40)))
    threads = {t for t, _ in ids}
    sessions = {s for _, s in ids}
    assert len(sessions) == len(threads)  # one session per thread, none shared
    assert images.thread_session() is images.thread_session()  # stable within a thread


# ------------------------------------------- live TLS regression (network tests)
# The truststore bug these guard against only exists on Windows and macOS, so CI runs
# them on windows-latest as well as ubuntu-latest (.github/workflows/tls-live.yml).

BAD_CERT_URL = "https://untrusted-root.badssl.com/"
GOOD_URL = "https://dodo.ac/np/images/4/4f/Ace_NH_Villager_Icon.png"
BUG_PLATFORMS = ("win32", "darwin")  # truststore backends that toggle verify_mode


def _concurrent_probe(get_session) -> list[tuple[str, str]]:
    """The grid's access pattern: 12 threads, good and bad hosts interleaved."""

    def outcome(url):
        try:
            fetch_bytes(get_session(), url, allowed_hosts=images.SELF_TEST_HOSTS)
            return url, "ok"
        except ImageFetchError as exc:
            return url, str(exc)

    with ThreadPoolExecutor(12) as pool:
        return list(pool.map(outcome, [GOOD_URL] * 40 + [BAD_CERT_URL] * 20))


def _verdict(results):
    good = [why for url, why in results if url == GOOD_URL]
    bad = [why for url, why in results if url == BAD_CERT_URL]
    return images.classify_tls_probe(
        good_ok=good.count("ok"), good_total=len(good),
        bad_accepted=bad.count("ok"), bad_rejected_by_tls=bad.count("SSLError"), bad_total=len(bad),
    )


@pytest.mark.network
def test_concurrent_fetches_reject_untrusted_certificates():
    verdict, reason = _verdict(_concurrent_probe(images.thread_session))  # the production path
    assert verdict != "failed", reason  # a bad certificate accepted: a real failure
    if verdict == "inconclusive":
        # An outage at the test hosts proves nothing either way. Skip, don't pass:
        # with REQUIRE_NETWORK_TESTS=1 the run then fails as "nothing tested".
        pytest.skip(reason)
    assert verdict == "passed", reason


@pytest.mark.network
@pytest.mark.skipif(sys.platform not in BUG_PLATFORMS, reason="the truststore bug is Windows/macOS-only")
# Accepting bad certificates is the point of this test; urllib3 rightly warns about it.
@pytest.mark.filterwarnings("ignore::urllib3.exceptions.InsecureRequestWarning")
def test_regression_check_would_catch_a_reverted_fix():
    """Mutation check: with the fix reverted (one Session shared by all threads),
    the probe above must see bad certificates accepted on this platform.

    This proves the regression test is actually guarding something on this runner.
    If it starts failing, the bug no longer reproduces here (e.g. fixed upstream
    in truststore): the per-thread sessions may no longer be needed, and the
    regression test above has stopped proving anything on this platform.
    """
    shared = images.build_session()
    results = _concurrent_probe(lambda: shared)
    bad = [why for url, why in results if url == BAD_CERT_URL]
    accepted = bad.count("ok")
    if not accepted and bad.count("SSLError") < len(bad) // 2:
        # Most probes never reached a TLS handshake: the test host is unreachable,
        # so this run can't say whether the bypass still reproduces.
        pytest.skip(f"inconclusive: only {bad.count('SSLError')} of {len(bad)} probes reached TLS")
    assert accepted > 0, (
        f"CANARY, not an app failure: the truststore shared-context bypass no longer "
        f"reproduces on {sys.platform} (0 of {len(bad)} untrusted-root requests accepted with a "
        f"shared session; truststore {truststore.__version__}). The upstream bug may be "
        f"fixed. The app is still safe (it uses one session per thread), but "
        f"test_concurrent_fetches_reject_untrusted_certificates no longer proves anything "
        f"on this platform. Check the truststore changelog, then update or retire this "
        f"canary and reconsider the per-thread workaround in src/images.py."
    )


def test_self_test_only_passes_on_real_tls_rejection(monkeypatch):
    # If the bad host were refused by the allowlist instead of by TLS, the self-test
    # must report failure rather than a false "verification on".
    monkeypatch.setattr(images, "SELF_TEST_HOSTS", images.IMAGE_HOSTS)
    monkeypatch.setattr(images, "thread_session", lambda: FakeSession(FakeResponse(200, png(8, 8))))
    result = images.tls_self_test(concurrency=2)
    assert result["untrusted_cert_accepted"] == "0/2"
    assert result["passed"] is False
    assert result["verdict"] == "inconclusive"  # never reached TLS, so nothing proven


@pytest.mark.parametrize(
    "counts, verdict",
    [
        (dict(good_ok=24, good_total=24, bad_accepted=0, bad_rejected_by_tls=12, bad_total=12), "passed"),
        # Any acceptance is a failure, whatever else happened.
        (dict(good_ok=24, good_total=24, bad_accepted=1, bad_rejected_by_tls=11, bad_total=12), "failed"),
        (dict(good_ok=0, good_total=24, bad_accepted=1, bad_rejected_by_tls=0, bad_total=12), "failed"),
        # Test host unreachable (timeouts): nothing accepted, nothing proven.
        (dict(good_ok=24, good_total=24, bad_accepted=0, bad_rejected_by_tls=7, bad_total=12), "inconclusive"),
        (dict(good_ok=20, good_total=24, bad_accepted=0, bad_rejected_by_tls=12, bad_total=12), "inconclusive"),
    ],
)
def test_tls_probe_verdicts_separate_insecure_from_unreachable(counts, verdict):
    assert images.classify_tls_probe(**counts)[0] == verdict


def test_candidates_prefer_icon_and_skip_missing():
    assert image_candidates("icon.png", "art.png") == ["icon.png", "art.png"]
    assert image_candidates(float("nan"), "art.png") == ["art.png"]
    assert image_candidates(None, None) == []
