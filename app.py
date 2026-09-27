"""Villager Explorer: entry point and navigation.

Each view is a page under views/. They share one data layer (src/data.py) and
one filtering engine (src/filters.py). Navigation sits at the top so the
sidebar belongs to the current view's controls.
"""

import streamlit as st

st.set_page_config(page_title="Villager Explorer", page_icon="🍃", layout="wide")

pages = [
    st.Page("views/browse.py", title="Browse", icon=":material/grid_view:", default=True),
]
page = st.navigation(pages, position="top")
page.run()

st.sidebar.divider()
st.sidebar.caption(
    "Villager data and images from [Nookipedia](https://nookipedia.com) "
    "(CC BY-SA 3.0). Fan-made; not affiliated with or endorsed by Nintendo."
)
