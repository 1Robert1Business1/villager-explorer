"""Reusable Streamlit UI pieces: the villager card, the card grid and the details dialog.

Images come from src.images as already-validated bytes or a placeholder, so
nothing here can render a broken image. Text that comes from the dataset is
escaped before it reaches markdown or HTML.

Progressive grid
----------------
The grid draws immediately. Cards whose image isn't cached yet show a faded
"loading" placeholder, and the grid (a fragment) re-renders itself every
POLL_SECONDS while fetches finish in the background. When nothing is pending
(or MAX_POLL_SECONDS passes) one full rerun switches polling off, because
st.fragment's run_every is fixed when the fragment is defined on a full run.

Details dialog
--------------
A card's Details button is inside the fragment, so clicking it only reruns
the fragment. The click records which villager to show, and the fragment
escalates to one full rerun, where browse.py opens the dialog. The dialog's
on_dismiss clears the selection, so it stays open across reruns until closed.
"""

from __future__ import annotations

import html
import re
import time

import pandas as pd
import streamlit as st

from src.filters import UNKNOWN
from src.images import (
    get_thumbnail_cache,
    image_candidates,
    loading_placeholder_bytes,
    placeholder_bytes,
)

PAGE_SIZE = 36
CARD_WIDTH = 164  # px; two fit side by side on a 375 px phone, and they wrap to any width
IMAGE_WIDTH = 112
DETAIL_IMAGE_WIDTH = 200
GRID_KEY = "villager-grid"

INITIAL_IMAGE_BUDGET = 0.6  # s to wait before first paint; a warm cache resolves instantly
POLL_SECONDS = 0.75  # how often the grid re-renders while images are arriving
MAX_POLL_SECONDS = 20  # stop polling after this; leftovers get a refresh note
DETAIL_IMAGE_BUDGET = 6.0  # s the dialog waits for the villager's full artwork

DETAIL_KEY = "details_villager"  # which villager the dialog shows (None = closed)
_DETAIL_REQUESTED = "details_requested"  # set by a click inside the fragment
_POLLING = "grid_polling"
_POLL_PAGE = "grid_poll_page"
_POLL_STARTED = "grid_poll_started"

# Name-chip background for villagers without wiki colours (the 73 non-NH villagers).
DEFAULT_CHIP_BG = "#e8eee4"
TEXT_ON_LIGHT, TEXT_ON_DARK = "#000000", "#ffffff"

GAME_NAMES = {
    "DnM": "Dōbutsu no Mori (N64)",
    "DnM+": "Dōbutsu no Mori+",
    "AC": "Animal Crossing (GameCube)",
    "e+": "Dōbutsu no Mori e+",
    "DnMe+": "Dōbutsu no Mori e+",
    "WW": "Wild World",
    "CF": "City Folk",
    "NL": "New Leaf",
    "WA": "New Leaf: Welcome amiibo",
    "NLWa": "New Leaf: Welcome amiibo",
    "NH": "New Horizons",
    "PC": "Pocket Camp",
    "HHD": "Happy Home Designer",
    "Film": "the 2006 film",
}

# Approximate swatches for the game's favourite-colour names (fixed values, never data).
COLOUR_SWATCHES = {
    "Aqua": "#3fc8cf", "Beige": "#e3d2ac", "Black": "#2b2b2b", "Blue": "#3069d0",
    "Brown": "#8a5a2b", "Gray": "#9a9a9a", "Green": "#3a9d4a", "Orange": "#f08a24",
    "Pink": "#f3a0c2", "Purple": "#8a58cc", "Red": "#d64545", "White": "#f4f4f4",
    "Yellow": "#f2cf2e",
    "Colorful": "conic-gradient(#d64545, #f2cf2e, #3a9d4a, #3069d0, #8a58cc, #d64545)",
}

# ------------------------------------------------------------------- colour


_HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")


def _safe_colour(value: object, default: str) -> str:
    """Only strict #rrggbb reaches a style attribute; anything else could inject CSS/HTML."""
    return value if isinstance(value, str) and _HEX_COLOUR.fullmatch(value) else default


