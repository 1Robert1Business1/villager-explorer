import pandas as pd
import pytest

from src.compare import (
    FULL_MESSAGE,
    MAX_COMPARE,
    add,
    comparison_rows,
    parse_share_param,
    picker_labels,
    remove,
    sanitize,
    share_param,
    toggle,
)
from src.data import DATA_PATH, clean_villagers
from src.filters import fold


@pytest.fixture(scope="module")
def real():
    return clean_villagers(pd.read_csv(DATA_PATH, dtype=str, keep_default_na=False))


@pytest.fixture(scope="module")
def keys(real):
    return set(real.index)


# --------------------------------------------------------- duplicate names


def test_real_duplicate_names_get_distinguishing_labels(real):
    labels = picker_labels(real)
    assert labels["Carmen"] == "Carmen (Rabbit)"
    assert labels["Carmen_(mouse)"] == "Carmen (Mouse)"
    assert labels["Lulu"] == "Lulu (Hippo)"
    assert labels["Lulu_(anteater)"] == "Lulu (Anteater)"
    assert labels["Raymond"] == "Raymond"  # unique names stay plain


def test_picker_labels_are_unique_so_no_villager_is_ambiguous(real):
    labels = picker_labels(real)
    assert len(set(labels.values())) == len(labels) == len(real)


def test_label_collisions_fall_back_to_the_key():
    df = pd.DataFrame(
        {"name": ["Pat", "Pat"], "species": ["Cat", "Cat"]}, index=pd.Index(["Pat_1", "Pat_2"], name="key")
    )
    labels = picker_labels(df)
    assert labels == {"Pat_1": "Pat (Cat) [Pat_1]", "Pat_2": "Pat (Cat) [Pat_2]"}


def test_both_carmens_can_be_compared_side_by_side(keys):
    sel = add(add((), "Carmen", keys).keys, "Carmen_(mouse)", keys).keys
    assert sel == ("Carmen", "Carmen_(mouse)")


# ---------------------------------------------------------------- selection


def test_adding_twice_is_a_no_op(keys):
    first = add((), "Raymond", keys)
    again = add(first.keys, "Raymond", keys)
    assert again.keys == ("Raymond",) and again.message is None


def test_cap_refuses_a_fifth_with_a_clear_message(keys):
    sel = ("Raymond", "Marshal", "Ankha", "Judy")
    change = add(sel, "Sherb", keys)
    assert change.keys == sel and change.message == FULL_MESSAGE
    assert len(sel) == MAX_COMPARE


def test_unknown_keys_are_refused(keys):
    change = add((), "Not_A_Villager", keys)
    assert change.keys == () and change.message


def test_remove_and_toggle(keys):
    sel = ("Raymond", "Marshal")
    assert remove(sel, "Raymond").keys == ("Marshal",)
    assert remove(sel, "Nobody").keys == sel
    assert toggle(sel, "Marshal", keys).keys == ("Raymond",)
    assert toggle(sel, "Ankha", keys).keys == ("Raymond", "Marshal", "Ankha")


def test_sanitize_dedupes_validates_and_caps(keys):
    raw = ["Raymond", "Raymond", "Bogus", "Marshal", "Ankha", "Judy", "Sherb"]
    assert sanitize(raw, keys) == ("Raymond", "Marshal", "Ankha", "Judy")


# --------------------------------------------------------------- share links


def test_share_link_round_trips_every_real_key(keys):
    # Keys stay unique after case- and accent-folding, so links can be forgiving.
    assert len({fold(k) for k in keys}) == len(keys)
    for k in keys:
        assert parse_share_param(share_param([k]), keys) == ((k,), 0)


def test_share_link_parsing_is_forgiving_about_case_and_accents(keys):
    assert parse_share_param("raymond,MARSHAL", keys) == (("Raymond", "Marshal"), 0)
    assert parse_share_param("etoile,carmen_(mouse)", keys) == (("Étoile", "Carmen_(mouse)"), 0)
    assert parse_share_param(" raymond , , marshal ", keys) == (("Raymond", "Marshal"), 0)


@pytest.mark.parametrize(
    "raw",
    [
        "<script>alert(1)</script>",
        "raymond<img src=x>",
        "../../etc/passwd",
        "javascript:alert(1)",
        "r" * 500,
    ],
)
def test_hostile_link_tokens_are_ignored_not_resolved(raw, keys):
    resolved, ignored = parse_share_param(f"marshal,{raw}", keys)
    assert resolved == ("Marshal",) and ignored == 1


def test_share_link_dedupes_caps_and_counts_ignored(keys):
    resolved, ignored = parse_share_param("raymond,raymond,marshal,ankha,judy,sherb,bogus", keys)
    assert resolved == ("Raymond", "Marshal", "Ankha", "Judy")
    assert ignored == 2  # "sherb" over the cap, "bogus" unknown


def test_share_link_ignores_tokens_past_the_limit(keys):
    resolved, ignored = parse_share_param(",".join(["bogus"] * 30 + ["raymond"]), keys)
    assert resolved == () and ignored == 31


def test_share_link_accepts_repeated_params_and_none(keys):
    assert parse_share_param(["raymond", "marshal"], keys) == (("Raymond", "Marshal"), 0)
    assert parse_share_param(None, keys) == ((), 0)


# ---------------------------------------------------------- comparison rows


def test_shared_values_are_marked_and_unknown_never_counts_as_shared(real):
    rows = {r.label: r for r in comparison_rows(real.loc[["Raymond", "Marshal"]])}
    assert rows["Personality"].same and rows["Personality"].values == ("Smug", "Smug")
    assert not rows["Species"].same  # Cat vs Squirrel
    # Two villagers from earlier games both lack a hobby; that's not a shared hobby.
    old = {r.label: r for r in comparison_rows(real.loc[["Carmen_(mouse)", "Lulu_(anteater)"]])}
    assert old["Hobby"].values == (None, None) and not old["Hobby"].same
    assert old["Styles"].values == ((), ()) and not old["Styles"].same


def test_multi_rows_report_the_common_values(real):
    rows = {r.label: r for r in comparison_rows(real.loc[["Raymond", "Marshal"]])}
    styles = rows["Styles"]
    expected = set(real.at["Raymond", "styles"]) & set(real.at["Marshal", "styles"])
    assert styles.multi and styles.common == expected


def test_rows_follow_selection_order(real):
    rows = comparison_rows(real.loc[["Marshal", "Raymond"]])
    assert rows[0].values == (real.at["Marshal", "species"], real.at["Raymond", "species"])
