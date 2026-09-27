"""Reusable Streamlit UI pieces: the villager card and the card grid.

Images come from src.images as already-validated bytes (or the placeholder), so
nothing here can render a broken image.
"""

from __future__ import annotations

import html
from collections.abc import Callable

import pandas as pd
import streamlit as st

from src.filters import UNKNOWN
from src.images import get_thumbnail_cache, image_candidates, placeholder_bytes

CARD_WIDTH = 164  # px; two fit side by side on a 375 px phone, and they wrap to any width
IMAGE_WIDTH = 112
PAGE_IMAGE_BUDGET = 8.0  # seconds a page render will wait for cold images
GRID_KEY = "villager-grid"

# Wiki colours for the name chip when a villager has none (the 73 non-NH villagers).
DEFAULT_CHIP = ("#e8eee4", "#2f3b2a")


def _chip(name: str, background: object, text: object) -> str:
    bg = background if isinstance(background, str) else DEFAULT_CHIP[0]
    fg = text if isinstance(text, str) else DEFAULT_CHIP[1]
    return (
        f'<div style="text-align:center;line-height:1.9;margin-bottom:0.15rem">'
        f'<span style="background:{bg};color:{fg};'
        "padding:0.15rem 0.7rem;border-radius:999px;font-weight:600;font-size:0.95rem;"
        f'display:inline-block">{html.escape(name)}</span></div>'
    )


def _value(v: object) -> str:
    return str(v) if isinstance(v, str) and v else UNKNOWN


def _short_birthday(villager: pd.Series) -> str:
    month, day = villager["birthday_month"], villager["birthday_day"]
    if pd.isna(month) or pd.isna(day):
        return UNKNOWN
    return f"{str(month)[:3]} {day}"


def villager_card(villager: pd.Series, image: bytes | None) -> None:
    """One villager: image (or placeholder), name chip in their wiki colours, key facts."""
    with st.container(border=True, width=CARD_WIDTH, horizontal_alignment="center", gap="xsmall"):
        # Size via the container, not st.image(width=...): an int width makes
        # Streamlit downscale the 2x thumbnail server-side, losing HiDPI sharpness.
        with st.container(width=IMAGE_WIDTH):
            st.image(image if image is not None else placeholder_bytes(), width="stretch")
        st.markdown(
            _chip(villager["name"], villager["title_color"], villager["text_color"]),
            unsafe_allow_html=True,
        )
        st.caption(
            f"{villager['species']} · {villager['personality']}  \n"
            f":material/interests: {_value(villager['hobby'])}"
            f"&nbsp;&nbsp;:material/cake: {_short_birthday(villager)}",
            text_alignment="center",
        )
        if not villager["in_nh"]:
            st.badge("Not in New Horizons", color="gray")


def villager_grid(villagers: pd.DataFrame, on_retry: Callable[[], None] | None = None) -> None:
    """Render cards for `villagers`, loading their images in parallel first.

    Waits up to PAGE_IMAGE_BUDGET seconds for cold images. Anything slower shows
    the placeholder for now and keeps loading in the background; a note offers a
    refresh rather than pretending those images are missing.
    """
    cache = get_thumbnail_cache()
    chains = [image_candidates(v.icon_url, v.image_url) for v in villagers.itertuples()]
    still_loading = cache.prefetch(chains, budget=PAGE_IMAGE_BUDGET)

    # Cards in a row share its height even when one card's text wraps. Streamlit has
    # no stretch alignment for horizontal containers, so this one rule targets the
    # grid through the stable class its key produces.
    st.html(f"<style>.st-key-{GRID_KEY} {{ align-items: stretch; }}</style>")
    with st.container(horizontal=True, gap="xsmall", key=GRID_KEY):
        for (_, villager), chain in zip(villagers.iterrows(), chains):
            villager_card(villager, cache.resolve(chain).image)

    if still_loading:
        left, right = st.columns([4, 1], vertical_alignment="center")
        left.caption(f"{still_loading} image(s) are still loading from Nookipedia.")
        right.button("Refresh images", on_click=on_retry, width="stretch")
