"""Streamlit metrics dashboard: `streamlit run dashboard.py` (dev) or Docker + nginx `/dashboard/`."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_KEY = os.environ.get("API_KEY", "").strip()


@st.cache_data(ttl=30)
def load_metrics(api_base: str, api_key: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(f"{api_base}/metrics", headers={"X-API-Key": api_key})
        r.raise_for_status()
        return r.json()


def _metric_block(title: str, data: dict[str, Any], block_key: str) -> None:
    st.subheader(title)
    c1, c2, c3, c4 = st.columns(4)
    total = data.get("total_calls", 0)
    booked = data.get("booked_pct", 0.0)
    rounds = data.get("avg_negotiation_rounds", 0.0)
    prem = data.get("avg_rate_premium_pct")
    prem_s = f"{prem:.2f}%" if prem is not None else "N/A"
    with c1:
        st.metric("Total calls", total)
    with c2:
        st.metric("Booked %", f"{booked}%")
    with c3:
        st.metric("Avg negotiation rounds", rounds)
    with c4:
        st.metric("Avg rate premium (booked)", prem_s)

    sent = data.get("sentiment_breakdown") or {}
    names = list(sent.keys())
    values = [int(sent[k]) for k in names]
    if sum(values) > 0:
        fig_pie = px.pie(names=names, values=values, title=f"Sentiment — {title}")
        st.plotly_chart(fig_pie, width="stretch", key=f"{block_key}_sentiment_pie")
    else:
        st.info("No sentiment data in this window.")

    outcomes = data.get("outcomes_breakdown") or {}
    if outcomes:
        oc = sorted(outcomes.items(), key=lambda x: -x[1])
        df_o = pd.DataFrame(oc, columns=["outcome", "count"])
        fig_bar = px.bar(df_o, x="outcome", y="count", title=f"Outcomes — {title}")
        st.plotly_chart(fig_bar, width="stretch", key=f"{block_key}_outcomes_bar")
    else:
        st.info("No outcomes in this window.")

    top_loads = data.get("top_loads_by_bookings") or []
    top_mcs = data.get("top_mcs_by_bookings") or []
    lc, rc = st.columns(2)
    with lc:
        st.markdown("**Top loads by bookings**")
        if top_loads:
            df_l = pd.DataFrame(top_loads)
            st.dataframe(df_l, width="stretch", hide_index=True, key=f"{block_key}_df_loads")
            fig_l = px.bar(df_l.head(10), x="load_id", y="bookings", title="Top loads")
            st.plotly_chart(fig_l, width="stretch", key=f"{block_key}_loads_bar")
        else:
            st.caption("None")
    with rc:
        st.markdown("**Top MCs by bookings**")
        if top_mcs:
            df_m = pd.DataFrame(top_mcs)
            st.dataframe(df_m, width="stretch", hide_index=True, key=f"{block_key}_df_mcs")
            fig_m = px.bar(df_m.head(10), x="mc", y="bookings", title="Top MCs")
            st.plotly_chart(fig_m, width="stretch", key=f"{block_key}_mcs_bar")
        else:
            st.caption("None")


def main() -> None:
    st.set_page_config(page_title="Call metrics", layout="wide")
    st.title("Freight call metrics")

    if not DEFAULT_KEY:
        api_key = st.sidebar.text_input("X-API-Key", type="password", help="Must match the API service API_KEY")
    else:
        api_key = DEFAULT_KEY
        st.sidebar.caption("Using API_KEY from environment.")

    if st.sidebar.button("Refresh"):
        load_metrics.clear()
        st.rerun()

    if not api_key:
        st.warning("Set API_KEY in the environment or enter a key in the sidebar.")
        return

    try:
        bundle = load_metrics(API_BASE, api_key)
    except httpx.HTTPStatusError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text}")
        return
    except httpx.RequestError as e:
        st.error(f"Could not reach API at {API_BASE}: {e}")
        return

    st.caption(f"Generated at: {bundle.get('generated_at', '')} · API: {API_BASE}")

    col_a, col_b = st.columns(2)
    with col_a:
        _metric_block("Last 24 hours", bundle.get("last_24h") or {}, "24h")
    with col_b:
        _metric_block("Last 7 days", bundle.get("last_7d") or {}, "7d")


if __name__ == "__main__":
    main()
