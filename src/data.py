"""Load and clean the villager catalogue.

data/villagers.csv is a committed snapshot of Nookipedia (CC BY-SA 3.0), built by
scripts/build_dataset.py. Everything that turns that raw export into the frame
the app relies on happens here, once, behind st.cache_data.

The contract every view can rely on:

* Index is `key` (the Nookipedia page name): unique and stable, unlike `name`
  (there are two Carmens and two Lulus).
* Missing values are real NA, never "" (the raw export uses empty strings).
* Filterable fields are Categorical with consistent casing.
* `styles`, `colors` and `games` are tuples of strings (empty tuple if unknown).
* HTML entities from the wiki ("K.K. D&amp;B") are decoded.

Coverage is uneven by design of the source: hobby, favourite styles/colours,
favourite song and the small icon exist only for the 417 New Horizons villagers
(`in_nh`). Views should treat those fields as optional.
"""

from __future__ import annotations

import calendar
import html
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "villagers.csv"

REQUIRED_COLUMNS = ["key", "name", "species", "personality", "gender", "image_url"]

# Filterable fields. Casing is normalised ("bear cub" and "Bear cub" are one value).
CATEGORICAL_COLUMNS = ["species", "personality", "sub_personality", "gender", "hobby", "sign"]

# Two-slot source fields collapsed into one tuple column each.
PAIRED_COLUMNS = {
    "styles": ("fav_style1", "fav_style2"),
    "colors": ("fav_color1", "fav_color2"),
}

MONTHS = list(calendar.month_name)[1:]


def _decode_entities(value: str) -> str:
    """Decode HTML entities, including double-escaped ones like '&amp;#39;'."""
    while True:
        decoded = html.unescape(value)
        if decoded == value:
            return decoded
        value = decoded


def _normalise_case(series: pd.Series) -> pd.Series:
    """Capitalise the first letter only, so 'bear cub' -> 'Bear cub' (not 'Bear Cub')."""
    return series.str[:1].str.upper() + series.str[1:]


def _to_tuple(*values) -> tuple[str, ...]:
    """Non-missing values, deduplicated, order preserved."""
    return tuple(dict.fromkeys(v for v in values if isinstance(v, str)))


def clean_villagers(raw: pd.DataFrame) -> pd.DataFrame:
    """Turn the raw string-typed CSV into the app's villager frame (pure; no Streamlit)."""
    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"villagers.csv is missing required columns: {missing}")

    df = raw.copy()

    text_cols = df.columns
    df[text_cols] = df[text_cols].apply(lambda s: s.str.strip().map(_decode_entities))
    df = df.mask(df.eq(""))

    if df["key"].isna().any() or df["key"].duplicated().any():
        raise ValueError("villagers.csv: 'key' must be present and unique for every villager")
    empty_required = [c for c in REQUIRED_COLUMNS if df[c].isna().any()]
    if empty_required:
        raise ValueError(f"villagers.csv has blank values in required columns: {empty_required}")

    for col in CATEGORICAL_COLUMNS:
        values = _normalise_case(df[col])
        df[col] = pd.Categorical(values, categories=sorted(values.dropna().unique()))

    for new_col, (first, second) in PAIRED_COLUMNS.items():
        normalised = [_normalise_case(df[first]), _normalise_case(df[second])]
        df[new_col] = [_to_tuple(a, b) for a, b in zip(*normalised)]
    df = df.drop(columns=[c for pair in PAIRED_COLUMNS.values() for c in pair])

    df["games"] = [tuple(g.split("|")) if isinstance(g, str) else () for g in df["games"]]

    df["birthday_month"] = pd.Categorical(df["birthday_month"], categories=MONTHS, ordered=True)
    df["birthday_day"] = pd.to_numeric(df["birthday_day"]).astype("Int64")

    for col in ["in_nh", "islander"]:
        df[col] = df[col].eq("1")

    for col in ["title_color", "text_color"]:
        df[col] = ("#" + df[col]).where(df[col].notna())

    # 'key' uniqueness is validated above.
    return df.set_index("key").sort_values("name", key=lambda s: s.str.casefold())


@st.cache_data(show_spinner="Loading villagers…")
def load_villagers(path: Path = DATA_PATH) -> pd.DataFrame:
    """Read and clean the committed villager CSV. Cached: runs once per process."""
    # dtype=str + keep_default_na=False: read every cell verbatim and decide what
    # counts as missing in one place (clean_villagers), rather than relying on
    # pandas' NA-string guessing on a dataset with a villager named "Nan".
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    return clean_villagers(raw)


@dataclass(frozen=True)
class DataReport:
    villagers: int
    in_new_horizons: int
    islanders: int
    missing: dict[str, int]  # column -> count of NA / empty tuples, only where > 0


def data_report(df: pd.DataFrame) -> DataReport:
    missing = {}
    for col in df.columns:
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, tuple)).all():
            n = int(df[col].map(len).eq(0).sum())
        else:
            n = int(df[col].isna().sum())
        if n:
            missing[col] = n
    return DataReport(
        villagers=len(df),
        in_new_horizons=int(df["in_nh"].sum()),
        islanders=int(df["islander"].sum()),
        missing=missing,
    )
