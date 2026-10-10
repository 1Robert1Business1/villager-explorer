"""Find Your Match: answer a few light questions, get villagers ranked by fit.

Built on the shared engine: src.filters.rank_matches scores each villager with
the same facet_mask Browse filters with. This page only asks the questions and
shows the answers' consequences. Every result says why it matched.

State: answers live in session state with persist_state="session" (separate
from Browse's filters), so they survive a trip to Browse and back.
"""

from __future__ import annotations

import streamlit as st

from src.components import PAGE_SIZE, match_note_html, villager_details, villager_grid
from src.data import load_villagers
from src.filters import FACETS, MATCH_FACETS, base_pool, facet_options, rank_matches

LIMIT = "match_limit"
SIGNATURE = "match_signature"

# The questions, worded lightly. Long option lists get a searchable multiselect.
QUESTIONS = {
    "species": "Any favourite species?",
    "personality": "Which personalities do you get on with?",
    "hobby": "A hobby you'd like them to share?",
    "styles": "Which clothing styles do you like?",
    "colors": "Favourite colours?",
}
MULTISELECT = {"species", "colors"}


def answer_key(column: str) -> str:
    return f"match_answer_{column}"


def _clear_answers() -> None:
    for column in MATCH_FACETS:
        st.session_state[answer_key(column)] = []


def _show_more() -> None:
    st.session_state[LIMIT] += PAGE_SIZE


def _questions(pool) -> dict[str, list[str]]:
    with st.container(border=True):
        for column in MATCH_FACETS:
            key = answer_key(column)
            options = facet_options(pool, column)
            st.session_state.setdefault(key, [])
            st.session_state[key] = [v for v in st.session_state[key] if v in options]
            label = QUESTIONS[column]
            if column in MULTISELECT:
                st.multiselect(
                    label, options, key=key, persist_state="session",
                    placeholder=f"Any {FACETS[column].label.lower()}",
                )
            else:
                st.pills(label, options, selection_mode="multi", key=key, persist_state="session")
        answers = {c: st.session_state[answer_key(c)] for c in MATCH_FACETS}
        st.button(
            "Clear answers",
            icon=":material/restart_alt:",
            on_click=_clear_answers,
            disabled=not any(answers.values()),
        )
    return answers


def render() -> None:
    villagers = load_villagers()
    pool = base_pool(villagers, nh_only=True)

    st.title("Find your match")
    st.caption(
        "Answer as many or as few as you like. Villagers are ranked by how many of your "
        f"answers they fit. **Matching uses the {len(pool)} New Horizons villagers**: the "
        f"{len(villagers) - len(pool)} from earlier games have no hobby, style or colour "
        "on record, so they can't be matched fairly. They're all in Browse."
    )
    answers = _questions(pool)
    answered = sum(bool(v) for v in answers.values())

    if not answered:
        st.info("Pick at least one answer above to see your matches.", icon=":material/touch_app:")
        villager_details(villagers)
        return

    ranked = rank_matches(villagers, answers)
    signature = tuple((c, tuple(sorted(v))) for c, v in answers.items())
    if st.session_state.get(SIGNATURE) != signature:
        st.session_state[SIGNATURE] = signature
        st.session_state[LIMIT] = PAGE_SIZE

    best = int(ranked["match_score"].max()) if not ranked.empty else 0
    perfect = int((ranked["match_score"] == answered).sum())

    st.subheader("Your matches")
    if best == 0:
        st.warning("No villager fits any of those answers. Try loosening one.", icon=":material/search_off:")
        villager_details(villagers)
        return
    if perfect:
        st.caption(
            f"{perfect} villager{'s' if perfect != 1 else ''} fit all {answered} of your answers"
            if answered > 1 else f"{perfect} villagers fit your answer"
        )
    else:
        st.info(
            f"No villager fits all {answered} answers. Here are the closest: the best fit "
            f"{best} of {answered}. Dropping an answer might find a perfect match.",
            icon=":material/tune:",
        )
    st.caption(
        f"{len(ranked)} villagers fit at least one answer. Best matches first; "
        "villagers with the same score are listed alphabetically."
    )

    limit = st.session_state[LIMIT]
    shown = ranked.head(limit)
    notes = {
        key: match_note_html(row.matched, row.missed, row.match_score, row.match_total)
        for key, row in zip(shown.index, shown.itertuples())
    }
    villager_grid(shown, upcoming=ranked.iloc[limit : limit + PAGE_SIZE], notes=notes)

    if len(shown) < len(ranked):
        remaining = len(ranked) - len(shown)
        with st.container(horizontal_alignment="center"):
            st.button(
                f"Show {min(PAGE_SIZE, remaining)} more ({remaining} left)",
                icon=":material/expand_more:",
                on_click=_show_more,
                key="match_show_more",
            )
    villager_details(villagers)


render()
