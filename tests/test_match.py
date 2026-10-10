import random

import pandas as pd
import pytest

from src.data import DATA_PATH, clean_villagers
from src.filters import Criteria, apply_criteria, rank_matches
from tests.test_filters import _raw


def _v(key, **kw):
    base = dict(key=key, name=key, species="Cat", personality="Lazy", gender="Male",
                hobby="Play", fav_style1="Simple", in_nh="1", image_url="x", games="NH")
    return {**base, **kw}


@pytest.fixture
def villagers():
    return clean_villagers(_raw([
        _v("Bob", species="Cat", personality="Lazy", hobby="Play", fav_style1="Simple"),
        _v("Ankha", species="Cat", personality="Snooty", hobby="Fashion", fav_style1="Elegant", fav_style2="Cute"),
        _v("Kiki", species="Cat", personality="Normal", hobby="Fashion", fav_style1="Cute"),
        _v("Rosie", species="Cat", personality="Peppy", hobby="Fashion", fav_style1="Cute", fav_style2="Active"),
        _v("Goldie", species="Dog", personality="Normal", hobby="Nature", fav_style1="Simple"),
        _v("Zucker", species="Octopus", personality="Lazy", hobby="Nature", fav_style1="Simple"),
        # Not in New Horizons: must never be ranked, even when it matches.
        _v("Carmen_(mouse)", name="Carmen", species="Cat", personality="Peppy", hobby="", fav_style1="", in_nh="0"),
    ]))


def test_no_answers_means_no_results(villagers):
    assert rank_matches(villagers, {}).empty
    assert rank_matches(villagers, {"species": [], "hobby": []}).empty


def test_score_counts_questions_not_values(villagers):
    ranked = rank_matches(villagers, {"species": ["Cat", "Dog"], "personality": ["Peppy"]})
    # Picking two species is one question: Rosie meets both questions, Goldie one.
    assert ranked.loc["Rosie", "match_score"] == 2
    assert ranked.loc["Goldie", "match_score"] == 1
    assert set(ranked["match_total"]) == {2}


def test_ranking_is_score_then_alphabetical(villagers):
    ranked = rank_matches(villagers, {"species": ["Cat"], "hobby": ["Fashion"]})
    assert list(ranked.index) == ["Ankha", "Kiki", "Rosie", "Bob"]  # 2,2,2 alphabetical, then 1


def test_order_does_not_depend_on_input_order(villagers):
    prefs = {"species": ["Cat"], "styles": ["Cute"], "hobby": ["Fashion", "Nature"]}
    expected = list(rank_matches(villagers, prefs).index)
    for seed in range(5):
        shuffled = villagers.sample(frac=1, random_state=seed)
        assert list(rank_matches(shuffled, prefs).index) == expected


def test_villagers_matching_nothing_are_left_out(villagers):
    ranked = rank_matches(villagers, {"species": ["Octopus"]})
    assert list(ranked.index) == ["Zucker"]


def test_only_new_horizons_villagers_are_ranked(villagers):
    ranked = rank_matches(villagers, {"species": ["Cat"], "personality": ["Peppy"]})
    assert "Carmen_(mouse)" not in ranked.index
    assert ranked.index[0] == "Rosie"


def test_explanation_lists_matched_values_and_misses(villagers):
    prefs = {"species": ["Cat"], "personality": ["Peppy"], "hobby": ["Fashion"], "styles": ["Cute", "Gorgeous"]}
    rosie = rank_matches(villagers, prefs).loc["Rosie"]
    assert rosie["match_score"] == 4 and rosie["match_total"] == 4
    assert rosie["matched"] == (("species", "Cat"), ("personality", "Peppy"), ("hobby", "Fashion"), ("styles", "Cute"))
    assert rosie["missed"] == ()
    kiki = rank_matches(villagers, prefs).loc["Kiki"]
    assert kiki["match_score"] == 3
    assert kiki["missed"] == (("personality", "Peppy"),)


def test_multi_valued_facets_report_only_the_overlap(villagers):
    ankha = rank_matches(villagers, {"styles": ["Cute", "Active"]}).loc["Ankha"]
    assert ankha["matched"] == (("styles", "Cute"),)  # Ankha likes Elegant and Cute


def test_unknown_question_fails_loudly(villagers):
    with pytest.raises(ValueError, match="Unknown facet"):
        rank_matches(villagers, {"star_sign": ["Leo"]})


# ---------------------------------------------------------------- real dataset


@pytest.fixture(scope="module")
def real():
    return clean_villagers(pd.read_csv(DATA_PATH, dtype=str, keep_default_na=False))


def test_single_question_agrees_with_browse_filtering(real):
    # Same engine: one answer in Match finds exactly what the same filter finds in Browse.
    for prefs in ({"species": ["Cat"]}, {"hobby": ["Music"]}, {"styles": ["Cute", "Cool"]}):
        ranked = rank_matches(real, prefs)
        browsed = apply_criteria(real, Criteria(prefs, nh_only=True))
        assert set(ranked.index) == set(browsed.index)


def test_real_ranking_is_sorted_and_scores_are_consistent(real):
    rng = random.Random(7)
    for _ in range(20):
        prefs = {
            "species": rng.sample(list(real["species"].cat.categories), 2),
            "personality": rng.sample(list(real["personality"].cat.categories), 1),
            "hobby": rng.sample(list(real["hobby"].cat.categories), 1),
            "colors": ["Blue"],
        }
        ranked = rank_matches(real, prefs)
        assert ranked["match_score"].is_monotonic_decreasing
        assert ranked["in_nh"].all()
        assert (ranked["match_score"] == [len({f for f, _ in m}) for m in ranked["matched"]]).all()
        assert (ranked["match_score"] + [len({f for f, _ in x}) for x in ranked["missed"]] == 4).all()
