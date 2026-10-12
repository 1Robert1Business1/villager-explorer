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

import base64
import html
import re
import time

import pandas as pd
import streamlit as st

from src import compare
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
# Set by a click inside the grid fragment (Details, Compare) whose effect lives
# outside it: the dialog, the Compare count in the navigation.
_FULL_RERUN = "grid_needs_full_rerun"
COMPARE_KEY = "compare_keys"  # selected villager keys, shared by every view
COMPARE_NOTICE = "compare_notice"  # a refusal message to show once (e.g. Compare is full)
_POLLING = "grid_polling"
_POLL_PAGE = "grid_poll_page"
_POLL_STARTED = "grid_poll_started"
# True while the grid fragment is executing as part of a full run (not its own rerun).
_IN_FULL_RUN = "grid_in_full_run"

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

# Narrow comparison layout (phones, or a main column squeezed by the sidebar): no
# sideways scrolling. Villagers sit two to a row in the header, and each attribute
# becomes a block whose values are labelled with the villager's name. It switches
# on the width the table actually gets (a container query), at the point where
# its columns would drop below a readable width for that many villagers.
CMP_LABEL_PX, CMP_COLUMN_PX = 136, 140


def _compare_narrow_css() -> str:
    rules = []
    for n in range(1, compare.MAX_COMPARE + 1):
        p = f".vx-n{n}"
        rules.append(
            f"@container (max-width: {CMP_LABEL_PX + n * CMP_COLUMN_PX}px) {{\n"
            f"  {p} .vx-cmp, {p} .vx-cmp thead, {p} .vx-cmp tbody, {p} .vx-cmp tr {{ display: block; width: 100%; }}\n"
            f"  {p} .vx-cmp thead tr {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }}\n"
            f"  {p} .vx-cmp thead th {{ display: block; width: auto; border-bottom: none; }}\n"
            f"  {p} .vx-cmp thead th.vx-cmp-corner {{ display: none; }}\n"
            f"  {p} .vx-cmp tbody tr {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));"
            " border-bottom: 1px solid rgba(128,128,128,0.25); }\n"
            f"  {p} .vx-cmp tbody th {{ grid-column: 1 / -1; width: auto; border-bottom: none; padding-bottom: 0.1rem; }}\n"
            f"  {p} .vx-cmp td {{ display: block; text-align: left; border-bottom: none; }}\n"
            f"  {p} .vx-cmp td::before {{ content: attr(data-name); display: block; font-size: 0.72rem; opacity: 0.6; }}\n"
            f"  {p} .vx-cmp-img {{ width: 72px; height: 72px; }}\n"
            "}"
        )
    return "\n".join(rules)


_COMPARE_NARROW_CSS = _compare_narrow_css()

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
/* The layout switches on the width the table actually gets (container query), not
   the screen width: an open sidebar can squeeze four columns on a wide screen. */
.vx-cmp-wrap {{ container-type: inline-size; width: 100%; }}
.vx-cmp thead .vx-chip {{ white-space: nowrap; overflow-wrap: normal; }}
.vx-cmp {{ width: 100%; border-collapse: separate; border-spacing: 0; table-layout: fixed; font-size: 0.9rem; }}
.vx-cmp th, .vx-cmp td {{ padding: 0.45rem 0.5rem; text-align: center; vertical-align: middle;
                         border-bottom: 1px solid rgba(128,128,128,0.25); overflow-wrap: anywhere; }}
.vx-cmp thead th {{ font-weight: 400; border-bottom: 2px solid rgba(128,128,128,0.35); }}
.vx-cmp tbody th {{ text-align: left; font-weight: 600; }}
.vx-cmp-corner, .vx-cmp tbody th {{ width: 8.5rem; }}
.vx-cmp-img {{ width: 96px; height: 96px; display: block; margin: 0 auto 0.2rem; }}
.vx-cmp-badge {{ font-size: 0.72rem; opacity: 0.7; }}
.vx-cmp tr.vx-same > * {{ background: rgba(70, 170, 110, 0.14); }}
.vx-same-tag {{ display: inline-block; margin-left: 0.35rem; padding: 0 0.45rem; border-radius: 999px;
               font-size: 0.7rem; font-weight: 600; background: rgba(70, 170, 110, 0.32); }}
