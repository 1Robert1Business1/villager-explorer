"""Compare: selection rules, share links and the comparison rows. Pure (no Streamlit).

Villagers are always identified by their unique key (the Nookipedia page name),
never by display name: two Carmens and two Lulus exist. Anywhere a name appears
alone for picking, colliding names get the species added so the right villager
is chosen.

Share links (?v=raymond,marshal) are user input. They are only ever used to
look up keys that exist in the dataset; unknown tokens are counted and dropped,
and their text is never rendered.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import pandas as pd

from src.filters import fold

MAX_COMPARE = 4
MAX_SHARE_TOKENS = 20  # ignore anything past this in a pasted link
MAX_TOKEN_LENGTH = 64  # the longest real key is far shorter

FULL_MESSAGE = f"Compare holds up to {MAX_COMPARE} villagers. Remove one to add another."


# ----------------------------------------------------------------- labels


def picker_labels(df: pd.DataFrame) -> dict[str, str]:
    """Key -> label to pick by. Colliding names get the species: "Carmen (Mouse)".

    Labels are guaranteed unique; if name + species ever collided too, the key is
    appended as a last resort.
    """
    counts = df["name"].value_counts()
    labels = {
        key: f"{name} ({species})" if counts[name] > 1 else str(name)
        for key, name, species in zip(df.index, df["name"], df["species"].astype(str))
    }
    seen = pd.Series(labels)
    for key in seen[seen.duplicated(keep=False)].index:
        labels[key] = f"{labels[key]} [{key}]"
    return labels


# -------------------------------------------------------------- selection


@dataclass(frozen=True)
class Change:
    keys: tuple[str, ...]
    message: str | None = None  # shown to the visitor when the change was refused


def sanitize(keys: Iterable[str], valid: Iterable[str]) -> tuple[str, ...]:
    """Known keys only, no duplicates, original order, at most MAX_COMPARE."""
    valid = set(valid)
    return tuple(dict.fromkeys(k for k in keys if k in valid))[:MAX_COMPARE]


def add(keys: Sequence[str], key: str, valid: Iterable[str]) -> Change:
    keys = tuple(keys)
    if key not in set(valid):
        return Change(keys, "That villager isn't in the dataset.")
    if key in keys:
        return Change(keys)  # already selected: adding again is a no-op
    if len(keys) >= MAX_COMPARE:
        return Change(keys, FULL_MESSAGE)
    return Change(keys + (key,))


def remove(keys: Sequence[str], key: str) -> Change:
    return Change(tuple(k for k in keys if k != key))


def toggle(keys: Sequence[str], key: str, valid: Iterable[str]) -> Change:
    return remove(keys, key) if key in keys else add(keys, key, valid)


# ------------------------------------------------------------- share links


def share_param(keys: Sequence[str]) -> str:
    """The ?v= value for a selection: lower-cased keys, comma-separated."""
    return ",".join(k.casefold() for k in keys)


def parse_share_param(raw: str | Sequence[str] | None, valid: Iterable[str]) -> tuple[tuple[str, ...], int]:
    """Resolve a ?v= value to dataset keys. Returns (keys, number of tokens ignored).

    Matching is case- and accent-insensitive ("raymond", "ETOILE" work). Only
    exact matches to existing keys count; everything else is ignored, including
    oversized tokens and anything past MAX_SHARE_TOKENS. The result is capped at
    MAX_COMPARE, extra valid keys counting as ignored.
    """
    if raw is None:
        return (), 0
    parts = [raw] if isinstance(raw, str) else list(raw)
    tokens = [t.strip() for part in parts for t in str(part).split(",") if t.strip()]
    lookup = {fold(k): k for k in valid}
    ignored = max(0, len(tokens) - MAX_SHARE_TOKENS)
    resolved: list[str] = []
    for token in tokens[:MAX_SHARE_TOKENS]:
        key = lookup.get(fold(token)) if len(token) <= MAX_TOKEN_LENGTH else None
        if key is None:
            ignored += 1
        elif key not in resolved:
            resolved.append(key)
    ignored += max(0, len(resolved) - MAX_COMPARE)
    return tuple(resolved[:MAX_COMPARE]), ignored


# --------------------------------------------------------- comparison rows


@dataclass(frozen=True)
class CompareRow:
    label: str
    values: tuple  # one per villager: str or None (unknown); tuples for multi rows
    multi: bool = False
    same: bool = False  # every villager has the same, known value
    common: frozenset = frozenset()  # multi rows: values every villager shares


def _known(v: object) -> str | None:
    return str(v) if isinstance(v, str) and v else None


def _short_birthday(row: pd.Series) -> str | None:
    month, day = row["birthday_month"], row["birthday_day"]
    if pd.isna(month) or pd.isna(day):
        return None
    return f"{str(month)[:3]} {day}"


SCALAR_ROWS = (
    ("Species", lambda r: _known(r["species"])),
    ("Personality", lambda r: _known(r["personality"])),
    ("Gender", lambda r: _known(r["gender"])),
    ("Hobby", lambda r: _known(r["hobby"])),
    ("Birthday", _short_birthday),
    ("Star sign", lambda r: _known(r["sign"])),
    ("Catchphrase", lambda r: _known(r["catchphrase"])),
    ("Favourite song", lambda r: _known(r["favorite_song"])),
)
MULTI_ROWS = (("Styles", "styles"), ("Colours", "colors"))


def comparison_rows(selected: pd.DataFrame) -> list[CompareRow]:
    """Attribute rows for the villagers in `selected`, in order, with shared values marked.

    A row is "same" only when every villager has a known, equal value; Unknown
    never counts as shared (two villagers missing a hobby don't share one).
    """
    rows = []
    for label, get in SCALAR_ROWS:
        values = tuple(get(r) for _, r in selected.iterrows())
        same = len(values) > 1 and None not in values and len(set(values)) == 1
        rows.append(CompareRow(label, values, same=same))
    for label, column in MULTI_ROWS:
        values = tuple(tuple(v) for v in selected[column])
        common = frozenset.intersection(*map(frozenset, values)) if values and all(values) else frozenset()
        same = len(values) > 1 and all(values) and len({frozenset(v) for v in values}) == 1
        rows.append(CompareRow(label, values, multi=True, same=same, common=common))
    return rows