def relative_luminance(hex_colour: str) -> float:
    """WCAG 2 relative luminance of a #rrggbb colour."""
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    hi, lo = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readable_text_colour(background: str) -> str:
    """Black or white, whichever contrasts more with `background`.

    One of the two always reaches at least 4.58:1, which clears WCAG AA (4.5:1)
    for every possible background. The wiki's own text colours fail AA on 340
    of 417 villagers.
    """
    if contrast_ratio(background, TEXT_ON_LIGHT) >= contrast_ratio(background, TEXT_ON_DARK):
        return TEXT_ON_LIGHT
    return TEXT_ON_DARK


def _chip(name: str, background: object, size: str = "0.95rem") -> str:
    """The villager's name on their wiki colour, with text picked for legibility."""
    bg = _safe_colour(background, DEFAULT_CHIP_BG)
    fg = readable_text_colour(bg)
    return (
        f'<div style="text-align:center;line-height:1.9;margin:0.1rem 0 0.45rem">'
        f'<span style="background:{bg};color:{fg};'
        f"padding:0.15rem 0.7rem;border-radius:999px;font-weight:600;font-size:{size};"
        f'display:inline-block">{html.escape(name)}</span></div>'
    )


# --------------------------------------------------------------------- text


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+\-.!|~$])")
_ZWSP = "​"


def escape_markdown(text: object) -> str:
    """Make dataset text inert in st.markdown/st.caption.

    Backslash escapes stop links, images, emphasis and LaTeX. They don't stop
    two things, verified by rendering: Streamlit's :material/...: shortcodes
    (expanded after markdown parsing) and GFM auto-links of bare URLs, www.
    hosts and emails. An invisible zero-width space after ":" and "@" and inside
    "www" breaks both without changing what the text looks like. (A space after
    "www." was not enough; the www. auto-link still fired.)
    """
    escaped = _MARKDOWN_SPECIAL.sub(r"\\\1", str(text))
    escaped = escaped.replace(":", ":" + _ZWSP).replace("@", "@" + _ZWSP)
    return re.sub(r"(?i)w(?=ww)", lambda m: m.group(0) + _ZWSP, escaped)


def _value(v: object) -> str:
    return escape_markdown(v) if isinstance(v, str) and v else UNKNOWN


def _short_birthday(villager: pd.Series) -> str:
    month, day = villager["birthday_month"], villager["birthday_day"]
    if pd.isna(month) or pd.isna(day):
        return UNKNOWN
    return f"{str(month)[:3]} {day}"


# --------------------------------------------------------------------- card


def _request_details(key: str) -> None:
    st.session_state[DETAIL_KEY] = key
    st.session_state[_DETAIL_REQUESTED] = True


def villager_card(key: str, villager: pd.Series, image: bytes | None, loading: bool) -> None:
    """One villager: image (placeholder while loading or if unavailable), name, key facts."""
    with st.container(border=True, width=CARD_WIDTH, horizontal_alignment="center", gap="xsmall"):
        # Size via the container, not st.image(width=...): an int width makes
        # Streamlit downscale the 2x thumbnail server-side, losing HiDPI sharpness.
        with st.container(width=IMAGE_WIDTH):
            fallback = loading_placeholder_bytes() if loading else placeholder_bytes()
            st.image(image if image is not None else fallback, width="stretch")
        st.markdown(_chip(villager["name"], villager["title_color"]), unsafe_allow_html=True)
        st.caption(
            f"{_value(villager['species'])} · {_value(villager['personality'])}  \n"
            f":material/interests: {_value(villager['hobby'])}"
            f"&nbsp;&nbsp;:material/cake: {_short_birthday(villager)}",
            text_alignment="center",
        )
        if not villager["in_nh"]:
            st.badge("Not in New Horizons", color="gray")
        st.button(
            "Details",
            key=f"details-{key}",
            type="tertiary",
            icon=":material/info:",
            on_click=_request_details,
            args=(key,),
        )


