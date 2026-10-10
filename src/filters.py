"""The shared filtering engine: the single source of truth for "which villagers match".

Browse drives it from filter widgets, Match from quiz answers, Compare from its
picker. None of them filter the frame themselves; they all build Criteria and
call apply_criteria (or facet_mask, to score partial matches).

Pure pandas, no Streamlit, so it is fast to test.

Semantics
---------
* Within one facet, selected values are OR'd ("Cat or Dog").
* Across facets, constraints are AND'd ("Cat or Dog" and "Peppy").
* An empty selection means "no constraint", never "match nothing".
* Missing data is "Unknown", a selectable value. A villager whose hobby isn't
  recorded is only excluded by a hobby filter if the user didn't pick Unknown.
  Nothing is dropped silently.
* List-valued facets (favourite styles/colours) match if *any* of the villager's
  values is selected. An empty tuple counts as Unknown.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

import pandas as pd

UNKNOWN = "Unknown"


@dataclass(frozen=True)
class Facet:
    column: str
    label: str
    multi_valued: bool = False  # column holds tuples (styles, colors)


FACETS: dict[str, Facet] = {
    f.column: f
    for f in (
        Facet("species", "Species"),
        Facet("personality", "Personality"),
        Facet("gender", "Gender"),
        Facet("hobby", "Hobby"),
        Facet("sign", "Star sign"),
        Facet("birthday_month", "Birthday month"),
        Facet("styles", "Favourite style", multi_valued=True),
        Facet("colors", "Favourite colour", multi_valued=True),
    )
}


@dataclass(frozen=True)
class Criteria:
    """What the user is looking for. Views build one of these; the engine applies it."""

    selections: Mapping[str, Collection[str]] = field(default_factory=dict)
    name_query: str = ""
    nh_only: bool = True

    def __post_init__(self) -> None:
        unknown = set(self.selections) - set(FACETS)
        if unknown:
            raise ValueError(f"Unknown facet(s): {sorted(unknown)}. Known: {sorted(FACETS)}")

    @property
    def active_facets(self) -> dict[str, frozenset[str]]:
        return {col: frozenset(vals) for col, vals in self.selections.items() if vals}

    @property
    def is_unconstrained(self) -> bool:
        """True if nothing beyond the NH scope is filtering (no facets, no search)."""
        return not self.active_facets and not self.name_query.strip()


# --------------------------------------------------------------------------- pool


def base_pool(df: pd.DataFrame, nh_only: bool) -> pd.DataFrame:
    """The villagers in scope before any facet filter: everyone, or New Horizons only."""
    return df[df["in_nh"]] if nh_only else df


# ------------------------------------------------------------------------- facets


def _is_unknown(series: pd.Series, facet: Facet) -> pd.Series:
    if facet.multi_valued:
        return series.map(len).eq(0)
    return series.isna()


def facet_options(df: pd.DataFrame, column: str) -> list[str]:
    """Values present in `df` for this facet, in natural order, plus Unknown if any are missing.

    Natural order is the column's category order (so months run January to December),
    or alphabetical for list-valued facets.
    """
    facet = FACETS[column]
    series = df[column]
    if facet.multi_valued:
        options = sorted({v for values in series for v in values})
    else:
        present = set(series.dropna().unique())
        options = [c for c in series.cat.categories if c in present]
    if _is_unknown(series, facet).any():
        options.append(UNKNOWN)
    return options


def facet_counts(df: pd.DataFrame, column: str) -> dict[str, int]:
    """How many villagers in `df` carry each option (a villager can count twice for styles)."""
    facet = FACETS[column]
    series = df[column]
    if facet.multi_valued:
        counts = series.explode().value_counts()
    else:
        counts = series.value_counts()
    result = {str(k): int(v) for k, v in counts.items() if v}
    unknown = int(_is_unknown(series, facet).sum())
    if unknown:
        result[UNKNOWN] = unknown
    return result


def facet_mask(df: pd.DataFrame, column: str, selected: Collection[str]) -> pd.Series:
    """Boolean mask: does each villager satisfy this one facet? Empty selection is all True."""
    facet = FACETS[column]
    series = df[column]
    selected = frozenset(selected)
    if not selected:
        return pd.Series(True, index=df.index)

    known = selected - {UNKNOWN}
    if facet.multi_valued:
        mask = series.map(lambda values: not known.isdisjoint(values))
    else:
        mask = series.isin(known).fillna(False).astype(bool)
    if UNKNOWN in selected:
        mask |= _is_unknown(series, facet)
    return mask


# -------------------------------------------------------------------------- search


def fold(text: str) -> str:
    """Case- and accent-insensitive form, so 'etoile' finds 'Étoile' and 'jubei' finds 'Jūbei'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold().strip()


