import pytest

from src.components import DEFAULT_CHIP, _chip, _safe_colour


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
    out = _chip('<img src=x onerror=alert(1)>', '#000"><script>', "#ffffff")
    assert "<img" not in out and "<script>" not in out
    assert "&lt;img" in out
    assert DEFAULT_CHIP[0] in out