# --------------------------------------------------------------------- grid


def _chains(villagers: pd.DataFrame) -> list[list[str]]:
    return [image_candidates(v.icon_url, v.image_url) for v in villagers.itertuples()]


def _render_grid(villagers: pd.DataFrame) -> None:
    """Fragment body: draw every card from whatever is cached right now. Never blocks."""
    cache = get_thumbnail_cache()
    chains = _chains(villagers)
    cache.prefetch(chains, budget=0)  # schedule next candidates (e.g. art after a dead icon)

    st.html(f"<style>.st-key-{GRID_KEY} {{ align-items: stretch; }}</style>")
    with st.container(horizontal=True, gap="xsmall", key=GRID_KEY):
        pending = 0
        for (key, villager), chain in zip(villagers.iterrows(), chains):
            resolved = cache.resolve(chain)
            pending += resolved.state == "pending"
            villager_card(key, villager, resolved.image, loading=resolved.state == "pending")

    if st.session_state.get(_DETAIL_REQUESTED):
        st.session_state[_DETAIL_REQUESTED] = False
        st.rerun()  # the dialog opens on a full run (see module docstring)

    if st.session_state.get(_POLLING):
        overdue = time.monotonic() - st.session_state.get(_POLL_STARTED, 0) > MAX_POLL_SECONDS
        if not pending or overdue:
            st.session_state[_POLLING] = False
            st.rerun()  # a full run redefines the fragment without run_every


def warm_images(villagers: pd.DataFrame) -> None:
    """Start fetching these villagers' images in the background; returns immediately."""
    get_thumbnail_cache().prefetch(_chains(villagers), budget=0)


def villager_grid(villagers: pd.DataFrame, upcoming: pd.DataFrame | None = None) -> None:
    """Render cards for `villagers` at once; images fill in as they arrive.

    `upcoming` (the next page, if any) is fetched in the background so that
    "Show more" is instant.
    """
    st.session_state[_DETAIL_REQUESTED] = False  # this full run will open any dialog itself
    cache = get_thumbnail_cache()
    chains = _chains(villagers)
    cache.prefetch(chains, budget=INITIAL_IMAGE_BUDGET)
    if upcoming is not None and not upcoming.empty:
        cache.prefetch(_chains(upcoming), budget=0)

    page = tuple(villagers.index)
    if st.session_state.get(_POLL_PAGE) != page:
        st.session_state[_POLL_PAGE] = page
        st.session_state[_POLL_STARTED] = time.monotonic()
    pending = sum(cache.resolve(c).state == "pending" for c in chains)
    overdue = time.monotonic() - st.session_state[_POLL_STARTED] > MAX_POLL_SECONDS
    polling = bool(pending) and not overdue
    st.session_state[_POLLING] = polling

    grid = st.fragment(_render_grid, run_every=POLL_SECONDS if polling else None)
    grid(villagers)

    if pending and overdue:
        left, right = st.columns([4, 1], vertical_alignment="center")
        left.caption(f"{pending} image(s) are taking a while to arrive from Nookipedia.")
        if right.button("Retry", width="stretch"):
            st.session_state[_POLL_STARTED] = time.monotonic()


# ------------------------------------------------------------------ details


def _close_details() -> None:
    st.session_state[DETAIL_KEY] = None


def _swatches(colours: tuple[str, ...]) -> str:
    items = []
    for name in colours:
        fill = COLOUR_SWATCHES.get(name, "#cccccc")
        items.append(
            '<span style="display:inline-flex;align-items:center;gap:0.35rem;margin-right:0.9rem">'
            f'<span style="width:0.9rem;height:0.9rem;border-radius:50%;background:{fill};'
            'border:1px solid rgba(128,128,128,0.6);display:inline-block"></span>'
            f"{html.escape(name)}</span>"
        )
    return "".join(items)


def _fact(label: str, value: str) -> None:
    st.markdown(f"**{label}:** {value}")


