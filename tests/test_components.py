import re

import pandas as pd
import pytest

from src.components import (
    DEFAULT_CHIP_BG,
    _chip,
    _nookipedia_url,
    _safe_colour,
    contrast_ratio,
    escape_markdown,
    readable_text_colour,
)
from src.data import DATA_PATH, clean_villagers


@pytest.mark.parametrize("value", ["#0961f6", "#FFFCE9"])
def test_hex_colours_pass_through(value):
    assert _safe_colour(value, "#000000") == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        float("nan"),
        "red",
        "#fff",
        "#0961f6; background:url(https://evil.example/)",
        '#000000"><script>alert(1)</script>',
        "expression(alert(1))",
    ],
)
def test_anything_else_falls_back_to_default(value):
    assert _safe_colour(value, "#123456") == "#123456"


def test_chip_escapes_name_and_rejects_hostile_colours():
    out = _chip('<img src=x onerror=alert(1)>', '#000"><script>')
    assert "<img" not in out and "<script>" not in out
    assert "&lt;img" in out
    assert DEFAULT_CHIP_BG in out


# ------------------------------------------------------------------- contrast


def test_contrast_ratio_known_values():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#8bcdea", "#fffad4") == pytest.approx(1.65, abs=0.01)  # wiki pair (Baabara)


@pytest.mark.parametrize("bg", ["#000000", "#ffffff", "#777777", "#767676", "#8bcdea", "#9edc03", "#0961f6"])
def test_readable_text_meets_wcag_aa_on_any_background(bg):
    assert contrast_ratio(bg, readable_text_colour(bg)) >= 4.5


def test_every_name_chip_in_the_dataset_meets_wcag_aa():
    df = clean_villagers(pd.read_csv(DATA_PATH, dtype=str, keep_default_na=False))
    backgrounds = df["title_color"].fillna(DEFAULT_CHIP_BG)
    worst = min(contrast_ratio(bg, readable_text_colour(bg)) for bg in backgrounds)
    assert worst >= 4.5


# ----------------------------------------------------------------------- text


# Each case was rendered in a real Streamlit 1.64 page and confirmed inert (no icon,
# link, image, LaTeX or emphasis element). Backslashes alone did NOT stop the
# :material: icon or the bare-URL / www. auto-links; these tests pin the fix.
HOSTILE_MARKDOWN = [
    ":material/warning: fake alert",
    ":smile: emoji",
    ":red[coloured]",
    "![beacon](https://evil.example/x.png)",
    "[click me](https://evil.example)",
    "plain https://evil.example/path",
    "see www.evil.example now",
    "mail me@evil.example",
    "$\\LaTeX$",
    "**bold** _italic_ `code`",
    "<b>html</b>",
]


@pytest.mark.parametrize("hostile", HOSTILE_MARKDOWN)
def test_escape_markdown_breaks_every_active_construct(hostile):
    escaped = escape_markdown(hostile)
    for token in ("](", "![", ":material", ":smile", ":red", "://", "www", "@evil", "**"):
        assert token not in escaped, (token, escaped)
    assert not re.search(r"(?<!\\)[`$_]", escaped), escaped  # code, LaTeX, emphasis stay escaped


@pytest.mark.parametrize("text", HOSTILE_MARKDOWN + ["K.K. Stroll", "Étoile", "Kiki & Lala tee"])
def test_escape_markdown_leaves_visible_text_unchanged(text):
    from src.components import _ZWSP

    rendered = escape_markdown(text).replace(_ZWSP, "")
    assert re.sub(r"\\(.)", r"\1", rendered) == text


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://nookipedia.com/wiki/Ace", "https://nookipedia.com/wiki/Ace"),
        ("javascript:alert(1)", None),
        ("https://evil.example/wiki/Ace", None),
        ("http://nookipedia.com/wiki/Ace", None),
        (float("nan"), None),
    ],
)
def test_only_nookipedia_wiki_links_are_rendered(url, expected):
    assert _nookipedia_url(url) == expected
