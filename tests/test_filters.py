import pandas as pd
import pytest

from src.data import DATA_PATH, clean_villagers
from src.filters import (
    UNKNOWN,
    Criteria,
    apply_criteria,
    facet_counts,
    facet_mask,
    facet_options,
    fold,
    sort_villagers,
)


def _raw(rows):
    cols = [
        "key", "name", "species", "personality", "sub_personality", "gender", "birthday",
        "birthday_month", "birthday_day", "sign", "catchphrase", "quote", "hobby",
        "fav_style1", "fav_style2", "fav_color1", "fav_color2", "favorite_song", "clothing",
        "debut", "games", "in_nh", "islander", "image_url", "icon_url", "photo_url",
        "nookipedia_url", "title_color", "text_color",
    ]
    base = dict.fromkeys(cols, "")
    return pd.DataFrame([{**base, **r} for r in rows], columns=cols, dtype=str)


@pytest.fixture
def villagers():
    return clean_villagers(_raw([
        dict(key="Ace", name="Ace", species="Bird", personality="Jock", gender="Male",
             birthday="August 11", birthday_month="August", birthday_day="11", hobby="Nature",
             fav_style1="Active", fav_style2="Cute", in_nh="1", image_url="a", games="NH"),
        dict(key="Étoile", name="Étoile", species="Sheep", personality="Normal", gender="Female",
             birthday="December 25", birthday_month="December", birthday_day="25", hobby="Music",
             fav_style1="Cute", in_nh="1", image_url="e", games="NH"),
        dict(key="Carmen", name="Carmen", species="Rabbit", personality="Peppy", gender="Female",
             birthday="January 6", birthday_month="January", birthday_day="6", hobby="Fashion",
             fav_style1="Elegant", fav_style2="Cute", in_nh="1", image_url="c", games="NH"),
        dict(key="Carmen_(mouse)", name="Carmen", species="Mouse", personality="Normal",
             gender="Female", in_nh="0", image_url="m", games="DnM"),
    ]))


def test_empty_criteria_returns_everyone_in_scope(villagers):
    assert len(apply_criteria(villagers, Criteria(nh_only=True))) == 3
    assert len(apply_criteria(villagers, Criteria(nh_only=False))) == 4


def test_or_within_facet_and_across_facets(villagers):
    either = Criteria({"species": ["Bird", "Sheep"]})
    assert set(apply_criteria(villagers, either).index) == {"Ace", "Étoile"}
    both = Criteria({"species": ["Bird", "Sheep"], "gender": ["Female"]})
    assert set(apply_criteria(villagers, both).index) == {"Étoile"}


def test_missing_hobby_is_unknown_not_silently_dropped(villagers):
    everyone = Criteria({"hobby": ["Music", UNKNOWN]}, nh_only=False)
    assert set(apply_criteria(villagers, everyone).index) == {"Étoile", "Carmen_(mouse)"}
    # Without Unknown selected, the villager with no hobby on record is excluded, visibly
    # so, because Unknown is offered as an option whenever such villagers are in scope.
    assert UNKNOWN in facet_options(villagers, "hobby")
    assert UNKNOWN not in facet_options(villagers[villagers["in_nh"]], "hobby")


def test_list_valued_facet_matches_any(villagers):
    cute = Criteria({"styles": ["Cute"]})
    assert set(apply_criteria(villagers, cute).index) == {"Ace", "Étoile", "Carmen"}
    assert facet_counts(villagers, "styles")["Cute"] == 3
    unknown_style = facet_mask(villagers, "styles", [UNKNOWN])
    assert list(villagers.index[unknown_style]) == ["Carmen_(mouse)"]


def test_name_search_is_case_and_accent_insensitive(villagers):
    assert list(apply_criteria(villagers, Criteria(name_query="  ETOILE ")).index) == ["Étoile"]
    assert fold("Jūbei") == "jubei"
    both_carmens = apply_criteria(villagers, Criteria(name_query="carm", nh_only=False))
    assert set(both_carmens.index) == {"Carmen", "Carmen_(mouse)"}


def test_month_options_are_in_calendar_order(villagers):
    assert facet_options(villagers[villagers["in_nh"]], "birthday_month") == [
        "January", "August", "December",
    ]


def test_birthday_sort_puts_missing_last(villagers):
    order = list(sort_villagers(villagers, "Birthday").index)
    assert order == ["Carmen", "Ace", "Étoile", "Carmen_(mouse)"]


def test_unknown_facet_name_fails_loudly():
    with pytest.raises(ValueError, match="Unknown facet"):
        Criteria({"specis": ["Cat"]})


def test_real_dataset_scope_counts():
    df = clean_villagers(pd.read_csv(DATA_PATH, dtype=str, keep_default_na=False))
    assert len(apply_criteria(df, Criteria(nh_only=False))) == 490
    assert len(apply_criteria(df, Criteria(nh_only=True))) == 417
    peppy_cats = apply_criteria(df, Criteria({"species": ["Cat"], "personality": ["Peppy"]}))
    assert len(peppy_cats) > 0
    assert set(peppy_cats["species"]) == {"Cat"} and set(peppy_cats["personality"]) == {"Peppy"}