.vx-unknown {{ opacity: 0.55; font-style: italic; }}
.vx-chip-sm {{ display: inline-flex; align-items: center; gap: 0.25rem; margin: 0.1rem 0.15rem;
              padding: 0.05rem 0.45rem; border-radius: 999px; font-size: 0.8rem;
              border: 1px solid rgba(128,128,128,0.35); }}
.vx-chip-sm.vx-common {{ font-weight: 600; border-color: rgba(70, 170, 110, 0.9); background: rgba(70, 170, 110, 0.18); }}
{_COMPARE_NARROW_CSS}
.vx-swatch {{ display: inline-flex; align-items: center; gap: 0.35rem; margin-right: 0.9rem; }}
.vx-dot {{ width: 0.9rem; height: 0.9rem; border-radius: 50%; display: inline-block;
          border: 1px solid rgba(128,128,128,0.6); }}
"""


def inject_css() -> None:
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
    st.session_state[_FULL_RERUN] = True


def compare_selection() -> tuple[str, ...]:
    return tuple(st.session_state.get(COMPARE_KEY, ()))


def set_compare_selection(keys) -> None:
    st.session_state[COMPARE_KEY] = list(keys)


def compare_shortcut() -> None:
    """In-page link to Compare with the selection count.

    The navigation also shows the count, but on phones Streamlit folds the
    navigation into the collapsed sidebar, so this keeps it visible at any width.
    """
    count = len(compare_selection())
    if count:
        st.page_link(
            "views/compare.py",
            label=f"Compare ({count} selected)",
            icon=":material/compare_arrows:",
        )


def _toggle_compare(key: str) -> None:
    change = compare.toggle(compare_selection(), key, valid={key})  # key comes from the dataset
    set_compare_selection(change.keys)
    if change.message:
        st.session_state[COMPARE_NOTICE] = change.message
    st.session_state[_FULL_RERUN] = True  # so the Compare count in the navigation updates


def villager_card(
    key: str,
    villager: pd.Series,
    image: bytes | None,
    loading: bool,
    note_html: str | None = None,
    selected: tuple[str, ...] = (),
) -> None:
    """One villager: image (placeholder while loading or if unavailable), name, key facts.

    `note_html` is an optional extra line (Match's "why it matched"). It must be
    built from escaped text, e.g. by match_note_html. `selected` is the current
    Compare selection, for the card's add/remove control.
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
        comparing = key in selected
        full = len(selected) >= compare.MAX_COMPARE and not comparing
        with st.container(horizontal=True, horizontal_alignment="center", gap="xxsmall"):
            st.button(
                "Details",
                key=f"details-{key}",
                type="tertiary",
                icon=":material/info:",
                on_click=_request_details,
                args=(key,),
            )
            st.button(
                "Comparing" if comparing else "Compare",
                key=f"compare-{key}",
                type="tertiary",
                icon=":material/check_circle:" if comparing else ":material/add_circle:",
                on_click=_toggle_compare,
                args=(key,),
                disabled=full,
                help=(
                    "Remove from Compare" if comparing
                    else compare.FULL_MESSAGE if full
                    else "Add to Compare"
                ),
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

    inject_css()
    selected = compare_selection()
    with st.container(horizontal=True, gap="xsmall", key=GRID_KEY):
        pending = 0
        for (key, villager), chain in zip(villagers.iterrows(), chains):
            resolved = cache.resolve(chain)
            pending += resolved.state == "pending"
            villager_card(
                key, villager, resolved.image, loading=resolved.state == "pending",
                note_html=notes.get(key), selected=selected,
            )

    if st.session_state.get(_FULL_RERUN):
        st.session_state[_FULL_RERUN] = False
        st.rerun()  # the dialog / Compare count live outside the fragment (see module docstring)

    # Never escalate from inside a full run: if images arrive while the page's
    # first run is still executing, an st.rerun() here would cut that run short
    # and immediately start another. The fragment's next poll (POLL_SECONDS later,
    # after the full run has finished) switches polling off instead.
    if st.session_state.get(_POLLING) and not st.session_state.get(_IN_FULL_RUN):
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
    st.session_state[_FULL_RERUN] = False  # this run is already a full run
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
    st.session_state[_IN_FULL_RUN] = True
    try:
        grid(villagers, notes)
    finally:
        st.session_state[_IN_FULL_RUN] = False

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
    inject_css()
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


# ------------------------------------------------------------------ compare


def _data_uri(png: bytes) -> str:
    """Our own re-encoded PNG thumbnails, inlined so the whole table is one st.html block."""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


_SHARED_SR = '<span class="vx-sr"> (shared by all)</span>'


def _compare_value(row: compare.CompareRow, value) -> str:
    if row.multi:
        if not value:
            return f'<span class="vx-unknown">{UNKNOWN}</span>'
        chips = []
        for v in value:
            shared = v in row.common
            dot = (
                f'<span class="vx-dot" style="background:{COLOUR_SWATCHES.get(v, "#cccccc")}"></span>'
                if row.label == "Colours" else ""
            )
            mark = "✓ " if shared else ""
            chips.append(
                f'<span class="vx-chip-sm{" vx-common" if shared else ""}">{dot}{mark}{html.escape(v)}'
                f'{_SHARED_SR if shared else ""}</span>'
            )
        return "".join(chips)
    if value is None:
        return f'<span class="vx-unknown">{UNKNOWN}</span>'
    text = html.escape(value)
    return f"“{text}”" if row.label == "Catchphrase" else text


def compare_table_html(
    selected: pd.DataFrame,
    rows: list[compare.CompareRow],
    images: dict[str, bytes],
    labels: dict[str, str] | None = None,
) -> str:
    """Villagers as columns, attributes as aligned rows; shared rows and values marked.

    `labels` are the disambiguated picker labels (compare.picker_labels): where
    two villagers share a name, every place a name identifies a column uses the
    label ("Carmen (Mouse)") and the header shows the species under the chip.
    Every dataset value is escaped; labels also appear in data-name attributes
    (escaped with quote=True) for the narrow layout's per-value labels.
    """
    labels = labels or {}
    names = [html.escape(labels.get(key, str(v["name"])), quote=True) for key, v in selected.iterrows()]
    head = []
    for (key, v), name in zip(selected.iterrows(), names):
        collides = labels.get(key, v["name"]) != v["name"]
        species = f'<div class="vx-cmp-badge">{html.escape(str(v["species"]))}</div>' if collides else ""
        badge = "" if v["in_nh"] else '<div class="vx-cmp-badge">Not in New Horizons</div>'
        head.append(
            f'<th scope="col"><img class="vx-cmp-img" src="{_data_uri(images[key])}" alt="{name}">'
            f'{chip_html(v["name"], v["title_color"])}{species}{badge}</th>'
        )
    body = []
    for row in rows:
        tag = '<span class="vx-same-tag">Same</span>' if row.same else ""
        cells = "".join(
            f'<td data-name="{name}">{_compare_value(row, value)}</td>' for name, value in zip(names, row.values)
        )
        row_class = ' class="vx-same"' if row.same else ""
        body.append(f'<tr{row_class}><th scope="row">{row.label}{tag}</th>{cells}</tr>')
    return (
        f'<div class="vx-cmp-wrap vx-n{len(selected)}"><table class="vx-cmp"><thead><tr><th class="vx-cmp-corner"></th>'
        f'{"".join(head)}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
    )


def compare_images(selected: pd.DataFrame, budget: float = 3.0) -> dict[str, bytes]:
    """Thumbnail bytes for each selected villager (placeholder if unavailable)."""
    cache = get_thumbnail_cache()
    chains = {key: image_candidates(v.icon_url, v.image_url) for key, v in zip(selected.index, selected.itertuples())}
    cache.prefetch(list(chains.values()), budget=budget)
    return {key: cache.resolve(chain).image or placeholder_bytes() for key, chain in chains.items()}
