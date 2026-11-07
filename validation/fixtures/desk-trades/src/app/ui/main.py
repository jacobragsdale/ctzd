"""The dashboard page."""

import streamlit as st

from app.data.loaders import load_trades
from app.logic.summary import notional_by_desk

st.set_page_config(page_title="Desk Trades", layout="wide")
st.title("Desk Trades")

trades = load_trades()
st.bar_chart(notional_by_desk(trades), x="desk", y="notional", y_label="Notional traded")
st.dataframe(trades, hide_index=True, width="stretch")
