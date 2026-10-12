import html
import re

import pandas as pd
import pytest

from src.components import (
    DEFAULT_CHIP_BG,
    _nookipedia_url,
    _safe_colour,
    card_meta_html,
    chip_html,
    compare_table_html,
    contrast_ratio,
    details_appearances_html,
    details_favourites_html,
    details_profile_html,
    match_note_html,
    readable_text_colour,
)
from src.data import DATA_PATH, clean_villagers


@pytest.fixture(scope="module")
def villagers():
    return clean_villagers(pd.read_csv(DATA_PATH, dtype=str, keep_default_na=False))


# --------------------------------------------------------------------- colour


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


def test_contrast_ratio_known_values():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#8bcdea", "#fffad4") == pytest.approx(1.65, abs=0.01)  # wiki pair (Baabara)


@pytest.mark.parametrize("bg", ["#000000", "#ffffff", "#777777", "#767676", "#8bcdea", "#9edc03", "#0961f6"])
def test_readable_text_meets_wcag_aa_on_any_background(bg):
    assert contrast_ratio(bg, readable_text_colour(bg)) >= 4.5


def test_every_name_chip_in_the_dataset_meets_wcag_aa(villagers):
    backgrounds = villagers["title_color"].fillna(DEFAULT_CHIP_BG)
    worst = min(contrast_ratio(bg, readable_text_colour(bg)) for bg in backgrounds)
    assert worst >= 4.5


# ------------------------------------------------------------ dataset text
# Dataset text is rendered only through st.html after html.escape. st.html does not
# interpret markdown: every string below was rendered in a real Streamlit 1.64 page
# and produced no link, image, icon, LaTeX, emphasis or HTML element, with the
# visible text exactly equal to the input. These tests pin the escaping half.

HOSTILE = [
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
    "<b>html</b><img src=x onerror=alert(1)>",
    '"><script>alert(1)</script>',
]


def _visible_text(fragment: str) -> str:
    """Roughly what a browser shows: tags removed, entities decoded."""
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def _hostile_villager(villagers, value):
    v = villagers.loc["Ace"].copy()
    for col in ("name", "catchphrase", "birthday", "favorite_song", "clothing", "quote"):
        v[col] = value
    v["games"] = (value,)
    v["debut"] = value
    return v


@pytest.mark.parametrize("hostile", HOSTILE)
def test_dataset_text_is_escaped_into_html(villagers, hostile):
    v = _hostile_villager(villagers, hostile)
    rendered = [
        chip_html(v["name"], v["title_color"]),
        details_profile_html(v),
        details_appearances_html(v),
        details_favourites_html(v),
    ]
    for fragment in rendered:
        assert "<script" not in fragment and "<img" not in fragment and "<b>" not in fragment
        assert "​" not in fragment  # no hidden characters any more
    assert hostile in _visible_text(rendered[0])  # the name renders exactly as written
    assert hostile in _visible_text(rendered[1])  # so does the catchphrase/birthday


def test_match_note_explains_score_matches_and_misses():
    note = match_note_html(
        (("species", "Cat"), ("personality", "Peppy"), ("hobby", "Fashion")),
        (("styles", "Cute"),),
        3, 4,
    )
    text = _visible_text(note)
    assert "Matches 3 of 4: Cat, Peppy, Fashion hobby" in text
    assert "Not: Cute style" in text
    assert "Matches all 2: Cat, Blue" in _visible_text(match_note_html((("species", "Cat"), ("colors", "Blue")), (), 2, 2))
    assert "Not:" not in match_note_html((("species", "Cat"),), (), 1, 1)


@pytest.mark.parametrize("hostile", HOSTILE)
def test_match_note_escapes_dataset_values(hostile):
    note = match_note_html((("species", hostile),), (("hobby", hostile),), 1, 2)
    assert "<script" not in note and "<img" not in note and "<b>" not in note
    assert hostile in _visible_text(note)


@pytest.mark.parametrize("hostile", HOSTILE)
def test_compare_table_escapes_dataset_text(villagers, hostile):
    from src.compare import comparison_rows

    a = _hostile_villager(villagers, hostile)
    b = villagers.loc["Marshal"].copy()
    selected = pd.DataFrame([a, b], index=["Ace", "Marshal"])
    rows = comparison_rows(selected)
    table = compare_table_html(selected, rows, {"Ace": b"png", "Marshal": b"png"})
    assert "<script" not in table and "<img src=x" not in table and "<b>" not in table
    assert hostile in _visible_text(table)
    # Names land in data-name attributes too, which must not break out of the quotes.
    assert 'data-name="' + html.escape(hostile, quote=True) + '"' in table


def test_compare_table_disambiguates_the_two_carmens_everywhere(villagers):
    from src.compare import comparison_rows, picker_labels

    selected = villagers.loc[["Carmen", "Carmen_(mouse)"]]
    table = compare_table_html(
        selected, comparison_rows(selected), {"Carmen": b"x", "Carmen_(mouse)": b"x"}, labels=picker_labels(villagers)
    )
    # Narrow-layout value labels and image alt text never say just "Carmen".
    assert 'data-name="Carmen"' not in table and 'alt="Carmen"' not in table
    assert 'data-name="Carmen (Rabbit)"' in table and 'data-name="Carmen (Mouse)"' in table
    assert ">Rabbit</div>" in table and ">Mouse</div>" in table  # species under each chip


def test_compare_table_marks_shared_rows_and_values(villagers):
    from src.compare import comparison_rows

    selected = villagers.loc[["Raymond", "Marshal"]]
    table = compare_table_html(selected, comparison_rows(selected), {"Raymond": b"x", "Marshal": b"x"})
    row = lambda label: "<tr" + table.split(f">{label}<")[0].rsplit("<tr", 1)[1]  # noqa: E731
    assert 'class="vx-same"' in row("Personality")  # both Smug
    assert 'class="vx-same"' not in row("Species")  # Cat vs Squirrel
    shared_styles = set(selected.at["Raymond", "styles"]) & set(selected.at["Marshal", "styles"])
    assert table.count("(shared by all)") >= len(shared_styles)


def test_card_meta_hides_icon_names_from_screen_readers(villagers):
    meta = card_meta_html(villagers.loc["Ace"])
    assert 'aria-hidden="true">interests<' in meta and 'aria-hidden="true">cake<' in meta
    assert "Hobby:" in meta and "Birthday:" in meta
    assert "Nature" in meta and "Aug 11" in meta


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