def _nookipedia_url(url: object) -> str | None:
    """Only link to Nookipedia wiki pages; never pass arbitrary dataset URLs to the browser."""
    if isinstance(url, str) and url.startswith("https://nookipedia.com/wiki/"):
        return url
    return None


def _details_body(villager: pd.Series) -> None:
    cache = get_thumbnail_cache()
    chain = image_candidates(villager["image_url"], villager["icon_url"])  # full art first
    with st.spinner("Loading artwork…"):
        cache.prefetch([chain], budget=DETAIL_IMAGE_BUDGET)
    resolved = cache.resolve(chain)

    left, right = st.columns([2, 3], gap="medium")
    with left:
        with st.container(horizontal_alignment="center"):
            with st.container(width=DETAIL_IMAGE_WIDTH):
                fallback = loading_placeholder_bytes() if resolved.state == "pending" else placeholder_bytes()
                st.image(resolved.image or fallback, width="stretch")
            st.markdown(_chip(villager["name"], villager["title_color"], "1.1rem"), unsafe_allow_html=True)
    with right:
        sub = villager["sub_personality"]
        personality = _value(villager["personality"]) + (f" (sub-type {sub})" if isinstance(sub, str) else "")
        _fact("Species", _value(villager["species"]))
        _fact("Personality", personality)
        _fact("Gender", _value(villager["gender"]))
        sign = villager["sign"]
        if isinstance(villager["birthday"], str):
            birthday = _value(villager["birthday"])
            _fact("Birthday", birthday + (f" ({escape_markdown(sign)})" if isinstance(sign, str) else ""))
        else:
            # Some early-game villagers have a star sign on record but no birthday.
            _fact("Birthday", UNKNOWN)
            if isinstance(sign, str):
                _fact("Star sign", escape_markdown(sign))
        _fact("Hobby", _value(villager["hobby"]))
        catchphrase = villager["catchphrase"]
        st.markdown(
            f"**Catchphrase:** “{escape_markdown(catchphrase)}”" if isinstance(catchphrase, str)
            else f"**Catchphrase:** {UNKNOWN}",
            help=(
                "The New Horizons catchphrase where the villager appears in New Horizons; "
                "a few differ from their catchphrase in earlier games."
            ) if villager["in_nh"] else None,
        )

    if isinstance(villager["quote"], str):
        st.markdown(f"> *{escape_markdown(villager['quote'])}*")

    st.markdown("##### Favourites")
    _fact("Song", _value(villager["favorite_song"]))
    styles = villager["styles"]
    _fact("Styles", ", ".join(escape_markdown(s) for s in styles) if styles else UNKNOWN)
    colours = villager["colors"]
    if colours:
        st.markdown(f"**Colours:** {_swatches(colours)}", unsafe_allow_html=True)
    else:
        _fact("Colours", UNKNOWN)
    if not villager["in_nh"]:
        st.caption(
            "This villager isn't in New Horizons, so Nookipedia has no hobby, "
            "favourites or song for them."
        )

    st.markdown("##### Appearances")
    debut = villager["debut"]
    _fact("Debut", escape_markdown(GAME_NAMES.get(debut, debut)) if isinstance(debut, str) else UNKNOWN)
    games = villager["games"]
    _fact("Appears in", ", ".join(escape_markdown(GAME_NAMES.get(g, g)) for g in games) if games else UNKNOWN)
    if isinstance(villager["clothing"], str):
        _fact("Default outfit", escape_markdown(villager["clothing"]))

    url = _nookipedia_url(villager["nookipedia_url"])
    if url:
        st.link_button("View on Nookipedia", url, icon=":material/open_in_new:")


def villager_details(villagers: pd.DataFrame) -> None:
    """Open the details dialog if a villager is selected. Call once per full run."""
    key = st.session_state.get(DETAIL_KEY)
    if key is None:
        return
    if key not in villagers.index:
        st.session_state[DETAIL_KEY] = None
        return
    villager = villagers.loc[key]
    dialog = st.dialog(villager["name"], width="medium", on_dismiss=_close_details)
    dialog(_details_body)(villager)
