"""Browse & Filter: the spine of the app. Filter the roster, see matching villagers as cards.

State: every filter widget has a stable key and persist_state="session", so
selections survive switching to Match/Compare and back. Defaults are seeded
into st.session_state once (widgets never also get a default=, which would
fight the session value), and stale selections are pruned before each widget
renders, so an option that disappears (Hobby "Unknown" when New Horizons only
is switched back on) can't crash the widget.
"""

from __future__ import annotations

import streamlit as st

from src.components import PAGE_SIZE, villager_details, villager_grid
from src.data import load_villagers
from src.filters import (
    FACETS,
    SORT_OPTIONS,
    Criteria,
    apply_criteria,
    base_pool,
    facet_counts,
    facet_options,
    sort_villagers,
)

SEARCH = "browse_search"
NH_ONLY = "browse_nh_only"
SORT = "browse_sort"
LIMIT = "browse_limit"
SIGNATURE = "browse_signature"


def facet_key(column: str) -> str:
    return f"browse_facet_{column}"


DEFAULTS: dict[str, object] = {
    SEARCH: "",
    NH_ONLY: True,
    SORT: SORT_OPTIONS[0],
    **{facet_key(c): [] for c in FACETS},
}

# How each facet is presented: pills for short option lists, a searchable
# multiselect (with counts) for long ones. "More filters" hides the less-used ones.
PILL_FACETS = {"personality", "gender", "hobby", "styles"}
PRIMARY_FACETS = ["species", "personality", "gender", "hobby"]
SECONDARY_FACETS = ["sign", "birthday_month", "styles", "colors"]


def _seed_state() -> None:
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, value.copy() if isinstance(value, list) else value)
    st.session_state.setdefault(LIMIT, PAGE_SIZE)


def _clear_filters() -> None:
    """Reset search and facets; keep the NH scope and sort order, which aren't filters."""
    st.session_state[SEARCH] = ""
    for column in FACETS:
        st.session_state[facet_key(column)] = []


def _include_all_games() -> None:
    st.session_state[NH_ONLY] = False


def _show_more() -> None:
    st.session_state[LIMIT] += PAGE_SIZE


def _facet_widget(pool, column: str) -> None:
    facet = FACETS[column]
    key = facet_key(column)
    options = facet_options(pool, column)
    st.session_state[key] = [v for v in st.session_state[key] if v in options]

    if column in PILL_FACETS:
        st.pills(facet.label, options, selection_mode="multi", key=key, persist_state="session")
    else:
        counts = facet_counts(pool, column)
        st.multiselect(
            facet.label,
            options,
            key=key,
            persist_state="session",
            format_func=lambda v: f"{v} ({counts.get(v, 0)})",
            placeholder=f"Any {facet.label.lower()}",
        )


def _sidebar(villagers) -> Criteria:
    with st.sidebar:
        st.text_input(
            "Search by name",
            key=SEARCH,
            type="search",
            live=True,
            placeholder="e.g. Marshal, Étoile",
            persist_state="session",
        )
        st.toggle(
            "New Horizons villagers only",
            key=NH_ONLY,
            persist_state="session",
            help=(
                "73 villagers appear only in earlier games. Nookipedia has no hobby, "
                "favourite styles/colours or song for them; switch this off to include "
                "them, with those fields shown as Unknown."
            ),
        )
        pool = base_pool(villagers, st.session_state[NH_ONLY])

        for column in PRIMARY_FACETS:
            _facet_widget(pool, column)
        secondary_active = any(st.session_state[facet_key(c)] for c in SECONDARY_FACETS)
        with st.expander("More filters", expanded=secondary_active):
            for column in SECONDARY_FACETS:
                _facet_widget(pool, column)

        criteria = Criteria(
            selections={c: st.session_state[facet_key(c)] for c in FACETS},
            name_query=st.session_state[SEARCH] or "",
            nh_only=st.session_state[NH_ONLY],
        )
        st.button(
            "Clear filters",
            icon=":material/filter_alt_off:",
            on_click=_clear_filters,
            disabled=criteria.is_unconstrained,
            width="stretch",
        )
    return criteria


def _empty_state(villagers, criteria: Criteria) -> None:
    with st.container(border=True, horizontal_alignment="center"):
        st.markdown("#### No villagers match these filters", text_alignment="center")
        outside_nh = 0
        if criteria.nh_only:
            wider = Criteria(criteria.selections, criteria.name_query, nh_only=False)
            outside_nh = len(apply_criteria(villagers, wider))
        with st.container(horizontal=True, horizontal_alignment="center"):
            if outside_nh:
                st.button(
                    f"Include {outside_nh} from earlier games",
                    icon=":material/history:",
                    on_click=_include_all_games,
                )
            st.button("Clear filters", icon=":material/filter_alt_off:", on_click=_clear_filters)
        st.caption("Try removing a filter, or a shorter name search.", text_alignment="center")


def render() -> None:
    villagers = load_villagers()
    _seed_state()
    criteria = _sidebar(villagers)

    title, sort = st.columns([3, 1], vertical_alignment="bottom")
    title.title("Browse villagers")
    sort.selectbox("Sort by", SORT_OPTIONS, key=SORT, persist_state="session")

    matches = sort_villagers(apply_criteria(villagers, criteria), st.session_state[SORT])

    # Any change to what's being looked at starts again from the first page.
    signature = (criteria.active_facets, criteria.name_query, criteria.nh_only, st.session_state[SORT])
    if st.session_state.get(SIGNATURE) != signature:
        st.session_state[SIGNATURE] = signature
        st.session_state[LIMIT] = PAGE_SIZE

    scope = "in New Horizons" if criteria.nh_only else "across the series"
    in_scope = len(base_pool(villagers, criteria.nh_only))
    if matches.empty:
        _empty_state(villagers, criteria)
        villager_details(villagers)
        return

    limit = st.session_state[LIMIT]
    shown = matches.head(limit)
    if criteria.is_unconstrained:
        st.caption(f"All {in_scope} villagers {scope}. Showing {len(shown)}.")
    else:
        st.caption(f"{len(matches)} of {in_scope} villagers {scope} match. Showing {len(shown)}.")

    villager_grid(shown, upcoming=matches.iloc[limit : limit + PAGE_SIZE])
    villager_details(villagers)

    if len(shown) < len(matches):
        remaining = len(matches) - len(shown)
        with st.container(horizontal_alignment="center"):
            st.button(
                f"Show {min(PAGE_SIZE, remaining)} more ({remaining} left)",
                icon=":material/expand_more:",
                on_click=_show_more,
            )


render()
