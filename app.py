"""Villager Explorer: entry point and navigation.

Each view is a page under views/. They share one data layer (src/data.py) and
one filtering engine (src/filters.py). Navigation sits at the top so the
sidebar belongs to the current view's controls.
"""

import logging

import streamlit as st

from src.components import PAGE_SIZE, warm_images
from src.data import load_villagers
from src.filters import SORT_OPTIONS, Criteria, apply_criteria, sort_villagers

# Our modules log through the root logger to stderr, which the host's log viewer
# captures (Streamlit's own loggers don't propagate here). No-op after first run.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

st.set_page_config(page_title="Villager Explorer", page_icon="🍃", layout="wide")


@st.cache_resource(show_spinner=False)
def _warm_default_page() -> bool:
    """Once per server process: start fetching the landing page's images.

    Streamlit has no server-start hook, so this fires on the first run after a
    cold start or wake-up. It's still the earliest point, ahead of building the
    sidebar and grid, and the first visitor's grid finds the fetches under way.
    """
    landing = sort_villagers(apply_criteria(load_villagers(), Criteria()), SORT_OPTIONS[0])
    warm_images(landing.head(PAGE_SIZE))
    return True


_warm_default_page()

pages = [
    st.Page("views/browse.py", title="Browse", icon=":material/grid_view:", default=True),
    st.Page("views/diagnostics.py", title="Diagnostics", url_path="diagnostics", visibility="hidden"),
]
page = st.navigation(pages, position="top")
page.run()

st.sidebar.divider()
st.sidebar.caption(
    "Villager data and images from [Nookipedia](https://nookipedia.com) "
    "(CC BY-SA 3.0). Fan-made; not affiliated with or endorsed by Nintendo."
)
