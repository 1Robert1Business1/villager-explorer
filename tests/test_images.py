"""Every way an image can fail must end in the placeholder, never an exception or a broken <img>."""

import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
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


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

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

    def get(self, url, **kwargs):
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
        fetch_bytes(FakeSession(result), "https://example.invalid/x.png")


def test_fetch_stops_reading_oversized_body_without_content_length(monkeypatch):
    monkeypatch.setattr(images, "MAX_DOWNLOAD_BYTES", 1000)
    with pytest.raises(ImageFetchError, match="too large"):
        fetch_bytes(FakeSession(FakeResponse(200, b"x" * 5000)), "https://example.invalid/x.png")


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


def test_each_worker_thread_gets_its_own_tls_session():
    # Regression guard: a truststore SSLContext shared across threads let concurrent
    # requests skip certificate verification (see src/images.py docstring).
    with ThreadPoolExecutor(4) as pool:
        ids = set(pool.map(lambda _: (threading.get_ident(), id(images.thread_session())), range(40)))
    threads = {t for t, _ in ids}
    sessions = {s for _, s in ids}
    assert len(sessions) == len(threads)  # one session per thread, none shared
    assert images.thread_session() is images.thread_session()  # stable within a thread


@pytest.mark.skipif(not os.environ.get("NETWORK_TESTS"), reason="set NETWORK_TESTS=1 to run")
def test_concurrent_fetches_reject_untrusted_certificates():
    bad, good = "https://untrusted-root.badssl.com/", "https://dodo.ac/np/images/4/4f/Ace_NH_Villager_Icon.png"

    def accepted(url):
        try:
            fetch_bytes(images.thread_session(), url)
            return url == bad
        except ImageFetchError:
            return False

    with ThreadPoolExecutor(12) as pool:
        assert sum(pool.map(accepted, [good] * 40 + [bad] * 20)) == 0


def test_candidates_prefer_icon_and_skip_missing():
    assert image_candidates("icon.png", "art.png") == ["icon.png", "art.png"]
    assert image_candidates(float("nan"), "art.png") == ["art.png"]
    assert image_candidates(None, None) == []
