"""Streamlit metrics dashboard: `streamlit run dashboard.py` (dev) or Docker + nginx `/dashboard/`."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit.errors import StreamlitAPIException

try:
    st.set_page_config(
        page_title="Inbound Carrier Sales – Control Room",
        layout="wide",
    )
except StreamlitAPIException:
    pass

API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API_METRICS_PATH = os.environ.get("API_METRICS_PATH", "/v1/metrics")
if not API_METRICS_PATH.startswith("/"):
    API_METRICS_PATH = "/" + API_METRICS_PATH
DEFAULT_KEY = os.environ.get("API_KEY", "").strip()

# Zauber-style palette (muted, cohesive)
ACCENT_BLUE = "#2456A6"
SECONDARY_BEIGE = "#F5E9DA"
TEXT_PRIMARY = "#1E1E1E"
TEXT_MUTED = "#7A7A7A"
CARD_BG = "#F0EBE3"
OUTCOME_COLORS = ["#2456A6", "#6C85B5", "#B0BDD5", "#D4D8E5"]
SENTIMENT_COLORS = ["#2456A6", "#E0C7A0", "#C2C2C2"]
TOP_BAR_COLORS = ["#2456A6", "#6C85B5", "#8FA3C4", "#B0BDD5", "#C8D0E0", "#D4D8E5", "#E2E5EE", "#EBEDF4"]


def _zauber_css() -> None:
    st.markdown(
        f"""
<style>
  html, body, .stApp {{
    font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif !important;
    font-size: 15px;
    color: {TEXT_PRIMARY};
  }}
  .block-container {{
    padding-top: 1.25rem;
    padding-bottom: 3rem;
    max-width: 1200px;
  }}
  div[data-testid="stMetric"],
  div[data-testid="metric-container"] {{
    background-color: {CARD_BG} !important;
    border-radius: 12px !important;
    padding: 16px 20px !important;
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08) !important;
    border: 1px solid rgba(36, 86, 166, 0.06) !important;
  }}
  div[data-testid="stMetric"] label,
  div[data-testid="metric-container"] label {{
    color: {TEXT_MUTED} !important;
  }}
  div[data-testid="stMetric"] [data-testid="stMetricValue"],
  div[data-testid="metric-container"] [data-testid="stMetricValue"] {{
    color: {TEXT_PRIMARY} !important;
  }}
  .zauber-hero {{
    margin-bottom: 2rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid rgba(30, 30, 30, 0.06);
  }}
  .zauber-hero h1 {{
    font-family: "Inter", system-ui, sans-serif;
    font-weight: 600;
    font-size: 1.65rem;
    letter-spacing: -0.02em;
    color: {TEXT_PRIMARY};
    margin: 0 0 0.5rem 0;
    line-height: 1.25;
  }}
  .zauber-hero p {{
    font-family: "Inter", system-ui, sans-serif;
    font-size: 0.95rem;
    line-height: 1.55;
    color: {TEXT_MUTED};
    margin: 0;
    max-width: 42rem;
  }}
  .zauber-section-title {{
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {TEXT_MUTED};
    margin: 1.75rem 0 0.75rem 0;
  }}
  .zauber-window-label {{
    font-size: 1.05rem;
    font-weight: 600;
    color: {TEXT_PRIMARY};
    margin: 0 0 1rem 0;
  }}
  .zauber-footer {{
    font-size: 0.78rem;
    color: {TEXT_MUTED};
    text-align: center;
    margin-top: 3rem;
    padding-top: 1.5rem;
    border-top: 1px solid rgba(30, 30, 30, 0.06);
  }}
  .zauber-caption {{
    font-size: 0.82rem;
    color: {TEXT_MUTED};
    margin-top: 0.35rem;
  }}
  div[data-testid="stSidebar"] {{
    background-color: {SECONDARY_BEIGE};
  }}
</style>
""",
        unsafe_allow_html=True,
    )


def _hero() -> None:
    st.markdown(
        f"""
<div class="zauber-hero">
  <h1>Inbound Carrier Sales – Control Room</h1>
  <p>Quiet overview of voice-channel outcomes: booking yield, negotiation load, and sentiment mix
  across recent activity windows. Data refreshes from your metrics API.</p>
