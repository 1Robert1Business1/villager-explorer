"""Reusable Streamlit UI pieces: the villager card, the card grid and the details dialog.

Images come from src.images as already-validated bytes or a placeholder, so
nothing here can render a broken image.

Dataset text never goes through markdown
----------------------------------------
Free text from the dataset (names, catchphrases, quotes, songs, outfits) is
rendered only with st.html after html.escape. st.html does not interpret
markdown, so Streamlit shortcodes (:material/...:), links, images, LaTeX and
auto-linked URLs can't come from data, whatever syntax Streamlit adds later.
This was verified by rendering hostile strings in a real 1.64 page. The few
places Streamlit renders markdown from data (pills options) only receive
category values, which src/data.py allowlists to plain words at load time.

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

# One stylesheet for every st.html block. Classes, not data-driven inline styles;
# the only per-villager style values are validated hex colours (see _safe_colour).
# Secondary text uses opacity on the inherited colour so it suits light and dark themes.
_CSS = f"""
.st-key-{GRID_KEY} {{ align-items: stretch; }}
.vx-chip-row {{ text-align: center; line-height: 1.9; margin: 0.1rem 0 0.45rem; }}
.vx-chip {{ padding: 0.15rem 0.7rem; border-radius: 999px; font-weight: 600; display: inline-block; }}
.vx-meta {{ text-align: center; font-size: 0.875rem; line-height: 1.5; opacity: 0.72; }}
.vx-icon {{ font-family: 'Material Symbols Rounded'; font-weight: 400; font-style: normal;
           font-size: 1.05em; line-height: 1; vertical-align: -0.18em; letter-spacing: normal;
           text-transform: none; white-space: nowrap; font-feature-settings: 'liga';
           margin-right: 0.2em; }}
.vx-sr {{ position: absolute; width: 1px; height: 1px; overflow: hidden;
         clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }}
.vx-facts {{ display: grid; grid-template-columns: max-content 1fr; gap: 0.35rem 0.8rem; margin: 0; }}
.vx-facts dt {{ font-weight: 600; }}
.vx-facts dd {{ margin: 0; }}
.vx-quote {{ border-left: 3px solid rgba(128,128,128,0.45); padding: 0.1rem 0 0.1rem 0.8rem;
            font-style: italic; opacity: 0.8; margin: 0.4rem 0; }}
.vx-match {{ text-align: center; font-size: 0.8rem; line-height: 1.35; margin-top: 0.15rem; }}
.vx-miss {{ text-align: center; font-size: 0.75rem; line-height: 1.3; opacity: 0.6; }}
.vx-swatch {{ display: inline-flex; align-items: center; gap: 0.35rem; margin-right: 0.9rem; }}
.vx-dot {{ width: 0.9rem; height: 0.9rem; border-radius: 50%; display: inline-block;
          border: 1px solid rgba(128,128,128,0.6); }}
"""


def _inject_css() -> None:
    """Style-only st.html goes to the event container: takes no space, applies page-wide."""
    st.html(f"<style>{_CSS}</style>")


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


# --------------------------------------------------------------- HTML pieces
# Pure functions returning HTML strings, so escaping is unit-testable without Streamlit.


def text(value: object) -> str:
    """Dataset value as escaped HTML text, or Unknown."""
    return html.escape(str(value)) if isinstance(value, str) and value else UNKNOWN


def chip_html(name: str, background: object, size: str = "0.95rem") -> str:
    """The villager's name on their wiki colour, with text picked for legibility."""
    bg = _safe_colour(background, DEFAULT_CHIP_BG)
    fg = readable_text_colour(bg)
    return (
        f'<div class="vx-chip-row"><span class="vx-chip" '
        f'style="background:{bg};color:{fg};font-size:{size}">{html.escape(name)}</span></div>'
    )


def _icon(name: str, label: str) -> str:
    """A decorative Material icon; screen readers get `label` instead of the icon's name."""
    return f'<span class="vx-icon" aria-hidden="true">{name}</span><span class="vx-sr">{label} </span>'


