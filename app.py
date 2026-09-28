"""Villager Explorer: entry point and navigation.

Each view is a page under views/. They share one data layer (src/data.py) and
one filtering engine (src/filters.py). Navigation sits at the top so the
sidebar belongs to the current view's controls.
"""

import logging

import streamlit as st

# Our modules log through the root logger to stderr, which the host's log viewer
# captures (Streamlit's own loggers don't propagate here). No-op after first run.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

st.set_page_config(page_title="Villager Explorer", page_icon="🍃", layout="wide")

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