</div>
""",
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=30)
def load_metrics(api_base: str, api_key: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(f"{api_base}{API_METRICS_PATH}", headers={"X-API-Key": api_key})
        r.raise_for_status()
        return r.json()


def _positive_sentiment_pct(data: dict[str, Any]) -> float:
    s = data.get("sentiment_breakdown") or {}
    pos = int(s.get("positive", 0))
    neu = int(s.get("neutral", 0))
    neg = int(s.get("negative", 0))
    total = pos + neu + neg
    return round(100.0 * pos / total, 1) if total else 0.0


def _apply_zauber_layout(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            family="Inter, system-ui, sans-serif",
            color=TEXT_PRIMARY,
            size=13,
        ),
        title=dict(font=dict(size=15, color=TEXT_PRIMARY)),
        margin=dict(t=48, b=48, l=48, r=32),
        legend=dict(
            bgcolor="rgba(247,245,242,0.92)",
            bordercolor="rgba(30,30,30,0.06)",
            borderwidth=1,
        ),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor="#E8E4DC", tickfont=dict(color=TEXT_MUTED))
    fig.update_yaxes(showgrid=False, zeroline=False, linecolor="#E8E4DC", tickfont=dict(color=TEXT_MUTED))
    return fig


def _kpi_row(data: dict[str, Any], block_key: str) -> None:
    total = int(data.get("total_calls", 0) or 0)
    booked = float(data.get("booked_pct", 0.0) or 0.0)
    rounds = float(data.get("avg_negotiation_rounds", 0.0) or 0.0)
    pos_pct = _positive_sentiment_pct(data)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total calls", f"{total:,}")
    with c2:
        st.metric("Booked %", f"{booked}%")
    with c3:
        st.metric("Avg negotiation rounds", f"{rounds}")
    with c4:
        st.metric("Positive sentiment %", f"{pos_pct}%")
    prem = data.get("avg_rate_premium_pct")
    prem_note = f"Avg rate premium (booked): {prem:.2f}%" if prem is not None else "Avg rate premium (booked): —"
    st.markdown(f'<p class="zauber-caption">{prem_note}</p>', unsafe_allow_html=True)


def _charts_for_window(title: str, data: dict[str, Any], block_key: str) -> None:
    st.markdown(f'<p class="zauber-window-label">{title}</p>', unsafe_allow_html=True)
    _kpi_row(data, block_key)

    sent = data.get("sentiment_breakdown") or {}
    order = ["positive", "neutral", "negative"]
    names = [k for k in order if k in sent]
    values = [int(sent[k]) for k in names]
    if sum(values) > 0:
        df_s = pd.DataFrame({"sentiment": names, "count": values})
        fig_pie = px.pie(
            df_s,
            names="sentiment",
            values="count",
            title="Sentiment mix",
            color="sentiment",
            color_discrete_map={
                "positive": SENTIMENT_COLORS[0],
                "neutral": SENTIMENT_COLORS[1],
                "negative": SENTIMENT_COLORS[2],
            },
            category_orders={"sentiment": names},
        )
        fig_pie.update_traces(
            textposition="inside",
            textinfo="percent+label",
            marker=dict(line=dict(color="#F7F5F2", width=1)),
        )
        _apply_zauber_layout(fig_pie)
        st.plotly_chart(fig_pie, width="stretch", key=f"{block_key}_sentiment_pie")
    else:
        st.info("No sentiment data in this window.")

    outcomes = data.get("outcomes_breakdown") or {}
    if outcomes:
        oc = sorted(outcomes.items(), key=lambda x: -x[1])
        df_o = pd.DataFrame(oc, columns=["outcome", "count"])
        cmap = {row["outcome"]: OUTCOME_COLORS[i % len(OUTCOME_COLORS)] for i, row in df_o.iterrows()}
        fig_bar = px.bar(
            df_o,
            x="outcome",
            y="count",
            title="Outcomes",
            color="outcome",
            color_discrete_map=cmap,
        )
        fig_bar.update_traces(marker_line_width=0)
        fig_bar.update_layout(showlegend=False)
        _apply_zauber_layout(fig_bar)
        st.plotly_chart(fig_bar, width="stretch", key=f"{block_key}_outcomes_bar")
    else:
        st.info("No outcomes in this window.")

    st.markdown('<p class="zauber-section-title">Top performers</p>', unsafe_allow_html=True)
    lc, rc = st.columns(2)
    top_loads = data.get("top_loads_by_bookings") or []
    top_mcs = data.get("top_mcs_by_bookings") or []
    with lc:
        st.markdown(f'<span style="color:{TEXT_MUTED};font-size:0.9rem;font-weight:500;">Loads by bookings</span>', unsafe_allow_html=True)
        if top_loads:
            df_l = pd.DataFrame(top_loads)
            st.dataframe(df_l, width="stretch", hide_index=True, key=f"{block_key}_df_loads")
            ldf = df_l.head(10)
            cmap_l = {row["load_id"]: TOP_BAR_COLORS[i % len(TOP_BAR_COLORS)] for i, row in ldf.iterrows()}
            fig_l = px.bar(
                ldf,
                x="load_id",
                y="bookings",
                title="Top loads",
                color="load_id",
                color_discrete_map=cmap_l,
            )
            fig_l.update_layout(showlegend=False)
            _apply_zauber_layout(fig_l)
            st.plotly_chart(fig_l, width="stretch", key=f"{block_key}_loads_bar")
        else:
            st.caption("None yet")
    with rc:
        st.markdown(f'<span style="color:{TEXT_MUTED};font-size:0.9rem;font-weight:500;">MCs by bookings</span>', unsafe_allow_html=True)
        if top_mcs:
            df_m = pd.DataFrame(top_mcs)
            st.dataframe(df_m, width="stretch", hide_index=True, key=f"{block_key}_df_mcs")
            mdf = df_m.head(10)
            cmap_m = {row["mc"]: TOP_BAR_COLORS[i % len(TOP_BAR_COLORS)] for i, row in mdf.iterrows()}
            fig_m = px.bar(
                mdf,
                x="mc",
                y="bookings",
                title="Top MCs",
                color="mc",
                color_discrete_map=cmap_m,
            )
            fig_m.update_layout(showlegend=False)
            _apply_zauber_layout(fig_m)
            st.plotly_chart(fig_m, width="stretch", key=f"{block_key}_mcs_bar")
        else:
            st.caption("None yet")


def main() -> None:
    _zauber_css()
    _hero()

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

    st.markdown(
        f'<p class="zauber-caption">Last generated <strong style="color:{TEXT_PRIMARY};">{bundle.get("generated_at", "")}</strong>'
        f" · Source <strong style=\"color:{TEXT_PRIMARY};\">{API_BASE}{API_METRICS_PATH}</strong></p>",
        unsafe_allow_html=True,
    )

    col_a, col_b = st.columns(2, gap="large")
    with col_a:
        _charts_for_window("Last 24 hours", bundle.get("last_24h") or {}, "24h")
    with col_b:
        _charts_for_window("Last 7 days", bundle.get("last_7d") or {}, "7d")

    st.markdown(
        '<p class="zauber-footer">Inbound carrier automation metrics · Acme Logistics · Powered by HappyRobot</p>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