def _short_birthday(villager: pd.Series) -> str:
    month, day = villager["birthday_month"], villager["birthday_day"]
    if pd.isna(month) or pd.isna(day):
        return UNKNOWN
    return html.escape(f"{str(month)[:3]} {day}")


def card_meta_html(villager: pd.Series) -> str:
    return (
        '<div class="vx-meta">'
        f"{text(villager['species'])} · {text(villager['personality'])}<br>"
        f"{_icon('interests', 'Hobby:')}{text(villager['hobby'])}&nbsp;&nbsp;"
        f"{_icon('cake', 'Birthday:')}{_short_birthday(villager)}"
        "</div>"
    )


def facts_html(rows: list[tuple[str, str]]) -> str:
    """A definition list. Labels are fixed strings; values must already be escaped HTML."""
    items = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows)
    return f'<dl class="vx-facts">{items}</dl>'


def swatches_html(colours: tuple[str, ...]) -> str:
    return "".join(
        f'<span class="vx-swatch"><span class="vx-dot" '
        f'style="background:{COLOUR_SWATCHES.get(name, "#cccccc")}"></span>{html.escape(name)}</span>'
        for name in colours
    )


def _game(code: object) -> str:
    return html.escape(GAME_NAMES.get(code, code)) if isinstance(code, str) else UNKNOWN


# How a matched/missed answer reads on a card. Species and personality read on
# their own ("Cat", "Peppy"); the rest need a word of context ("Fashion hobby").
_ANSWER_SUFFIX = {"hobby": " hobby", "styles": " style", "colors": ""}


def _answer(facet: str, value: str) -> str:
    return html.escape(f"{value}{_ANSWER_SUFFIX.get(facet, '')}")


def match_note_html(matched: tuple, missed: tuple, score: int, total: int) -> str:
    """Why a villager is in the results: e.g. "Matches 3 of 4: Cat, Peppy, Fashion hobby"."""
    headline = f"Matches all {total}" if score == total and total > 1 else (
        "Matches" if total == 1 else f"Matches {score} of {total}"
    )
    parts = [
        f'<div class="vx-match"><strong>{headline}</strong>: '
        f"{', '.join(_answer(f, v) for f, v in matched)}</div>"
    ]
    if missed:
        parts.append(f'<div class="vx-miss">Not: {", ".join(_answer(f, v) for f, v in missed)}</div>')
    return "".join(parts)


# --------------------------------------------------------------------- card


def _request_details(key: str) -> None:
    st.session_state[DETAIL_KEY] = key
    st.session_state[_DETAIL_REQUESTED] = True


def villager_card(
    key: str, villager: pd.Series, image: bytes | None, loading: bool, note_html: str | None = None
) -> None:
    """One villager: image (placeholder while loading or if unavailable), name, key facts.

    `note_html` is an optional extra line (Match's "why it matched"). It must be
    built from escaped text, e.g. by match_note_html.
    """
    with st.container(border=True, width=CARD_WIDTH, horizontal_alignment="center", gap="xsmall"):
        # Size via the container, not st.image(width=...): an int width makes
        # Streamlit downscale the 2x thumbnail server-side, losing HiDPI sharpness.
        with st.container(width=IMAGE_WIDTH):
            fallback = loading_placeholder_bytes() if loading else placeholder_bytes()
            st.image(image if image is not None else fallback, width="stretch")
        st.html(chip_html(villager["name"], villager["title_color"]))
        st.html(card_meta_html(villager))
        if note_html:
            st.html(note_html)
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


def _render_grid(villagers: pd.DataFrame, notes: dict[str, str] | None = None) -> None:
    """Fragment body: draw every card from whatever is cached right now. Never blocks."""
    notes = notes or {}
    cache = get_thumbnail_cache()
    chains = _chains(villagers)
    cache.prefetch(chains, budget=0)  # schedule next candidates (e.g. art after a dead icon)

    _inject_css()
    with st.container(horizontal=True, gap="xsmall", key=GRID_KEY):
        pending = 0
        for (key, villager), chain in zip(villagers.iterrows(), chains):
            resolved = cache.resolve(chain)
            pending += resolved.state == "pending"
            villager_card(
                key, villager, resolved.image, loading=resolved.state == "pending", note_html=notes.get(key)
            )

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