def name_mask(df: pd.DataFrame, query: str) -> pd.Series:
    needle = fold(query)
    if not needle:
        return pd.Series(True, index=df.index)
    return df["name"].map(fold).str.contains(needle, regex=False)


# ------------------------------------------------------------------------- applying


def apply_criteria(df: pd.DataFrame, criteria: Criteria) -> pd.DataFrame:
    pool = base_pool(df, criteria.nh_only)
    mask = name_mask(pool, criteria.name_query)
    for column, selected in criteria.active_facets.items():
        mask &= facet_mask(pool, column, selected)
    return pool[mask]


# ------------------------------------------------------------------------ matching

# Questions Find Your Match asks, in display order. Each is an ordinary facet.
MATCH_FACETS = ("species", "personality", "hobby", "styles", "colors")


def rank_matches(df: pd.DataFrame, preferences: Mapping[str, Collection[str]]) -> pd.DataFrame:
    """Rank New Horizons villagers by how many answered questions they satisfy.

    No new filtering logic: each answered question is one facet_mask, exactly as
    Browse applies it (choosing Cat and Dog asks "Cat or Dog?"). A villager's
    score is the number of answered questions they satisfy, so picking two
    species never counts double. Villagers satisfying none are left out.

    Always New Horizons only: hobby, styles and colours exist only for NH
    villagers, so the 73 from earlier games can't be compared fairly.

    Order is deterministic: score (high first), then name (accent- and
    case-insensitive), then key. Ties read alphabetically, never randomly.

    Adds columns:
      match_score   questions satisfied
      match_total   questions answered
      matched       ((facet, value), ...) the villager's values that matched
      missed        ((facet, value), ...) the answers they didn't meet
    """
    answered = {col: frozenset(vals) for col, vals in preferences.items() if vals}
    unknown = set(answered) - set(FACETS)
    if unknown:
        raise ValueError(f"Unknown facet(s): {sorted(unknown)}. Known: {sorted(FACETS)}")

    pool = base_pool(df, nh_only=True)
    if not answered:
        return pool.iloc[0:0].assign(match_score=0, match_total=0, matched=(), missed=())

    order = [c for c in MATCH_FACETS if c in answered] + sorted(set(answered) - set(MATCH_FACETS))
    masks = {col: facet_mask(pool, col, answered[col]) for col in order}
    score = sum(mask.astype(int) for mask in masks.values())

    def explain(key) -> tuple[tuple, tuple]:
        matched, missed = [], []
        for col in order:
            chosen = answered[col]
            if masks[col].at[key]:
                value = pool.at[key, col]
                values = [v for v in value if v in chosen] if FACETS[col].multi_valued else [str(value)]
                matched.extend((col, v) for v in values)
            else:
                missed.extend((col, v) for v in sorted(chosen))
        return tuple(matched), tuple(missed)

    hits = pool[score > 0]
    explained = [explain(key) for key in hits.index]
    ranked = hits.assign(
        match_score=score[score > 0],
        match_total=len(answered),
        matched=[m for m, _ in explained],
        missed=[x for _, x in explained],
    )
    sort_keys = pd.DataFrame({
        "by_score": -ranked["match_score"],
        "by_name": ranked["name"].map(fold),
        "by_key": ranked.index.astype(str),
    }, index=ranked.index)
    return ranked.loc[sort_keys.sort_values(["by_score", "by_name", "by_key"], kind="stable").index]


# ------------------------------------------------------------------------- sorting


SORT_OPTIONS = ("Name", "Birthday", "Species")


def sort_villagers(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """Stable, human orderings. Missing birthdays sort last, not first."""
    name_key = df["name"].map(fold)
    if by == "Name":
        order = name_key.sort_values(kind="stable").index
    elif by == "Birthday":
        keys = pd.DataFrame({
            "month": df["birthday_month"].cat.codes.where(df["birthday_month"].notna(), 99),
            "day": df["birthday_day"].fillna(99),
            "name": name_key,
        })
        order = keys.sort_values(["month", "day", "name"], kind="stable").index
    elif by == "Species":
        keys = pd.DataFrame({"species": df["species"].astype(str), "name": name_key})
        order = keys.sort_values(["species", "name"], kind="stable").index
    else:
        raise ValueError(f"Unknown sort {by!r}; expected one of {SORT_OPTIONS}")
    return df.loc[order]
