"""The dashboard page."""

import streamlit as st

from app.data.loaders import load_latest_prices
from app.logic.summary import price_by_sector

st.set_page_config(page_title="Sector Prices", layout="wide")
st.title("Sector Prices")

summary = price_by_sector(load_latest_prices())
st.bar_chart(summary, x="sector", y="clean_price", y_label="Average clean price (per 100)")
st.dataframe(summary, hide_index=True, width="stretch")
