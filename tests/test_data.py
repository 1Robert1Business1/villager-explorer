"""Invariants of the committed dataset, so a bad refresh fails tests instead of shipping."""

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

from src.data import DATA_PATH, clean_villagers
from src.images import check_url_allowed

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_dataset import hex_colour  # noqa: E402


@pytest.fixture(scope="module")
def villagers():
    return clean_villagers(pd.read_csv(DATA_PATH, dtype=str, keep_default_na=False))


def test_every_colour_is_strict_hex(villagers):
    for col in ("title_color", "text_color"):
        values = villagers[col].dropna()
        assert values.map(lambda v: bool(re.fullmatch(r"#[0-9a-f]{6}", v))).all(), col


def test_every_image_url_is_https_on_the_allowlist(villagers):
    for url in pd.concat([villagers["icon_url"], villagers["image_url"]]).dropna():
        check_url_allowed(url)  # raises if not


@pytest.mark.parametrize(
    "raw, expected",
    [
        (515151, "515151"),
        (51515, "051515"),  # leading zero lost as a JSON number
        (735200000000, "7352e8"),  # "7352e8" read as scientific notation
        (8.78e88, "878e86"),
        ("#0961F6", "0961f6"),
        ("", ""),
        (None, ""),
    ],
)
def test_cargo_numeric_colours_are_recovered(raw, expected):
    assert hex_colour(raw) == expected


@pytest.mark.parametrize("raw", ["red", "zzzzzz", 1.5, "#12345"])
def test_unrecoverable_colours_fail_the_build(raw):
    with pytest.raises(ValueError):
        hex_colour(raw)
