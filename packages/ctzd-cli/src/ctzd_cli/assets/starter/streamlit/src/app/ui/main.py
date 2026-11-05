"""The dashboard page. `ctzd dev` runs it locally; the platform runs it with `streamlit run`."""

import streamlit as st

from app.data.loaders import load_demo
from app.logic.summary import with_share

st.set_page_config(page_title="{{title}}", layout="wide")
st.title("{{title}}")

frame = with_share(load_demo())
st.bar_chart(frame, x="category", y="value")
st.dataframe(frame, hide_index=True, width="stretch")
