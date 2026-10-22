"""End-to-end flows through the real app scripts (Streamlit AppTest, headless).

Images are swapped for an instant in-memory fake so these tests never touch
the network; everything else (navigation, session state, widgets, callbacks,
fragments, the share-link handling) is the real code.
"""

import io
import time
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

import src.components as components
from src.images import Resolved

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), (200, 120, 60, 255)).save(buf, format="PNG")
    return buf.getvalue()


class InstantCache:
    """Stands in for the thumbnail cache: every image is 'already cached'."""

    image = _png()

    def prefetch(self, chains, budget):
        return 0

    def resolve(self, chain):
        return Resolved(self.image, chain[0] if chain else None, "ok")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(components, "get_thumbnail_cache", lambda: InstantCache())


def _app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def _html(at) -> str:
    return "".join(el.proto.body for el in at.get("html"))


# ------------------------------------------------ image polling vs. main run


class OnePendingForever(InstantCache):
    """One villager's image never arrives, so the grid polls until its cap.

    Like the real cache, prefetch blocks for its whole time budget when an image
    it was asked for never resolves, so any code that waits on images shows up
    in the timing.
    """

    def prefetch(self, chains, budget):
        if any(c and "Ace_NH_Villager_Icon" in c[0] for c in chains):
            time.sleep(budget)
            return 1
        return 0

    def resolve(self, chain):
        if chain and "Ace_NH_Villager_Icon" in chain[0]:
            return Resolved(None, None, "pending")
        return super().resolve(chain)


def test_a_stuck_image_never_holds_up_the_page(monkeypatch):
    # Regression guard for the "grey sidebar filters" investigation: whatever the
    # image cache is doing, the page's main run must finish promptly with every
    # sidebar widget in place, and the stuck image must show the loading leaf.
    monkeypatch.setattr(components, "get_thumbnail_cache", lambda: OnePendingForever())
    started = time.perf_counter()
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    elapsed = time.perf_counter() - started
    assert not at.exception, at.exception
    assert elapsed < components.MAX_POLL_SECONDS / 2, f"main run took {elapsed:.1f}s"
    assert at.text_input(key="browse_search") is not None
    assert at.toggle(key="browse_nh_only").value is True
    assert len(at.get("image")) >= 36  # every card drawn, Ace with the placeholder
    assert at.session_state["grid_polling"] is True  # still polling for Ace, without blocking


# ----------------------------------------------------- cross-view persistence


def test_compare_selection_persists_from_browse_card_to_compare_page():
    at = _app()  # lands on Browse
    at.button(key="compare-Ace").click().run()
    assert at.session_state[components.COMPARE_KEY] == ["Ace"]

    at.switch_page("views/match.py").run()
    assert at.session_state[components.COMPARE_KEY] == ["Ace"]  # survives a view change

    at.switch_page("views/compare.py").run()
    assert not at.exception, at.exception
    assert at.multiselect(key="compare_picker").value == ["Ace"]
    assert "Add at least one more" in " ".join(i.value for i in at.info)


def test_selection_count_is_visible_in_page_after_adding():
    at = _app()
    at.button(key="compare-Ace").click().run()
    at.button(key="compare-Admiral").click().run()
    links = [el.proto.label for el in at.get("page_link")]
    assert "Compare (2 selected)" in links  # in-page, so visible even with the nav folded away


def test_card_button_cannot_add_the_same_villager_twice():
    at = _app()
    at.button(key="compare-Ace").click().run()
    assert at.button(key="compare-Ace").label == "Comparing"
    at.button(key="compare-Ace").click().run()  # second click removes, never duplicates
    assert at.session_state[components.COMPARE_KEY] == []


def test_card_buttons_disable_at_the_cap():
    at = _app()
    at.session_state[components.COMPARE_KEY] = ["Raymond", "Marshal", "Judy", "Sherb"]
    at.run()
    ace = at.button(key="compare-Ace")
    assert ace.disabled and "up to 4" in (ace.help or "")


# ------------------------------------------------------------- compare page


def test_compare_page_shows_both_carmens_distinctly():
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state[components.COMPARE_KEY] = ["Carmen", "Carmen_(mouse)"]
    at.switch_page("views/compare.py").run()
    assert not at.exception, at.exception
    picker = at.multiselect(key="compare_picker")
    assert picker.value == ["Carmen", "Carmen_(mouse)"]  # selected by key, not by name
    page = _html(at)
    assert "Not in New Horizons" in page  # the mouse Carmen
    assert page.count('class="vx-chip"') == 2


def test_share_link_opens_the_comparison_and_ignores_unknown_entries():
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["v"] = "raymond,MARSHAL,not-a-villager"
    at.switch_page("views/compare.py").run()
    assert not at.exception, at.exception
    assert at.session_state[components.COMPARE_KEY] == ["Raymond", "Marshal"]
    assert "1 entry in the link" in " ".join(i.value for i in at.info)
    assert at.query_params["v"] == ["raymond,marshal"]  # rewritten to the canonical form
    assert "vx-same" in _html(at)  # Raymond and Marshal are both Smug


def test_hostile_share_link_text_is_never_rendered():
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["v"] = "<script>alert(1)</script>,<img src=x onerror=alert(1)>"
    at.switch_page("views/compare.py").run()
    assert not at.exception, at.exception
    assert at.session_state[components.COMPARE_KEY] == []
    rendered = _html(at) + " ".join(str(m.value) for m in at.markdown) + " ".join(i.value for i in at.info)
    assert "<script" not in rendered and "<img src=x" not in rendered and "alert(1)" not in rendered
    assert "2 entries in the link" in " ".join(i.value for i in at.info)


def test_empty_compare_offers_examples():
    at = AppTest.from_file(APP, default_timeout=30)
    at.switch_page("views/compare.py").run()
    assert "Nothing to compare yet" in " ".join(i.value for i in at.info)
    at.button(key="example-The two Carmens").click().run()
    assert at.session_state[components.COMPARE_KEY] == ["Carmen", "Carmen_(mouse)"]
