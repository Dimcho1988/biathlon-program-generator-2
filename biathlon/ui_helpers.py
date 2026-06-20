"""Малки помощни функции за Streamlit интерфейса."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st


def inject_css() -> None:
    st.markdown(
        """
<style>
.block-container {padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1500px;}
[data-testid="stSidebar"] {min-width: 285px;}
.demo-banner {
    border: 1px solid rgba(180, 35, 35, 0.30);
    background: rgba(180, 35, 35, 0.06);
    border-radius: 12px;
    padding: 0.65rem 0.9rem;
    margin-bottom: 1rem;
    font-size: 0.92rem;
}
.hero-card {
    border: 1px solid rgba(49, 51, 63, 0.16);
    border-radius: 16px;
    padding: 1rem 1.15rem;
    background: rgba(250, 250, 250, 0.42);
    margin-bottom: 0.8rem;
}
.reason-box {
    border-left: 4px solid #9c2f2f;
    padding: 0.7rem 0.9rem;
    background: rgba(156, 47, 47, 0.06);
    border-radius: 6px;
    margin: 0.35rem 0 0.8rem 0;
}
.soft-box {
    border: 1px solid rgba(49, 51, 63, 0.12);
    border-radius: 12px;
    padding: 0.75rem 0.9rem;
    margin-bottom: 0.65rem;
}
.small-muted {font-size: 0.84rem; opacity: 0.75;}
.day-card {
    border: 1px solid rgba(49, 51, 63, 0.13);
    border-radius: 14px;
    padding: 0.8rem 0.95rem;
    margin-bottom: 0.7rem;
}
</style>
""",
        unsafe_allow_html=True,
    )


def demo_banner(version: int) -> None:
    st.markdown(
        f'<div class="demo-banner"><b>ДЕМО РЕЖИМ</b> · синтетични и ръчно променяеми данни · без Strava · версия на данните <b>{version}</b></div>',
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str | None = None) -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def status_badge(status: str, hard_flag: bool = False) -> None:
    if hard_flag:
        st.badge(status, icon=":material/warning:", color="red")
    elif "Готов" in status:
        st.badge(status, icon=":material/check_circle:", color="green")
    elif "Умерена" in status:
        st.badge(status, icon=":material/info:", color="blue")
    else:
        st.badge(status, icon=":material/monitor_heart:", color="orange")


def dataframe_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def audit_entry(action: str, athlete_id: str | None, reason: str, version: int) -> dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "athlete_id": athlete_id or "—",
        "reason": reason,
        "data_version": version,
    }
