"""Diagnostics: a hidden, read-only health page at /diagnostics.

Not linked from the navigation. It exists so the deployed app's behaviour
(platform, TLS trust store, image cache, memory against the container limit)
can be checked from the public URL without access to the hosting dashboard.
Shows nothing sensitive.
"""

from __future__ import annotations

import platform
import ssl
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
import truststore
from PIL import __version__ as pillow_version

from src.data import load_villagers
from src.images import get_thumbnail_cache, tls_self_test

@st.cache_resource
def _process_started() -> float:
    """Time of the first diagnostics view in this server process (a cold-start marker)."""
    return time.time()


TRUSTSTORE_BACKEND = {"win32": "Windows CryptoAPI", "darwin": "macOS Security framework"}.get(
    sys.platform, "OpenSSL + system CA store"
)


def _read_first(*paths: str) -> str | None:
    for p in paths:
        try:
            return Path(p).read_text().strip()
        except OSError:
            continue
    return None


def _memory() -> dict:
    """RSS and cgroup limit on Linux (what the hosting container enforces); n/a elsewhere."""
    rss = None
    status = _read_first("/proc/self/status")
    if status:
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
    limit = _read_first("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")
    limit_bytes = int(limit) if limit and limit.isdigit() else None
    mb = lambda b: f"{b / 2**20:,.0f} MB" if b else "n/a"  # noqa: E731
    return {
        "process_rss": mb(rss),
        "container_limit": mb(limit_bytes) if limit_bytes and limit_bytes < 2**50 else (limit or "n/a"),
        "rss_share_of_limit": f"{rss / limit_bytes:.0%}" if rss and limit_bytes and limit_bytes < 2**50 else "n/a",
    }


st.title("Diagnostics")
st.caption(
    f"First viewed in this server process {time.time() - _process_started():,.0f} s ago. "
    "Read-only; not linked from the app."
)

left, right = st.columns(2)
with left:
    st.subheader("Runtime")
    st.json({
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "streamlit": st.__version__,
        "pandas": pd.__version__,
        "pillow": pillow_version,
        "requests": requests.__version__,
        "truststore": truststore.__version__,
        "villagers_loaded": len(load_villagers()),
    })
    st.subheader("Memory")
    st.json(_memory())
with right:
    st.subheader("TLS")
    paths = ssl.get_default_verify_paths()
    st.json({
        "truststore_backend": TRUSTSTORE_BACKEND,
        "openssl": ssl.OPENSSL_VERSION,
        "default_cafile": paths.cafile,
        "default_capath": paths.capath,
    })
    if st.button("Run TLS self-test", icon=":material/verified_user:"):
        with st.spinner("Fetching concurrently from the image host and an untrusted-root host…"):
            result = tls_self_test()
        (st.success if result["passed"] else st.error)(
            "Verification on under concurrency" if result["passed"] else "Self-test FAILED"
        )
        st.json(result)

st.subheader("Image cache")
st.json(get_thumbnail_cache().stats())