def villager_grid(
    villagers: pd.DataFrame,
    upcoming: pd.DataFrame | None = None,
    notes: dict[str, str] | None = None,
) -> None:
    """Render cards for `villagers` at once; images fill in as they arrive.

    `upcoming` (the next page, if any) is fetched in the background so that
    "Show more" is instant. `notes` maps villager key to an extra escaped HTML
    line for that card.
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
    grid(villagers, notes)

    if pending and overdue:
        left, right = st.columns([4, 1], vertical_alignment="center")
        left.caption(f"{pending} image(s) are taking a while to arrive from Nookipedia.")
        if right.button("Retry", width="stretch"):
            st.session_state[_POLL_STARTED] = time.monotonic()


# ------------------------------------------------------------------ details


def _close_details() -> None:
    st.session_state[DETAIL_KEY] = None


def _nookipedia_url(url: object) -> str | None:
    """Only link to Nookipedia wiki pages; never pass arbitrary dataset URLs to the browser."""
    if isinstance(url, str) and url.startswith("https://nookipedia.com/wiki/"):
        return url
    return None


def details_profile_html(villager: pd.Series) -> str:
    sub = villager["sub_personality"]
    personality = text(villager["personality"]) + (
        f" (sub-type {html.escape(sub)})" if isinstance(sub, str) else ""
    )
    rows = [
        ("Species", text(villager["species"])),
        ("Personality", personality),
        ("Gender", text(villager["gender"])),
    ]
    sign = villager["sign"]
    if isinstance(villager["birthday"], str):
        rows.append(("Birthday", text(villager["birthday"]) + (f" ({html.escape(sign)})" if isinstance(sign, str) else "")))
    else:
        # Some early-game villagers have a star sign on record but no birthday.
        rows.append(("Birthday", UNKNOWN))
        if isinstance(sign, str):
            rows.append(("Star sign", html.escape(sign)))
    rows.append(("Hobby", text(villager["hobby"])))
    catchphrase = villager["catchphrase"]
    rows.append(("Catchphrase", f"“{html.escape(catchphrase)}”" if isinstance(catchphrase, str) else UNKNOWN))
    return facts_html(rows)


def details_favourites_html(villager: pd.Series) -> str:
    styles, colours = villager["styles"], villager["colors"]
    return facts_html([
        ("Song", text(villager["favorite_song"])),
        ("Styles", ", ".join(html.escape(s) for s in styles) if styles else UNKNOWN),
        ("Colours", swatches_html(colours) if colours else UNKNOWN),
    ])


def details_appearances_html(villager: pd.Series) -> str:
    games = villager["games"]
    rows = [
        ("Debut", _game(villager["debut"])),
        ("Appears in", ", ".join(_game(g) for g in games) if games else UNKNOWN),
    ]
    if isinstance(villager["clothing"], str):
        rows.append(("Default outfit", html.escape(villager["clothing"])))
    return facts_html(rows)


def _details_body(villager: pd.Series) -> None:
    _inject_css()
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
            st.html(chip_html(villager["name"], villager["title_color"], "1.1rem"))
    with right:
        st.html(details_profile_html(villager))
        if villager["in_nh"]:
            st.caption("Catchphrases are the New Horizons ones; a few differ in earlier games.")

    if isinstance(villager["quote"], str):
        st.html(f'<blockquote class="vx-quote">{html.escape(villager["quote"])}</blockquote>')

    st.markdown("##### Favourites")
    st.html(details_favourites_html(villager))
    if not villager["in_nh"]:
        st.caption(
            "This villager isn't in New Horizons, so Nookipedia has no hobby, "
            "favourites or song for them."
        )

    st.markdown("##### Appearances")
    st.html(details_appearances_html(villager))

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
    # Fixed title: st.dialog titles render markdown, so dataset text stays out of it.
    # The villager's name appears in the body, rendered through st.html.
    dialog = st.dialog("Villager details", width="medium", on_dismiss=_close_details)
    dialog(_details_body)(villagers.loc[key])
