"""Villager Explorer: entry point.

Stage 1 skeleton: proves the environment runs and the villager data loads.
Navigation between the Browse / Match / Compare views arrives in later stages.
"""

import streamlit as st

from src.data import data_report, load_villagers

st.set_page_config(page_title="Villager Explorer", page_icon="🍃", layout="wide")

villagers = load_villagers()
report = data_report(villagers)

st.title("Villager Explorer")
st.success(
    f"Loaded {report.villagers} villagers "
    f"({report.in_new_horizons} in New Horizons, {report.islanders} islanders)."
)

left, right = st.columns(2)
with left:
    st.subheader(f"Columns ({villagers.shape[1]})")
    st.dataframe(
        {"column": villagers.columns, "dtype": villagers.dtypes.astype(str).to_list()},
        hide_index=True,
    )
with right:
    st.subheader("Missing values")
    st.caption("Hobby, styles, colours, song and icon exist only for New Horizons villagers.")
    st.dataframe(
        {"column": list(report.missing), "missing": list(report.missing.values())},
        hide_index=True,
    )

st.subheader("Sample")
st.dataframe(villagers.head(10))

st.caption(
    "Villager data from [Nookipedia](https://nookipedia.com) (CC BY-SA 3.0). "
    "Fan-made; not affiliated with or endorsed by Nintendo."
)
