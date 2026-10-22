"""Compare: pick 2-4 villagers and see their attributes side by side.

Selection is one list of villager keys in session state (components.COMPARE_KEY),
shared with the Compare buttons on Browse and Find Your Match cards, so it
survives moving between all three views. Picking is by unique key; colliding
names are labelled with their species (src.compare.picker_labels).

Share links: the page keeps ?v=<keys> in the address bar in step with the
selection, and a link opened fresh is resolved against the dataset. Unknown
tokens are only counted; URL text is never rendered.
"""

from __future__ import annotations

import streamlit as st

from src import compare
from src.components import (
    compare_images,
    compare_selection,
    compare_table_html,
    inject_css,
    set_compare_selection,
)
from src.data import load_villagers

PICKER = "compare_picker"
LAST_LINK = "compare_last_link"  # the ?v= value this session last wrote or applied
LINK_IGNORED = "compare_link_ignored"  # skipped-entry count, carried across the rerun below

EXAMPLES = {
    "Raymond & Marshal": ("Raymond", "Marshal"),
    "The two Carmens": ("Carmen", "Carmen_(mouse)"),
    "Ankha, Bob & Judy": ("Ankha", "Bob", "Judy"),
}


def _from_picker() -> None:
    set_compare_selection(compare.sanitize(st.session_state[PICKER], load_villagers().index))


def _choose(keys: tuple[str, ...]) -> None:
    set_compare_selection(compare.sanitize(keys, load_villagers().index))


def _apply_link(valid) -> None:
    """If the page was opened with a new ?v= link, adopt it.

    app.py builds the navigation (with its "Compare (n)" count) before this page
    runs, so a link that changes the selection triggers one rerun to bring the
    count up to date. LAST_LINK is recorded first, so the rerun doesn't re-apply.
    """
    raw = st.query_params.get_all("v")
    joined = ",".join(raw)
    if not joined or joined == st.session_state.get(LAST_LINK):
        return
    keys, ignored = compare.parse_share_param(raw, valid)
    before = compare_selection()
    set_compare_selection(keys)
    st.session_state[LAST_LINK] = joined
    st.session_state[LINK_IGNORED] = ignored
    if tuple(keys) != tuple(before):
        st.rerun()


def _write_link(keys: tuple[str, ...]) -> None:
    param = compare.share_param(keys)
    if param:
        st.query_params["v"] = param
    elif "v" in st.query_params:
        del st.query_params["v"]
    st.session_state[LAST_LINK] = param


def render() -> None:
    villagers = load_villagers()
    _apply_link(villagers.index)
    ignored = st.session_state.pop(LINK_IGNORED, 0)
    selected = compare.sanitize(compare_selection(), villagers.index)
    set_compare_selection(selected)
    labels = compare.picker_labels(villagers)

    st.title("Compare villagers")
    st.caption(
        f"Pick up to {compare.MAX_COMPARE} villagers here, or use **Compare** on any card in "
        "Browse or Find your match. Where two villagers share a name, the species tells them apart."
    )
    if ignored:
        st.info(
            f"{ignored} {'entry' if ignored == 1 else 'entries'} in the link didn't match a villager "
            f"(or went past {compare.MAX_COMPARE}) and {'was' if ignored == 1 else 'were'} skipped.",
            icon=":material/link_off:",
        )

    st.session_state[PICKER] = list(selected)
    st.multiselect(
        "Villagers to compare",
        options=list(villagers.index),
        format_func=lambda key: labels[key],
        key=PICKER,
        max_selections=compare.MAX_COMPARE,
        placeholder="Search by name…",
        on_change=_from_picker,
    )

    if not selected:
        st.info("Nothing to compare yet. Pick villagers above, or try one of these:", icon=":material/compare:")
        with st.container(horizontal=True):
            for label, keys in EXAMPLES.items():
                st.button(label, on_click=_choose, args=(keys,), key=f"example-{label}")
        _write_link(selected)
        return

    with st.container(horizontal=True, vertical_alignment="center"):
        st.button("Clear all", icon=":material/clear_all:", on_click=_choose, args=((),), key="compare_clear")
        if len(selected) >= 2:
            st.caption("This page's address links to this comparison, so you can share it.")

    rows = compare.comparison_rows(villagers.loc[list(selected)])
    inject_css()
    if len(selected) == 1:
        st.info("Add at least one more villager to compare.", icon=":material/person_add:")
    else:
        st.caption("Highlighted rows are the same for everyone; ✓ marks styles and colours they all share.")
    chosen = villagers.loc[list(selected)]
    st.html(compare_table_html(chosen, rows, compare_images(chosen), labels=labels))
    _write_link(selected)


render()
