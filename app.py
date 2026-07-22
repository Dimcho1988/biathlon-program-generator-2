from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from biathlon.charts import (
    activity_stream_figure,
    annual_goal_figure,
    calendar_timeline_figure,
    effective_load_figure,
    index_7_40_figure,
    monitoring_history_figure,
    plan_comparison_figure,
    readiness_figure,
    real_vs_equivalent_figure,
    strength_breakdown_figure,
    test_history_figure,
    weekly_plan_vs_actual_figure,
    weekly_targets_figure,
)
from biathlon.constants import (
    AEROBIC_COMPONENTS,
    COMPONENT_LABELS,
    COMPONENT_SHORT,
    COMPONENTS,
    EDIT_ROLES,
    EXPERT_ROLES,
    METRIC_DEFINITIONS,
    ROLE_LABELS,
    STRENGTH_COEFFICIENTS,
    STRENGTH_LABELS,
    STRENGTH_TYPES,
    TEST_DEFINITIONS,
)
from biathlon.demo_data import DEMO_SEED, generate_activity_stream, generate_demo_bundle
from biathlon.explanations import EXPLANATIONS, explanation_titles, help_text
from biathlon.physiology import analyze_activity_stream, strength_equivalent_minutes
from biathlon.preferences import (
    EVENT_TYPE_LABELS,
    WEEKDAY_BY_LABEL,
    WEEKDAY_LABELS,
    build_week_structure,
    daily_history_from_activities,
    daily_table_to_activities,
    history_template,
    normalize_preferences,
    weekly_totals_to_activities,
)
from biathlon.reporting import work_report_xlsx_bytes
from biathlon.service import analyze_athlete, team_summary
from biathlon.ui_helpers import (
    audit_entry,
    dataframe_csv_bytes,
    demo_banner,
    inject_css,
    json_bytes,
    page_header,
    status_badge,
)

st.set_page_config(
    page_title="Biathlon LoadLab Â· MVP 0.5",
    page_icon="ðŸŽ¯",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_css()

PAGES = {
    "team": "ÐžÑ‚Ð±Ð¾Ñ€Ð½Ð¾ Ñ‚Ð°Ð±Ð»Ð¾",
    "dashboard": "Ð¡Ð¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚ Â· Ð¿Ñ€ÐµÐ³Ð»ÐµÐ´",
    "load": "ÐÐ°Ñ‚Ð¾Ð²Ð°Ñ€Ð²Ð°Ð½Ðµ Ð¸ 7/40",
    "recovery": "Ð’ÑŠÐ·ÑÑ‚Ð°Ð½Ð¾Ð²ÑÐ²Ð°Ð½Ðµ",
    "plan": "ÐÐ´Ð°Ð¿Ñ‚Ð¸Ð²Ð½Ð° Ð¿Ñ€Ð¾Ð³Ñ€Ð°Ð¼Ð°",
    "calendar": "ÐšÐ°Ð»ÐµÐ½Ð´Ð°Ñ€ Ð¸ Ñ†ÐµÐ»Ð¸",
    "history": "Ð˜ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð¸ Ð½Ð°Ñ‡Ð°Ð»Ð½Ð¸ Ð´Ð°Ð½Ð½Ð¸",
    "monitoring": "Ð”Ð½ÐµÐ²ÐµÐ½ Ð¼Ð¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³",
    "tests": "ÐšÐ¾Ð½Ñ‚Ñ€Ð¾Ð»Ð½Ð¸ Ñ‚ÐµÑÑ‚Ð¾Ð²Ðµ",
    "simulator": "Ð¡Ð¸Ð¼ÑƒÐ»Ð°Ñ‚Ð¾Ñ€ â€žÐšÐ°ÐºÐ²Ð¾ Ð°ÐºÐ¾â€œ",
    "profile": "ÐŸÑ€Ð¾Ñ„Ð¸Ð» Ð¸ Ð·Ð¾Ð½Ð¸",
    "models": "ÐœÐ¾Ð´ÐµÐ»Ð¸ Ð¸ Ð¾Ð±ÑÑÐ½ÐµÐ½Ð¸Ñ",
    "settings": "Ð•ÐºÑÐ¿ÐµÑ€Ñ‚Ð½Ð¸ Ð½Ð°ÑÑ‚Ñ€Ð¾Ð¹ÐºÐ¸",
}

PAGE_ICONS = {
    "team": "ðŸ‘¥",
    "dashboard": "ðŸ",
    "load": "ðŸ“ˆ",
    "recovery": "ðŸ”„",
    "plan": "ðŸ“…",
    "calendar": "ðŸ—“ï¸",
    "history": "ðŸ§¾",
    "monitoring": "ðŸ«€",
    "tests": "ðŸ§ª",
    "simulator": "ðŸ§­",
    "profile": "ðŸ‘¤",
    "models": "â“",
    "settings": "âš™ï¸",
}


def initialize_state() -> None:
    if "bundle" not in st.session_state:
        st.session_state.bundle = generate_demo_bundle(seed=DEMO_SEED, history_days=150)
    if "role" not in st.session_state:
        st.session_state.role = "Ð“Ð»Ð°Ð²ÐµÐ½ Ñ‚Ñ€ÐµÐ½ÑŒÐ¾Ñ€"
    if "athlete_id" not in st.session_state:
        st.session_state.athlete_id = "A"
    if "nav_page" not in st.session_state:
        st.session_state.nav_page = "team"
    if "flash" not in st.session_state:
        st.session_state.flash = None


def commit_bundle(bundle: dict[str, Any], action: str, reason: str, athlete_id: str | None = None) -> None:
    bundle["version"] = int(bundle.get("version", 1)) + 1
    bundle.setdefault("audit_log", []).append(audit_entry(action, athlete_id, reason, bundle["version"]))
    st.session_state.bundle = bundle
    st.session_state.flash = ("success", reason)
    st.rerun()


def render_flash() -> None:
    flash = st.session_state.get("flash")
    if not flash:
        return
    kind, text = flash
    if kind == "success":
        st.success(text)
    elif kind == "warning":
        st.warning(text)
    else:
        st.info(text)
    st.session_state.flash = None


def sync_navigation(bundle: dict[str, Any]) -> tuple[str, str, str]:
    requested_page = str(st.query_params.get("page", "team"))
    if requested_page not in PAGES:
        requested_page = "team"
    if st.session_state.get("_last_query_page") != requested_page:
        st.session_state.nav_page = requested_page
        st.session_state._last_query_page = requested_page

    requested_athlete = str(st.query_params.get("athlete", st.session_state.athlete_id))
    valid_athletes = bundle["athletes"]["athlete_id"].astype(str).tolist()
    if requested_athlete in valid_athletes and st.session_state.get("_last_query_athlete") != requested_athlete:
        st.session_state.athlete_id = requested_athlete
        st.session_state._last_query_athlete = requested_athlete

    role = st.sidebar.selectbox("Ð Ð¾Ð»Ñ", ROLE_LABELS, key="role", help="Ð Ð¾Ð»ÑÑ‚Ð° Ð¿Ñ€Ð¾Ð¼ÐµÐ½Ñ Ð¿Ñ€Ð°Ð²Ð¾Ñ‚Ð¾ Ð·Ð° Ñ€ÐµÐ´Ð°ÐºÑ†Ð¸Ñ. ÐÐ°Ð±Ð»ÑŽÐ´Ð°Ñ‚ÐµÐ»ÑÑ‚ Ñ€Ð°Ð±Ð¾Ñ‚Ð¸ ÑÐ°Ð¼Ð¾ Ð² Ñ€ÐµÐ¶Ð¸Ð¼ Ð¿Ñ€ÐµÐ³Ð»ÐµÐ´.")

    name_map = bundle["athletes"].set_index("athlete_id")["name"].to_dict()
    athlete_id = st.sidebar.selectbox(
        "Ð¡Ð¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚",
        valid_athletes,
        key="athlete_id",
        format_func=lambda value: f"{value} Â· {name_map[value]}",
    )

    page = st.sidebar.radio(
        "ÐÐ°Ð²Ð¸Ð³Ð°Ñ†Ð¸Ñ",
        list(PAGES),
        key="nav_page",
        format_func=lambda code: f"{PAGE_ICONS[code]}  {PAGES[code]}",
    )

    if str(st.query_params.get("page", "")) != page:
        st.query_params["page"] = page
    if str(st.query_params.get("athlete", "")) != athlete_id:
        st.query_params["athlete"] = athlete_id
    st.session_state._last_query_page = page
    st.session_state._last_query_athlete = athlete_id

    st.sidebar.divider()
    st.sidebar.caption(f"Ð’ÐµÑ€ÑÐ¸Ñ Ð½Ð° Ð´Ð°Ð½Ð½Ð¸Ñ‚Ðµ: {bundle['version']} Â· seed: {bundle['seed']}")
    st.sidebar.caption("Ð ÐµÑˆÐµÐ½Ð¸ÑÑ‚Ð° ÑÐ° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÑŠÑ‡Ð½Ð° Ð¿Ð¾Ð´ÐºÑ€ÐµÐ¿Ð°, Ð½Ðµ Ð¼ÐµÐ´Ð¸Ñ†Ð¸Ð½ÑÐºÐ° Ð´Ð¸Ð°Ð³Ð½Ð¾Ð·Ð°.")
    with st.sidebar.expander("Ð£Ð¿Ñ€Ð°Ð²Ð»ÐµÐ½Ð¸Ðµ Ð½Ð° Ð´ÐµÐ¼Ð¾Ñ‚Ð¾"):
        confirmed = st.checkbox("ÐŸÐ¾Ñ‚Ð²ÑŠÑ€Ð¶Ð´Ð°Ð²Ð°Ð¼ Ð½ÑƒÐ»Ð¸Ñ€Ð°Ð½Ðµ", key="reset_confirm")
        if st.button("ÐÑƒÐ»Ð¸Ñ€Ð°Ð¹ Ð²ÑÐ¸Ñ‡ÐºÐ¸ Ñ‚ÐµÑÑ‚Ð¾Ð²Ð¸ Ð´Ð°Ð½Ð½Ð¸", disabled=not confirmed, width="stretch"):
            st.session_state.bundle = generate_demo_bundle(seed=DEMO_SEED, history_days=150)
            st.session_state.flash = ("success", "Ð”ÐµÐ¼Ð¾Ñ‚Ð¾ Ðµ Ð²ÑŠÑ€Ð½Ð°Ñ‚Ð¾ ÐºÑŠÐ¼ Ð½Ð°Ñ‡Ð°Ð»Ð½Ð¸Ñ Ð¿Ð¾Ð²Ñ‚Ð¾Ñ€ÑÐµÐ¼ ÑÑ†ÐµÐ½Ð°Ñ€Ð¸Ð¹.")
            st.rerun()
    return page, athlete_id, role


def render_team_page(bundle: dict[str, Any]) -> None:
    page_header("ÐžÑ‚Ð±Ð¾Ñ€Ð½Ð¾ Ñ‚Ð°Ð±Ð»Ð¾", "Ð¢Ñ€Ð¸ Ñ€Ð°Ð·Ð»Ð¸Ñ‡Ð½Ð¸ Ð´ÐµÐ¼Ð¾Ð½ÑÑ‚Ñ€Ð°Ñ†Ð¸Ð¾Ð½Ð½Ð¸ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»Ð° Ñ ÐµÐ´Ð½Ð°ÐºÐ²Ð° ÐºÐ°Ð»ÐµÐ½Ð´Ð°Ñ€Ð½Ð° Ñ†ÐµÐ» Ð¸ Ñ€Ð°Ð·Ð»Ð¸Ñ‡Ð½Ð° Ð²ÑŠÑ‚Ñ€ÐµÑˆÐ½Ð° Ñ€ÐµÐ°ÐºÑ†Ð¸Ñ.")
    summary = team_summary(bundle)
    mean_readiness = float(summary["Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚"].mean())
    flags = int((summary["Ð¢Ð²ÑŠÑ€Ð´ Ñ„Ð»Ð°Ð³"] == "Ð”Ð°").sum())
    main_events = bundle["calendar"].loc[
        (bundle["calendar"]["type"] == "MAIN_RACE")
        & (pd.to_datetime(bundle["calendar"]["start_date"]).dt.normalize() >= pd.Timestamp.today().normalize())
    ].copy()
    days_to_main = None
    if not main_events.empty:
        days_to_main = int((pd.to_datetime(main_events["start_date"]).min().date() - date.today()).days)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ð¡Ð¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚Ð¸", len(summary))
    c2.metric("Ð¡Ñ€ÐµÐ´Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚", f"{mean_readiness:.0f}/100", help=help_text("integrated_readiness"))
    c3.metric("Ð¢Ð²ÑŠÑ€Ð´Ð¸ Ñ„Ð»Ð°Ð³Ð¾Ð²Ðµ", flags, help=help_text("monitoring"))
    c4.metric(
        "Ð”Ð¾ Ð¾ÑÐ½Ð¾Ð²Ð½Ð¸Ñ ÑÑ‚Ð°Ñ€Ñ‚",
        f"{days_to_main} Ð´Ð½Ð¸" if days_to_main is not None else "ÐÐµ Ðµ Ð·Ð°Ð´Ð°Ð´ÐµÐ½",
        help=help_text("periodization"),
    )

    display = summary.drop(columns=["athlete_id"]).copy()
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚": st.column_config.ProgressColumn(
                "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚",
                help=help_text("integrated_readiness"),
                min_value=0,
                max_value=100,
                format="%.1f",
            )
        },
    )

    with st.expander("ðŸ“¥ Excel Ð¾Ñ‚Ñ‡ÐµÑ‚ Ð·Ð° Ð¸Ð·Ð²ÑŠÑ€ÑˆÐµÐ½Ð°Ñ‚Ð° Ñ€Ð°Ð±Ð¾Ñ‚Ð°", expanded=True):
        st.caption(
            "Ð£Ð¿Ñ€Ð°Ð²Ð»ÐµÐ½ÑÐºÐ¸ Ð¾Ñ‚Ñ‡ÐµÑ‚ Ð·Ð° Ð¸Ð·Ð±Ñ€Ð°Ð½ Ð¿ÐµÑ€Ð¸Ð¾Ð´: Ð¾Ð±ÐµÐ¼ Ð¿Ð¾ Ð·Ð¾Ð½Ð¸, ÐµÑ„ÐµÐºÑ‚Ð¸Ð²ÐµÐ½ Ñ‚Ð¾Ð²Ð°Ñ€, "
            "7/40, Tref, readiness, Ð¾ÑÑ‚Ð°Ñ‚ÑŠÑ‡Ð½Ð° ÑƒÐ¼Ð¾Ñ€Ð° Ð¸ ÑÐ¿Ð¸ÑÑŠÐº Ð½Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ¸Ñ‚Ðµ."
        )
        default_end = date.today()
        default_start = default_end - timedelta(days=29)
        r1, r2 = st.columns(2)
        report_start = r1.date_input("ÐÐ°Ñ‡Ð°Ð»Ð¾ Ð½Ð° Ð¾Ñ‚Ñ‡ÐµÑ‚Ð½Ð¸Ñ Ð¿ÐµÑ€Ð¸Ð¾Ð´", value=default_start, key="work_report_start")
        report_end = r2.date_input("ÐšÑ€Ð°Ð¹ Ð½Ð° Ð¾Ñ‚Ñ‡ÐµÑ‚Ð½Ð¸Ñ Ð¿ÐµÑ€Ð¸Ð¾Ð´", value=default_end, key="work_report_end")
        if report_end < report_start:
            st.error("ÐšÑ€Ð°Ð¹Ð½Ð°Ñ‚Ð° Ð´Ð°Ñ‚Ð° Ð½Ðµ Ð¼Ð¾Ð¶Ðµ Ð´Ð° Ð±ÑŠÐ´Ðµ Ð¿Ñ€ÐµÐ´Ð¸ Ð½Ð°Ñ‡Ð°Ð»Ð½Ð°Ñ‚Ð° Ð´Ð°Ñ‚Ð°.")
        else:
            report_bytes = work_report_xlsx_bytes(bundle, report_start, report_end)
            st.download_button(
                "Ð˜Ð·Ñ‚ÐµÐ³Ð»Ð¸ Excel Ð¾Ñ‚Ñ‡ÐµÑ‚",
                report_bytes,
                file_name=f"biathlon_work_report_{report_start.isoformat()}_{report_end.isoformat()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )

    st.subheader("Ð˜Ð½Ð´Ð¸Ð²Ð¸Ð´ÑƒÐ°Ð»Ð¸Ð·Ð°Ñ†Ð¸Ñ Ð½Ð° ÐµÐ´Ð½Ð°ÐºÑŠÐ² ÐºÐ°Ð»ÐµÐ½Ð´Ð°Ñ€")
    cols = st.columns(3)
    for col, (_, row) in zip(cols, summary.iterrows()):
        athlete_id = str(row["athlete_id"])
        athlete = bundle["athletes"].loc[bundle["athletes"]["athlete_id"] == athlete_id].iloc[0]
        with col:
            st.markdown(
                f"""
<div class="hero-card">
<h4>{athlete['name']}</h4>
<p><b>{athlete['profile_name']}</b></p>
<p>Ð“Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚: <b>{row['Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚']:.0f}/100</b><br>
Ð¡Ñ‚Ð°Ñ‚ÑƒÑ: {row['Ð¡Ñ‚Ð°Ñ‚ÑƒÑ']}<br>
ÐÐ°Ð¹-ÑÐ»Ð°Ð± ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚: {row['ÐÐ°Ð¹-ÑÐ»Ð°Ð± ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚']}</p>
</div>
""",
                unsafe_allow_html=True,
            )
            if st.button("ÐžÑ‚Ð²Ð¾Ñ€Ð¸ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»Ð°", key=f"open_{athlete_id}", width="stretch"):
                # ÐÐµ Ð¿Ñ€Ð¾Ð¼ÐµÐ½ÑÐ¼Ðµ Ð´Ð¸Ñ€ÐµÐºÑ‚Ð½Ð¾ st.session_state.athlete_id/nav_page Ñ‚ÑƒÐº,
                # Ð·Ð°Ñ‰Ð¾Ñ‚Ð¾ Ñ‚ÐµÐ·Ð¸ ÐºÐ»ÑŽÑ‡Ð¾Ð²Ðµ Ð²ÐµÑ‡Ðµ ÑÐ° ÑÐ²ÑŠÑ€Ð·Ð°Ð½Ð¸ ÑÑŠÑ sidebar widgets.
                # ÐŸÑ€Ð¾Ð¼ÐµÐ½ÑÐ¼Ðµ ÑÐ°Ð¼Ð¾ URL Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€Ð¸Ñ‚Ðµ Ð¸ Ð¿Ñ€Ð¸ ÑÐ»ÐµÐ´Ð²Ð°Ñ‰Ð¸Ñ rerun sync_navigation()
                # Ñ‰Ðµ ÑÐ¸Ð½Ñ…Ñ€Ð¾Ð½Ð¸Ð·Ð¸Ñ€Ð° Ð¸Ð·Ð±Ñ€Ð°Ð½Ð¸Ñ ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚ Ð¸ ÑÑ‚Ñ€Ð°Ð½Ð¸Ñ†Ð°Ñ‚Ð° Ð¿Ñ€ÐµÐ´Ð¸ widget-Ð¸Ñ‚Ðµ Ð´Ð° ÑÐµ ÑÑŠÐ·Ð´Ð°Ð´Ð°Ñ‚.
                st.query_params["page"] = "dashboard"
                st.query_params["athlete"] = athlete_id
                st.rerun()

    st.info(
        "ÐŸÑ€Ð¾Ñ„Ð¸Ð» A Ð¿Ð¾Ð½Ð°ÑÑ Ð´Ð¾Ð±Ñ€Ðµ Ð°ÐµÑ€Ð¾Ð±Ð½Ð¸Ñ Ð¾Ð±ÐµÐ¼; Ð¿Ñ€Ð¾Ñ„Ð¸Ð» B Ñ€ÐµÐ°Ð³Ð¸Ñ€Ð° Ð½ÐµÐ±Ð»Ð°Ð³Ð¾Ð¿Ñ€Ð¸ÑÑ‚Ð½Ð¾ Ð½Ð° Ð½Ð°Ñ‚Ñ€ÑƒÐ¿Ð²Ð°Ð½Ðµ Ð² Z3â€“Z4; "
        "Ð¿Ñ€Ð¾Ñ„Ð¸Ð» C Ðµ Ñ‡ÑƒÐ²ÑÑ‚Ð²Ð¸Ñ‚ÐµÐ»ÐµÐ½ ÐºÑŠÐ¼ Z5 Ð¸ ÑÐ¸Ð»Ð¾Ð²Ð¸ Ð±Ð»Ð¾ÐºÐ¾Ð²Ðµ. Ð¢Ð¾Ð²Ð° Ð²Ð¾Ð´Ð¸ Ð´Ð¾ Ñ€Ð°Ð·Ð»Ð¸Ñ‡Ð½Ð¸ Ð°Ð´Ð°Ð¿Ñ‚Ð¸Ð²Ð½Ð¸ Ð¼Ð½Ð¾Ð¶Ð¸Ñ‚ÐµÐ»Ð¸ Ð¸ Ñ€Ð°Ð·Ð»Ð¸Ñ‡Ð½Ð° Ð¿Ñ€Ð¾Ð³Ñ€Ð°Ð¼Ð°."
    )


def component_summary_table(analysis: dict[str, Any]) -> pd.DataFrame:
    first_week = analysis["weekly_targets"].loc[analysis["weekly_targets"]["week_no"] == 1].set_index("component")
    table = analysis["load_stats"].join(analysis["integrated"], how="left")
    table = table.join(first_week[["target_index", "target_effective_week", "status"]], how="left", rsuffix="_target")
    table = table.reset_index().rename(
        columns={
            "component": "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚",
            "index_7_40": "7/40",
            "Tref": "Tref",
            "load_readiness": "Readiness",
            "monitoring_score": "ÐœÐ¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³",
            "test_adjustment": "Ð¢ÐµÑÑ‚Ð¾Ð²Ð° ÐºÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ",
            "integrated_readiness": "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚",
            "adaptive_multiplier": "ÐœÐ½Ð¾Ð¶Ð¸Ñ‚ÐµÐ»",
            "target_index": "Ð¦ÐµÐ»ÐµÐ²Ð¸ 7/40",
            "target_effective_week": "Ð¡ÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð° Ñ†ÐµÐ» E",
            "status": "Ð Ð¾Ð»Ñ Ð² Ð¼ÐµÐ·Ð¾Ñ†Ð¸ÐºÑŠÐ»Ð°",
        }
    )
    table["Ð¢ÐµÑÑ‚Ð¾Ð²Ð° ÐºÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ"] = table["Ð¢ÐµÑÑ‚Ð¾Ð²Ð° ÐºÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ"] * 100.0
    return table[
        [
            "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚",
            "7/40",
            "Tref",
            "Readiness",
            "ÐœÐ¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³",
            "Ð¢ÐµÑÑ‚Ð¾Ð²Ð° ÐºÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ",
            "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚",
            "ÐœÐ½Ð¾Ð¶Ð¸Ñ‚ÐµÐ»",
            "Ð¦ÐµÐ»ÐµÐ²Ð¸ 7/40",
            "Ð¡ÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð° Ñ†ÐµÐ» E",
            "Ð Ð¾Ð»Ñ Ð² Ð¼ÐµÐ·Ð¾Ñ†Ð¸ÐºÑŠÐ»Ð°",
        ]
    ]


def render_dashboard_page(analysis: dict[str, Any]) -> None:
    athlete = analysis["athlete"]
    page_header(str(athlete["name"]), f"{athlete['profile_name']} Â· {athlete['category']}")
    status_badge(analysis["status"], analysis["hard_flag"])
    if analysis["hard_reasons"]:
        st.warning("ÐÐºÑ‚Ð¸Ð²Ð½Ð¸ Ð¾Ð³Ñ€Ð°Ð½Ð¸Ñ‡ÐµÐ½Ð¸Ñ: " + "; ".join(analysis["hard_reasons"]))

    max_component = analysis["load_stats"]["index_7_40"].idxmax()
    min_component = analysis["integrated"]["integrated_readiness"].idxmin()
    next_event = analysis["next_event"]
    days_to_event = (pd.Timestamp(next_event["start_date"]).date() - date.today()).days if next_event else None
    next_key = analysis["plan"].loc[analysis["plan"]["focus"].isin(["Z3", "Z4", "Z5", "STR"])].head(1)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚",
        f"{analysis['global_readiness']:.0f}/100",
        help=help_text("integrated_readiness"),
    )
    c2.metric(
        f"ÐÐ°Ð¹-Ð²Ð¸ÑÐ¾Ðº 7/40 Â· {max_component}",
        f"{analysis['load_stats'].loc[max_component, 'index_7_40']:.2f}",
        help=help_text("seven_forty"),
    )
    c3.metric(
        f"ÐÐ°Ð¹-Ð½Ð¸ÑÐºÐ° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚ Â· {min_component}",
        f"{analysis['integrated'].loc[min_component, 'integrated_readiness']:.0f}/100",
        help=help_text("readiness"),
    )
    c4.metric(
        "Ð¡Ð»ÐµÐ´Ð²Ð°Ñ‰Ð¾ ÑÑŠÐ±Ð¸Ñ‚Ð¸Ðµ",
        f"{days_to_event} Ð´Ð½Ð¸" if days_to_event is not None else "â€”",
        delta=str(next_event["name"]) if next_event else None,
        delta_color="off",
        help=help_text("periodization"),
    )

    reduced = analysis["integrated"].loc[analysis["integrated"]["adaptive_multiplier"] < 0.95]
    increased = analysis["integrated"].loc[analysis["integrated"]["adaptive_multiplier"] > 1.001]
    reason_lines = []
    if not reduced.empty:
        reason_lines.append("ÐÐ°Ð¼Ð°Ð»ÐµÐ½Ð¸/Ð·Ð°Ð´ÑŠÑ€Ð¶Ð°Ð½Ð¸: " + ", ".join(f"{c} Ã—{r.adaptive_multiplier:.2f}" for c, r in reduced.iterrows()))
    if not increased.empty:
        reason_lines.append("Ð”Ð¾Ð¿ÑƒÑÐ½Ð°Ñ‚Ð¾ ÑƒÐ¼ÐµÑ€ÐµÐ½Ð¾ Ð¸Ð·Ð³Ñ€Ð°Ð¶Ð´Ð°Ð½Ðµ: " + ", ".join(f"{c} Ã—{r.adaptive_multiplier:.2f}" for c, r in increased.iterrows()))
    if not next_key.empty:
        row = next_key.iloc[0]
        reason_lines.append(f"Ð¡Ð»ÐµÐ´Ð²Ð°Ñ‰ ÑÐ¿ÐµÑ†Ð¸Ñ„Ð¸Ñ‡ÐµÐ½ ÑÑ‚Ð¸Ð¼ÑƒÐ»: {row['date'].date()} Â· {row['focus']} Â· {row['method']}")
    st.markdown(
        '<div class="reason-box"><b>ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð¾ Ð°Ð´Ð°Ð¿Ñ‚Ð¸Ð²Ð½Ð¾ Ñ€ÐµÑˆÐµÐ½Ð¸Ðµ</b><br>' + "<br>".join(reason_lines or ["ÐŸÐ»Ð°Ð½ÑŠÑ‚ ÑÐ»ÐµÐ´Ð²Ð° Ð±Ð°Ð·Ð¾Ð²Ð°Ñ‚Ð° Ð¿ÐµÑ€Ð¸Ð¾Ð´Ð¸Ð·Ð°Ñ†Ð¸Ñ Ð±ÐµÐ· Ð´Ð¾Ð¿ÑŠÐ»Ð½Ð¸Ñ‚ÐµÐ»Ð½Ð° ÐºÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ."]) + "</div>",
        unsafe_allow_html=True,
    )

    st.subheader("ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚Ð½Ð¾ Ñ€ÐµÑˆÐµÐ½Ð¸Ðµ")
    table = component_summary_table(analysis)
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={
            "7/40": st.column_config.NumberColumn("7/40", format="%.2f", help=help_text("seven_forty")),
            "Tref": st.column_config.NumberColumn("Tref", format="%.1f", help=help_text("tref")),
            "Readiness": st.column_config.ProgressColumn("Readiness", min_value=0, max_value=100, format="%.0f", help=help_text("readiness")),
            "ÐœÐ¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³": st.column_config.ProgressColumn("ÐœÐ¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³", min_value=0, max_value=100, format="%.0f", help=help_text("monitoring")),
            "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚": st.column_config.ProgressColumn(
                "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚", min_value=0, max_value=100, format="%.0f", help=help_text("integrated_readiness")
            ),
            "ÐœÐ½Ð¾Ð¶Ð¸Ñ‚ÐµÐ»": st.column_config.NumberColumn("ÐœÐ½Ð¾Ð¶Ð¸Ñ‚ÐµÐ»", format="%.2f", help=help_text("adaptive_multiplier")),
            "Ð¦ÐµÐ»ÐµÐ²Ð¸ 7/40": st.column_config.NumberColumn("Ð¦ÐµÐ»ÐµÐ²Ð¸ 7/40", format="%.2f", help=help_text("weekly_target")),
        },
    )

    left, right = st.columns(2)
    with left:
        st.plotly_chart(readiness_figure(analysis["readiness_history"], days=35), width="stretch")
    with right:
        first_six = analysis["weekly_targets"].loc[analysis["weekly_targets"]["week_no"] <= 6]
        st.plotly_chart(weekly_targets_figure(first_six, "target_index"), width="stretch")


def render_load_page(analysis: dict[str, Any]) -> None:
    page_header(
        "ÐÐ°Ñ‚Ð¾Ð²Ð°Ñ€Ð²Ð°Ð½Ðµ Ð¸ Ð¸Ð½Ð´ÐµÐºÑ 7/40",
        "Ð ÐµÐ°Ð»Ð½Ð¾ Ð²Ñ€ÐµÐ¼Ðµ, Ð²ÑŠÑ‚Ñ€ÐµÑˆÐ½Ð¾Ð·Ð¾Ð½Ð¾Ð²Ð¾ Ð¿Ñ€ÐµÑ‚ÐµÐ³Ð»ÑÐ½Ðµ, ÑÐ¸Ð»Ð¾Ð²Ð¸ ÐºÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚Ð¸, Ð´Ð¸Ñ€ÐµÐºÑ‚ÐµÐ½ Ð¸ ÐµÑ„ÐµÐºÑ‚Ð¸Ð²ÐµÐ½ Ñ‚Ð¾Ð²Ð°Ñ€.",
    )
    component = st.selectbox(
        "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚",
        COMPONENTS,
        format_func=lambda c: COMPONENT_LABELS[c],
        key="load_component",
    )
    row = analysis["load_stats"].loc[component]
    readiness = analysis["load_readiness"].loc[component]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("7/40", f"{row['index_7_40']:.2f}", help=help_text("seven_forty"))
    c2.metric("E7 Â· ÑÑ€ÐµÐ´Ð½Ð¾/Ð´ÐµÐ½", f"{row['E7_daily']:.1f}", help=help_text("effective_load"))
    c3.metric("E40 Â· ÑÑ€ÐµÐ´Ð½Ð¾/Ð´ÐµÐ½", f"{row['E40_daily']:.1f}", help=help_text("effective_load"))
    c4.metric("Tref", f"{row['Tref']:.1f}", help=help_text("tref"))
    c5.metric("Readiness", f"{readiness['readiness']:.0f}%", help=help_text("readiness"))

    if component == "STR":
        st.info(
            "STR Ðµ Ð¾Ð±Ñ‰Ð¸ÑÑ‚ ÑÐ¸Ð»Ð¾Ð² Ñ„Ð¸Ð·Ð¸Ð¾Ð»Ð¾Ð³Ð¸Ñ‡ÐµÐ½ ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚. Ð ÐµÐ°Ð»Ð½Ð¾Ñ‚Ð¾ Ð²Ñ€ÐµÐ¼Ðµ ÑÐµ Ð¿Ð°Ð·Ð¸ Ð¿Ð¾ Ñ‡ÐµÑ‚Ð¸Ñ€Ð¸ Ð²Ð¸Ð´Ð°, "
            "Ð° ÑÐ¸Ð»Ð¾Ð²Ð¸ÑÑ‚ Q ÑÐµ Ð¿Ð¾Ð»ÑƒÑ‡Ð°Ð²Ð° Ñ‡Ñ€ÐµÐ· ÐºÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚Ð¸Ñ‚Ðµ Ð¿Ð¾-Ð´Ð¾Ð»Ñƒ.",
            icon="ðŸ‹ï¸",
        )
        strength_model = pd.DataFrame(
            [
                {
                    "ÐšÐ¾Ð´": strength_type,
                    "Ð’Ð¸Ð´ ÑÐ¸Ð»Ð°": STRENGTH_LABELS[strength_type],
                    "ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚": STRENGTH_COEFFICIENTS[strength_type],
                    "ÐŸÑ€Ð¸Ð¼ÐµÑ€ Â· 30 Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½": 30.0 * STRENGTH_COEFFICIENTS[strength_type],
                }
                for strength_type in STRENGTH_TYPES
            ]
        )
        st.dataframe(
            strength_model,
            width="stretch",
            hide_index=True,
            column_config={
                "ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚": st.column_config.NumberColumn(format="%.1f", help=help_text("strength_load")),
                "ÐŸÑ€Ð¸Ð¼ÐµÑ€ Â· 30 Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½": st.column_config.NumberColumn("Ð•ÐºÐ². Ð¼Ð¸Ð½ Ð¿Ñ€Ð¸ 30 Ñ€ÐµÐ°Ð»Ð½Ð¸", format="%.1f"),
            },
        )

    left, right = st.columns(2)
    with left:
        st.plotly_chart(index_7_40_figure(analysis["rolling_load"], component), width="stretch")
    with right:
        st.plotly_chart(effective_load_figure(analysis["rolling_load"], component), width="stretch")

    st.subheader("Ð”ÐµÑ‚Ð°Ð¹Ð» Ð½Ð° Ð¸Ð·Ð¿ÑŠÐ»Ð½ÐµÐ½Ð° Ñ‚ÐµÑÑ‚Ð¾Ð²Ð° Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚")
    summaries = analysis["activity_summaries"].sort_values("date", ascending=False).head(40).copy()
    if summaries.empty:
        st.info(
            "Ð’ÑÐµ Ð¾Ñ‰Ðµ Ð½ÑÐ¼Ð° Ð²ÑŠÐ²ÐµÐ´ÐµÐ½Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÑŠÑ‡Ð½Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð·Ð° Ñ‚Ð¾Ð·Ð¸ ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚. "
            "Ð”Ð¾Ð±Ð°Ð²Ð¸ Ð´Ð½ÐµÐ²Ð½Ð¸ Ð¸Ð»Ð¸ ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð¸ Ð´Ð°Ð½Ð½Ð¸ Ð¾Ñ‚ ÑÑ‚Ñ€Ð°Ð½Ð¸Ñ†Ð°Ñ‚Ð° â€žÐ˜ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð¸ Ð½Ð°Ñ‡Ð°Ð»Ð½Ð¸ Ð´Ð°Ð½Ð½Ð¸â€œ; "
            "ÑÐ»ÐµÐ´ Ñ‚Ð¾Ð²Ð° Ñ‚ÑƒÐº Ñ‰Ðµ ÑÐµ Ð¿Ð¾ÑÐ²Ð¸ Ð°Ð½Ð°Ð»Ð¸Ð·ÑŠÑ‚ Ñ€ÐµÐ°Ð»Ð½Ð¾ Ð²Ñ€ÐµÐ¼Ðµ â†’ Q â†’ E."
        )
        return

    label_map = {
        row["activity_id"]: f"{pd.Timestamp(row['date']).date()} Â· {row['sport']} Â· {row['moving_min']:.0f} Ð¼Ð¸Ð½"
        for _, row in summaries.iterrows()
    }
    selected_id = st.selectbox(
        "ÐÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚",
        summaries["activity_id"].tolist(),
        format_func=lambda value: label_map[value],
        key="activity_detail",
    )
    selected = summaries.loc[summaries["activity_id"] == selected_id].iloc[0]
    stream = generate_activity_stream(selected, analysis["zone_profile"])
    stream_summary = analyze_activity_stream(stream, analysis["zone_profile"])

    lcol, rcol = st.columns([1.25, 1])
    with lcol:
        st.plotly_chart(activity_stream_figure(stream, analysis["zone_profile"]), width="stretch")
    with rcol:
        st.plotly_chart(real_vs_equivalent_figure(selected), width="stretch")

    st.dataframe(
        stream_summary.rename(
            columns={
                "component": "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚",
                "real_min": "Ð ÐµÐ°Ð»Ð½Ð¾ Ð²Ñ€ÐµÐ¼Ðµ",
                "q_min": "Ð•ÐºÐ²Ð¸Ð²Ð°Ð»ÐµÐ½Ñ‚Ð½Ð¾ Q",
                "avg_k": "Ð¡Ñ€ÐµÐ´ÐµÐ½ k",
                "valid_seconds": "Ð’Ð°Ð»Ð¸Ð´Ð½Ð¸ ÑÐµÐºÑƒÐ½Ð´Ð¸",
            }
        ),
        width="stretch",
        hide_index=True,
        column_config={
            "Ð ÐµÐ°Ð»Ð½Ð¾ Ð²Ñ€ÐµÐ¼Ðµ": st.column_config.NumberColumn(format="%.2f", help=help_text("real_equivalent")),
            "Ð•ÐºÐ²Ð¸Ð²Ð°Ð»ÐµÐ½Ñ‚Ð½Ð¾ Q": st.column_config.NumberColumn(format="%.2f", help=help_text("real_equivalent")),
            "Ð¡Ñ€ÐµÐ´ÐµÐ½ k": st.column_config.NumberColumn(format="%.3f", help=help_text("real_equivalent")),
        },
    )

    strength_rows = []
    for strength_type in STRENGTH_TYPES:
        real_min = float(selected.get(f"real_{strength_type}", 0.0) or 0.0)
        coefficient = float(STRENGTH_COEFFICIENTS[strength_type])
        equivalent_min = float(selected.get(f"q_{strength_type}", real_min * coefficient) or 0.0)
        strength_rows.append(
            {
                "Ð’Ð¸Ð´ ÑÐ¸Ð»Ð°": STRENGTH_LABELS[strength_type],
                "Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸": real_min,
                "ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚": coefficient,
                "Ð•ÐºÐ²Ð¸Ð²Ð°Ð»ÐµÐ½Ñ‚Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸": equivalent_min,
            }
        )
    strength_detail = pd.DataFrame(strength_rows)
    if float(strength_detail["Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸"].sum()) > 0:
        st.subheader("Ð¡Ð¸Ð»Ð¾Ð² Ð´ÐµÑ‚Ð°Ð¹Ð» Ð½Ð° Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ñ‚Ð°")
        scol1, scol2 = st.columns([1.2, 1])
        with scol1:
            st.plotly_chart(strength_breakdown_figure(selected), width="stretch")
        with scol2:
            st.dataframe(
                strength_detail,
                width="stretch",
                hide_index=True,
                column_config={
                    "Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸": st.column_config.NumberColumn(format="%.1f"),
                    "ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚": st.column_config.NumberColumn(format="%.1f", help=help_text("strength_load")),
                    "Ð•ÐºÐ²Ð¸Ð²Ð°Ð»ÐµÐ½Ñ‚Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸": st.column_config.NumberColumn(format="%.1f"),
                },
            )
            real_total = float(strength_detail["Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸"].sum())
            q_total = float(strength_detail["Ð•ÐºÐ²Ð¸Ð²Ð°Ð»ÐµÐ½Ñ‚Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸"].sum())
            average_k = q_total / real_total if real_total > 0 else 0.0
            st.metric("Ð¡Ð¸Ð»Ð° Â· Ð¾Ð±Ñ‰Ð¾ Ñ€ÐµÐ°Ð»Ð½Ð¾ Ð²Ñ€ÐµÐ¼Ðµ", f"{real_total:.1f} Ð¼Ð¸Ð½")
            st.metric("Ð¡Ð¸Ð»Ð° Â· Ð¾Ð±Ñ‰ Q", f"{q_total:.1f} ÐµÐºÐ². Ð¼Ð¸Ð½", delta=f"ÑÑ€ÐµÐ´ÐµÐ½ k = {average_k:.2f}", delta_color="off")

    st.caption(
        "ÐŸÑƒÐ»ÑÐ¾Ð²Ð¸ÑÑ‚ Ð¿Ð¾Ñ‚Ð¾Ðº Ðµ ÑÐ¸Ð½Ñ‚ÐµÑ‚Ð¸Ñ‡ÐµÐ½ Ð¸ ÑÐµ Ð¾Ñ‚Ð½Ð°ÑÑ Ð·Ð° Z1â€“Z5. Ð¡Ð¸Ð»Ð¾Ð²Ð°Ñ‚Ð° Ñ€Ð°Ð±Ð¾Ñ‚Ð° ÑÐµ Ð°Ð½Ð°Ð»Ð¸Ð·Ð¸Ñ€Ð° "
        "Ð¾Ñ‚Ð´ÐµÐ»Ð½Ð¾ Ñ‡Ñ€ÐµÐ· Ð²Ð¸Ð´Ð°, Ñ€ÐµÐ°Ð»Ð½Ð¸Ñ‚Ðµ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ð¸ Ñ„Ð¸ÐºÑÐ¸Ñ€Ð°Ð½Ð¸Ñ ÐºÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚."
    )

def render_recovery_page(analysis: dict[str, Any]) -> None:
    page_header("Ð”Ð¸Ð½Ð°Ð¼Ð¸ÐºÐ° Ð½Ð° Ð²ÑŠÐ·ÑÑ‚Ð°Ð½Ð¾Ð²ÑÐ²Ð°Ð½ÐµÑ‚Ð¾", "ÐžÑÑ‚Ð°Ñ‚ÑŠÑ‡Ð½Ð° ÑƒÐ¼Ð¾Ñ€Ð°, ÐµÐºÑÐ¿Ð¾Ð½ÐµÐ½Ñ†Ð¸Ð°Ð»Ð½Ð¾ Ð·Ð°Ñ‚Ð¸Ñ…Ð²Ð°Ð½Ðµ Ð¸ Ð¿Ñ€Ð¾Ð³Ð½Ð¾Ð·ÐµÐ½ Ð¼Ð¾Ð¼ÐµÐ½Ñ‚ Ð·Ð° ÑÐ»ÐµÐ´Ð²Ð°Ñ‰ ÐºÐ»ÑŽÑ‡Ð¾Ð² ÑÑ‚Ð¸Ð¼ÑƒÐ».")
    selected_components = st.multiselect(
        "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚Ð¸",
        COMPONENTS,
        default=["Z2", "Z3", "Z4", "Z5"],
        format_func=lambda c: COMPONENT_SHORT[c],
        key="recovery_components",
    )
    st.plotly_chart(readiness_figure(analysis["readiness_history"], selected_components or COMPONENTS, days=60), width="stretch")

    current = analysis["load_readiness"].join(analysis["integrated"][["integrated_readiness", "hard_flag"]]).reset_index()
    current = current.rename(
        columns={
            "component": "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚",
            "fatigue": "ÐžÑÑ‚Ð°Ñ‚ÑŠÑ‡Ð½Ð° ÑƒÐ¼Ð¾Ñ€Ð°",
            "readiness": "Ð¢Ð¾Ð²Ð°Ñ€Ð½Ð° readiness",
            "days_to_full": "Ð”Ð½Ð¸ Ð´Ð¾ Ð¿Ñ€Ð°ÐºÑ‚Ð¸Ñ‡ÐµÑÐºÐ¾ Ð²ÑŠÐ·ÑÑ‚Ð°Ð½Ð¾Ð²ÑÐ²Ð°Ð½Ðµ",
            "integrated_readiness": "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚",
            "hard_flag": "Ð¢Ð²ÑŠÑ€Ð´ Ñ„Ð»Ð°Ð³",
        }
    )
    st.dataframe(
        current,
        width="stretch",
        hide_index=True,
        column_config={
            "Ð¢Ð¾Ð²Ð°Ñ€Ð½Ð° readiness": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f", help=help_text("readiness")),
            "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%.1f", help=help_text("integrated_readiness")
            ),
            "Ð”Ð½Ð¸ Ð´Ð¾ Ð¿Ñ€Ð°ÐºÑ‚Ð¸Ñ‡ÐµÑÐºÐ¾ Ð²ÑŠÐ·ÑÑ‚Ð°Ð½Ð¾Ð²ÑÐ²Ð°Ð½Ðµ": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    component = st.selectbox("ÐŸÑ€Ð¾Ð³Ð½Ð¾Ð·Ð° Ð±ÐµÐ· Ð½Ð¾Ð² ÑÑ‚Ð¸Ð¼ÑƒÐ»", COMPONENTS, format_func=lambda c: COMPONENT_LABELS[c], key="recovery_forecast_component")
    fatigue = float(analysis["load_readiness"].loc[component, "fatigue"])
    tau = float(st.session_state.bundle["parameters"]["recovery"][component]["tau_days"])
    days = np.linspace(0, 7, 57)
    forecast = 100.0 - fatigue * np.exp(-days / max(tau, 1e-9))
    fig = go.Figure(go.Scatter(x=days, y=forecast, mode="lines", name=component))
    fig.add_hline(y=90, line_dash="dot", annotation_text="ÐºÐ»ÑŽÑ‡Ð¾Ð² ÑÑ‚Ð¸Ð¼ÑƒÐ»")
    fig.add_hline(y=float(st.session_state.bundle["parameters"]["practical_full_recovery"]), line_dash="dot", annotation_text="Ð¿Ñ€Ð°ÐºÑ‚Ð¸Ñ‡ÐµÑÐºÐ¸ Ð²ÑŠÐ·ÑÑ‚Ð°Ð½Ð¾Ð²ÐµÐ½")
    fig.update_layout(title=f"ÐŸÑ€Ð¾Ð³Ð½Ð¾Ð·Ð½Ð° ÐºÑ€Ð¸Ð²Ð° Ð±ÐµÐ· Ð½Ð¾Ð²Ð¾ Ð½Ð°Ñ‚Ð¾Ð²Ð°Ñ€Ð²Ð°Ð½Ðµ Â· {component}", xaxis_title="Ð”Ð½Ð¸", yaxis_title="Readiness %", yaxis_range=[0, 102], height=380)
    st.plotly_chart(fig, width="stretch")

    recent = analysis["readiness_history"].loc[
        pd.to_datetime(analysis["readiness_history"]["date"]) >= pd.Timestamp.today().normalize() - pd.Timedelta(days=14)
    ].copy()
    recent = recent.loc[recent["impulse"] > 0.05].sort_values(["date", "component"], ascending=[False, True]).head(30)
    with st.expander("ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð¸ Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÑŠÑ‡Ð½Ð¸ Ð¸Ð¼Ð¿ÑƒÐ»ÑÐ¸"):
        st.dataframe(recent, width="stretch", hide_index=True)


def render_plan_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    page_header(
        "ÐÐ´Ð°Ð¿Ñ‚Ð¸Ð²Ð½Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÑŠÑ‡Ð½Ð° Ð¿Ñ€Ð¾Ð³Ñ€Ð°Ð¼Ð°",
        "Ð¡ÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð° ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚Ð½Ð° Ñ†ÐµÐ» â†’ Ð³Ð¾Ð´Ð¸ÑˆÐµÐ½ ÐºÐ¾Ð½Ñ‚ÐµÐºÑÑ‚ â†’ ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð° ÑÑ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð° â†’ Ð´Ð¸Ñ€ÐµÐºÑ‚ÐµÐ½ Ñ‚Ð¾Ð²Ð°Ñ€ â†’ Ñ€ÐµÐ°Ð»Ð½Ð¾ Ð²Ñ€ÐµÐ¼Ðµ â†’ ÐºÐ¾Ð½ÐºÑ€ÐµÑ‚ÐµÐ½ Ð¼ÐµÑ‚Ð¾Ð´.",
    )
    plan = analysis["plan"].copy()
    training_rows = plan.loc[plan["focus"] != "REST"] if not plan.empty else plan
    snapshot = analysis["decision_snapshot"].get("plan", {})
    preferences = analysis["planning_preferences"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Ð¢Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÑŠÑ‡Ð½Ð¸ ÑÐµÑÐ¸Ð¸",
        f"{len(training_rows)}/{preferences['sessions_per_week']}",
        help=help_text("weekly_structure"),
    )
    c2.metric("ÐŸÐ»Ð°Ð½Ð¸Ñ€Ð°Ð½ Ð¾Ð±ÐµÐ¼", f"{training_rows['total_real_min'].sum() / 60.0:.1f} h")
    rest_labels = [WEEKDAY_LABELS[day] for day in preferences["rest_days"]]
    c3.metric("ÐŸÐ¾Ñ‡Ð¸Ð²Ð½Ð¸ Ð´Ð½Ð¸", ", ".join(rest_labels) if rest_labels else "ÐÑÐ¼Ð°")
    c4.metric(
        "Ð”Ð²Ð¾Ð¹Ð½Ð° Ð¿Ñ€Ð°Ð³Ð¾Ð²Ð°",
        "ÐÐºÑ‚Ð¸Ð²Ð½Ð°" if snapshot.get("double_threshold_active") else "ÐÐµÐ°ÐºÑ‚Ð¸Ð²Ð½Ð°",
        help=help_text("double_threshold"),
    )

    for warning in snapshot.get("warnings", []):
        st.warning(warning)

    tab_volume, tab_wave, tab_week = st.tabs(
        ["Ð ÐµÐ°Ð»Ð½Ð¾ ÑÑ€ÐµÑ‰Ñƒ Ð¿Ð»Ð°Ð½", "Ð”ÑŠÐ»Ð³Ð¾ÑÑ€Ð¾Ñ‡Ð½Ð° Ð´Ð¸Ð½Ð°Ð¼Ð¸ÐºÐ°", "Ð¡Ð»ÐµÐ´Ð²Ð°Ñ‰Ð¸ 7 Ð´Ð½Ð¸"]
    )

    with tab_volume:
        st.plotly_chart(weekly_plan_vs_actual_figure(analysis["volume_trajectory"]), width="stretch")
        context = analysis["annual_context"]
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð¸ 4 ÑÐµÐ´Ð¼Ð¸Ñ†Ð¸", f"{context['recent_weekly_hours']:.1f} h/ÑÐµÐ´Ð¼.")
        v2.metric("ÐŸÐ»Ð°Ð½ Â· ÑÐ»ÐµÐ´Ð²Ð°Ñ‰Ð¸ 7 Ð´Ð½Ð¸", f"{training_rows['total_real_min'].sum() / 60.0:.1f} h")
        v3.metric("ÐÑƒÐ¶Ð½Ð¾ ÑÑ€ÐµÐ´Ð½Ð¾ Ð´Ð¾ ÐºÑ€Ð°Ñ", f"{context['required_weekly_hours']:.1f} h/ÑÐµÐ´Ð¼.")
        v4.metric("ÐšÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ Ð¾Ñ‚ ÑÐµÐ·Ð¾Ð½Ð½Ð°Ñ‚Ð° Ñ†ÐµÐ»", f"Ã—{context['volume_factor']:.3f}")
        st.caption(
            "Ð›Ð¸Ð½Ð¸ÑÑ‚Ð° â€žÐ ÐµÐ°Ð»Ð½Ð¾ Ð¸Ð·Ð¿ÑŠÐ»Ð½ÐµÐ½Ð¾â€œ ÑÐµ Ñ„Ð¾Ñ€Ð¼Ð¸Ñ€Ð° ÑÐ°Ð¼Ð¾ Ð¾Ñ‚ Ð¸ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð° Ð¸ Ð½Ðµ ÑÐµ Ð¿Ñ€Ð¾Ð¼ÐµÐ½Ñ Ð¿Ñ€Ð¸ Ñ€ÐµÐ´Ð°ÐºÑ†Ð¸Ñ "
            "Ð½Ð° ÑÐµÐ·Ð¾Ð½Ð½Ð°Ñ‚Ð° Ñ†ÐµÐ». Ð›Ð¸Ð½Ð¸ÑÑ‚Ð° â€žÐÐ´Ð°Ð¿Ñ‚Ð¸Ð²ÐµÐ½ Ð¿Ð»Ð°Ð½â€œ ÑÐµ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»ÑÐ²Ð° ÑÐ»ÐµÐ´ Ð²ÑÑÐºÐ° Ð¿Ñ€Ð¾Ð¼ÑÐ½Ð° Ð½Ð° Ñ†ÐµÐ»Ñ‚Ð°, "
            "ÐºÐ°Ð»ÐµÐ½Ð´Ð°Ñ€Ð°, readiness Ð¸Ð»Ð¸ Ð¸ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð°."
        )
        if context.get("factor_limited"):
            st.warning(context.get("feasibility_status", "ÐšÐ¾Ñ€ÐµÐºÑ†Ð¸ÑÑ‚Ð° Ðµ Ð¾Ð³Ñ€Ð°Ð½Ð¸Ñ‡ÐµÐ½Ð° Ð¾Ñ‚ Ð·Ð°Ñ‰Ð¸Ñ‚ÐµÐ½ Ð»Ð¸Ð¼Ð¸Ñ‚."))

    with tab_wave:
        metric = st.radio(
            "Ð“Ñ€Ð°Ñ„Ð¸ÐºÐ°",
            ["target_effective_week", "target_index"],
            horizontal=True,
            format_func=lambda x: "Ð•Ñ„ÐµÐºÑ‚Ð¸Ð²ÐµÐ½ Ñ‚Ð¾Ð²Ð°Ñ€" if x == "target_effective_week" else "Ð¦ÐµÐ»ÐµÐ²Ð¸ 7/40",
            key="weekly_chart_metric",
        )
        st.plotly_chart(weekly_targets_figure(analysis["weekly_targets"], metric), width="stretch")
        first_week = analysis["weekly_targets"].loc[analysis["weekly_targets"]["week_no"] == 1].copy()
        factor_cols = [
            "component",
            "target_index",
            "target_effective_week",
            "annual_goal_factor",
            "adaptive_factor",
            "calendar_factor",
            "taper_factor",
            "status",
        ]
        st.dataframe(first_week[factor_cols], width="stretch", hide_index=True)
        events = bundle["calendar"].loc[
            bundle["calendar"]["athlete_id"] == str(analysis["athlete"]["athlete_id"])
        ].copy()
        st.dataframe(
            events[["type", "name", "start_date", "end_date", "priority", "goal"]],
            width="stretch",
            hide_index=True,
        )

    with tab_week:
        st.plotly_chart(plan_comparison_figure(analysis["plan_comparison"]), width="stretch")
        comp_display = analysis["plan_comparison"].reset_index().rename(
            columns={
                "component": "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚",
                "target_effective": "Ð¦ÐµÐ» E",
                "planned_effective": "ÐŸÐ»Ð°Ð½ E",
                "target_direct_q": "Ð¦ÐµÐ» Q",
                "planned_direct_q": "ÐŸÐ»Ð°Ð½ Q",
                "remaining_direct_q": "ÐžÑÑ‚Ð°Ñ‚ÑŠÐº Q",
                "completion_pct": "Ð˜Ð·Ð¿ÑŠÐ»Ð½ÐµÐ½Ð¸Ðµ %",
            }
        )
        st.dataframe(
            comp_display,
            width="stretch",
            hide_index=True,
            column_config={"Ð˜Ð·Ð¿ÑŠÐ»Ð½ÐµÐ½Ð¸Ðµ %": st.column_config.ProgressColumn(min_value=0, max_value=130, format="%.0f")},
        )

        editor_columns = [
            "date",
            "day",
            "session_no",
            "time_of_day",
            "focus",
            "method",
            "strength_label",
            "strength_coefficient",
            "strength_real_min",
            "strength_equivalent_min",
            "total_real_min",
            "status",
            "locked",
            "coach_note",
        ]
        editable = plan[editor_columns].copy()
        edited = st.data_editor(
            editable,
            width="stretch",
            hide_index=True,
            key=f"plan_editor_{analysis['athlete']['athlete_id']}_{bundle['version']}",
            disabled=[
                "date",
                "day",
                "session_no",
                "time_of_day",
                "focus",
                "method",
                "strength_label",
                "strength_coefficient",
                "strength_real_min",
                "strength_equivalent_min",
            ] if can_edit else list(editable.columns),
            column_config={
                "date": st.column_config.DateColumn("Ð”Ð°Ñ‚Ð°", format="DD.MM.YYYY"),
                "day": "Ð”ÐµÐ½",
                "session_no": st.column_config.NumberColumn("Ð¡ÐµÑÐ¸Ñ", format="%d"),
                "time_of_day": "Ð§Ð°ÑÑ‚ Ð½Ð° Ð´ÐµÐ½Ñ",
                "focus": "Ð¤Ð¾ÐºÑƒÑ",
                "method": "ÐœÐµÑ‚Ð¾Ð´",
                "strength_label": st.column_config.TextColumn("Ð’Ð¸Ð´ ÑÐ¸Ð»Ð°"),
                "strength_coefficient": st.column_config.NumberColumn("Ð¡Ð¸Ð»Ð¾Ð² k", format="%.1f", help=help_text("strength_load")),
                "strength_real_min": st.column_config.NumberColumn("Ð¡Ð¸Ð»Ð° Â· Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½", format="%.1f"),
                "strength_equivalent_min": st.column_config.NumberColumn("Ð¡Ð¸Ð»Ð° Â· ÐµÐºÐ². Ð¼Ð¸Ð½", format="%.1f"),
                "total_real_min": st.column_config.NumberColumn("ÐžÐ±Ñ‰Ð¾ Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸", min_value=0.0, max_value=360.0, step=5.0),
                "status": st.column_config.SelectboxColumn("Ð¡Ñ‚Ð°Ñ‚ÑƒÑ", options=["ÐŸÑ€ÐµÐ´Ð»Ð¾Ð¶ÐµÐ½Ð°", "ÐžÐ´Ð¾Ð±Ñ€ÐµÐ½Ð°", "ÐžÑ‚Ñ…Ð²ÑŠÑ€Ð»ÐµÐ½Ð°"]),
                "locked": st.column_config.CheckboxColumn("Ð—Ð°ÐºÐ»ÑŽÑ‡ÐµÐ½Ð°"),
                "coach_note": st.column_config.TextColumn("Ð‘ÐµÐ»ÐµÐ¶ÐºÐ° Ð½Ð° Ñ‚Ñ€ÐµÐ½ÑŒÐ¾Ñ€Ð°"),
            },
        )
        b1, b2, b3 = st.columns([1, 1, 2])
        if b1.button("Ð—Ð°Ð¿Ð°Ð·Ð¸ Ñ€ÐµÐ´Ð°ÐºÑ†Ð¸Ð¸Ñ‚Ðµ", disabled=not can_edit, width="stretch"):
            for _, row in edited.iterrows():
                key = (
                    f"{analysis['athlete']['athlete_id']}|"
                    f"{pd.Timestamp(row['date']).date().isoformat()}|{int(row['session_no'])}"
                )
                bundle["plan_overrides"][key] = {
                    "status": row["status"],
                    "locked": bool(row["locked"]),
                    "coach_note": str(row["coach_note"]),
                    "total_real_min": float(row["total_real_min"]),
                }
            commit_bundle(
                bundle,
                "plan_edit",
                "Ð ÐµÐ´Ð°ÐºÑ†Ð¸Ð¸Ñ‚Ðµ Ð½Ð° ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð°Ñ‚Ð° Ð¿Ñ€Ð¾Ð³Ñ€Ð°Ð¼Ð° ÑÐ° Ð·Ð°Ð¿Ð¸ÑÐ°Ð½Ð¸ ÐºÐ°Ñ‚Ð¾ Ð½Ð¾Ð²Ð° Ð²ÐµÑ€ÑÐ¸Ñ.",
                str(analysis["athlete"]["athlete_id"]),
            )
        if b2.button("ÐžÐ´Ð¾Ð±Ñ€Ð¸ Ð²ÑÐ¸Ñ‡ÐºÐ¸ ÑÐµÑÐ¸Ð¸", disabled=not can_edit, width="stretch"):
            for _, row in plan.iterrows():
                key = (
                    f"{analysis['athlete']['athlete_id']}|"
                    f"{pd.Timestamp(row['date']).date().isoformat()}|{int(row['session_no'])}"
                )
                current = bundle["plan_overrides"].get(key, {})
                current.update(
                    {
                        "status": "ÐžÐ´Ð¾Ð±Ñ€ÐµÐ½Ð°",
                        "locked": bool(current.get("locked", False)),
                        "coach_note": current.get("coach_note", ""),
                    }
                )
                bundle["plan_overrides"][key] = current
            commit_bundle(
                bundle,
                "plan_approve",
                "Ð’ÑÐ¸Ñ‡ÐºÐ¸ ÑÐµÑÐ¸Ð¸ Ð¾Ñ‚ Ñ‚ÐµÐºÑƒÑ‰Ð°Ñ‚Ð° Ð¿Ñ€Ð¾Ð³Ñ€Ð°Ð¼Ð° ÑÐ° Ð¾Ð´Ð¾Ð±Ñ€ÐµÐ½Ð¸.",
                str(analysis["athlete"]["athlete_id"]),
            )
        b3.caption(
            "Ð‘Ñ€Ð¾ÑÑ‚ ÑÐµÑÐ¸Ð¸, Ð¿Ð¾Ñ‡Ð¸Ð²Ð½Ð¸Ñ‚Ðµ Ð´Ð½Ð¸ Ð¸ Ð´Ð²Ð¾Ð¹Ð½Ð¸Ñ‚Ðµ Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ¸ ÑÐµ ÑƒÐ¿Ñ€Ð°Ð²Ð»ÑÐ²Ð°Ñ‚ Ð¾Ñ‚ â€žÐšÐ°Ð»ÐµÐ½Ð´Ð°Ñ€ Ð¸ Ñ†ÐµÐ»Ð¸â€œ. "
            "Ð—Ð°ÐºÐ»ÑŽÑ‡ÐµÐ½Ð¸Ñ‚Ðµ Ð·Ð°Ð¿Ð¸ÑÐ¸ ÑÐµ Ð¿Ð°Ð·ÑÑ‚ ÐºÐ°Ñ‚Ð¾ Ñ‚Ñ€ÐµÐ½ÑŒÐ¾Ñ€ÑÐºÐ¸ override."
        )

        d1, d2 = st.columns(2)
        d1.download_button(
            "Ð˜Ð·Ñ‚ÐµÐ³Ð»Ð¸ Ð¿Ñ€Ð¾Ð³Ñ€Ð°Ð¼Ð°Ñ‚Ð° Â· CSV",
            dataframe_csv_bytes(plan),
            file_name=f"plan_{analysis['athlete']['athlete_id']}_{date.today().isoformat()}.csv",
            mime="text/csv",
            width="stretch",
        )
        d2.download_button(
            "Ð˜Ð·Ñ‚ÐµÐ³Ð»Ð¸ DecisionSnapshot Â· JSON",
            json_bytes(analysis["decision_snapshot"]),
            file_name=f"decision_snapshot_{analysis['athlete']['athlete_id']}_{date.today().isoformat()}.json",
            mime="application/json",
            width="stretch",
        )

        st.subheader("ÐžÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ Ð¿Ð¾ ÑÐµÑÐ¸Ð¸")
        for _, row in plan.iterrows():
            label = (
                f"{pd.Timestamp(row['date']).strftime('%d.%m')} Â· {row['day']} Â· "
                f"{row['time_of_day']} Â· {row['focus']} Â· {row['method']}"
            )
            with st.expander(label):
                st.write(row["description"])
                st.markdown(f"**ÐžÐ±ÑÑÐ½ÐµÐ½Ð¸Ðµ Ð½Ð° Ñ€ÐµÑˆÐµÐ½Ð¸ÐµÑ‚Ð¾:** {row['explanation']}")
                details = pd.DataFrame(
                    {
                        "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚": COMPONENTS,
                        "Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸": [row[f"real_{c}"] for c in COMPONENTS],
                        "Ð”Ð¸Ñ€ÐµÐºÑ‚Ð½Ð¾ Q": [row[f"q_{c}"] for c in COMPONENTS],
                        "Ð•Ñ„ÐµÐºÑ‚Ð¸Ð²Ð½Ð¾ E": [row[f"e_{c}"] for c in COMPONENTS],
                    }
                )
                st.dataframe(details, width="stretch", hide_index=True)
                if str(row.get("focus", "")) == "STR":
                    strength_type = str(row.get("strength_type", ""))
                    strength_table = pd.DataFrame(
                        [
                            {
                                "Ð’Ð¸Ð´ ÑÐ¸Ð»Ð°": row.get("strength_label", STRENGTH_LABELS.get(strength_type, "")),
                                "Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸": float(row.get("strength_real_min", 0.0)),
                                "ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚": float(row.get("strength_coefficient", 0.0)),
                                "Ð•ÐºÐ²Ð¸Ð²Ð°Ð»ÐµÐ½Ñ‚Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸": float(row.get("strength_equivalent_min", 0.0)),
                            }
                        ]
                    )
                    st.markdown("**Ð¡Ð¸Ð»Ð¾Ð²Ð¾ Ð¿Ñ€ÐµÐ¾Ð±Ñ€Ð°Ð·ÑƒÐ²Ð°Ð½Ðµ**", help=help_text("strength_load"))
                    st.dataframe(
                        strength_table,
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸": st.column_config.NumberColumn(format="%.1f"),
                            "ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚": st.column_config.NumberColumn(format="%.1f"),
                            "Ð•ÐºÐ²Ð¸Ð²Ð°Ð»ÐµÐ½Ñ‚Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸": st.column_config.NumberColumn(format="%.1f"),
                        },
                    )


def render_calendar_goals_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(analysis["athlete"]["athlete_id"])
    profile_code = str(analysis["athlete"].get("profile_code", "A"))
    preferences = normalize_preferences(
        bundle.setdefault("planning_preferences", {}).get(athlete_id),
        profile_code,
        date.today(),
    )
    bundle["planning_preferences"][athlete_id] = preferences
    context = analysis["annual_context"]

    page_header(
        "ÐšÐ°Ð»ÐµÐ½Ð´Ð°Ñ€, ÑÐµÐ·Ð¾Ð½Ð½Ð¸ Ñ†ÐµÐ»Ð¸ Ð¸ ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð° ÑÑ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð°",
        "Ð ÐµÐ´Ð°ÐºÑ‚Ð¸Ñ€Ð°Ð¹ Ð¾ÑÐ½Ð¾Ð²Ð½Ð¸ Ð¸ ÐºÐ¾Ð½Ñ‚Ñ€Ð¾Ð»Ð½Ð¸ ÑÑ‚Ð°Ñ€Ñ‚Ð¾Ð²Ðµ, Ð»Ð°Ð³ÐµÑ€Ð¸, Ð³Ð¾Ð´Ð¸ÑˆÐ½Ð°Ñ‚Ð° Ð¾Ð±ÐµÐ¼Ð½Ð° Ñ†ÐµÐ» Ð¸ Ð¿Ñ€Ð°Ð²Ð¸Ð»Ð°Ñ‚Ð° Ð·Ð° Ñ€Ð°Ð·Ð¿Ñ€ÐµÐ´ÐµÐ»ÐµÐ½Ð¸Ðµ Ð½Ð° ÑÐµÑÐ¸Ð¸Ñ‚Ðµ.",
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ð¡ÐµÐ·Ð¾Ð½Ð½Ð° Ñ†ÐµÐ»", f"{context['target_hours']:.0f} h", help=help_text("annual_goal"))
    c2.metric("Ð˜Ð·Ð¿ÑŠÐ»Ð½ÐµÐ½Ð¾", f"{context['completed_hours']:.1f} h", f"{context['progress_pct']:.1f}%")
    c3.metric("ÐÑƒÐ¶Ð½Ð¾ ÑÑ€ÐµÐ´Ð½Ð¾ Ð´Ð¾ ÐºÑ€Ð°Ñ", f"{context['required_weekly_hours']:.1f} h/ÑÐµÐ´Ð¼.")
    c4.metric("ÐžÐ±ÐµÐ¼ÐµÐ½ Ñ„Ð°ÐºÑ‚Ð¾Ñ€", f"{context['volume_factor']:.3f}", help=help_text("annual_goal"))

    tab_goal, tab_calendar, tab_structure = st.tabs(
        ["Ð¡ÐµÐ·Ð¾Ð½Ð½Ð° Ñ†ÐµÐ»", "Ð¡Ñ‚Ð°Ñ€Ñ‚Ð¾Ð²Ðµ, Ð»Ð°Ð³ÐµÑ€Ð¸ Ð¸ Ñ‚ÐµÑÑ‚Ð¾Ð²Ðµ", "Ð¡ÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð° ÑÑ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð°"]
    )

    with tab_goal:
        left, right = st.columns([1, 1.25])
        with left:
            with st.form(f"season_goal_{athlete_id}"):
                season_start = st.date_input(
                    "ÐÐ°Ñ‡Ð°Ð»Ð¾ Ð½Ð° Ð¾Ñ‚Ñ‡ÐµÑ‚Ð½Ð¸Ñ ÑÐµÐ·Ð¾Ð½",
                    value=pd.Timestamp(preferences["season_start"]).date(),
                    disabled=not can_edit,
                    help=help_text("annual_goal"),
                )
                season_end = st.date_input(
                    "ÐšÑ€Ð°Ð¹ Ð½Ð° Ð¾Ñ‚Ñ‡ÐµÑ‚Ð½Ð¸Ñ ÑÐµÐ·Ð¾Ð½",
                    value=pd.Timestamp(preferences["season_end"]).date(),
                    disabled=not can_edit,
                )
                annual_target = st.number_input(
                    "Ð¦ÐµÐ»ÐµÐ²Ð¸ Ð¾Ð±ÐµÐ¼ Ð·Ð° ÑÐµÐ·Ð¾Ð½Ð° Â· Ñ‡Ð°ÑÐ¾Ð²Ðµ",
                    min_value=50.0,
                    max_value=1500.0,
                    value=float(preferences["annual_target_hours"]),
                    step=10.0,
                    disabled=not can_edit,
                    help=(
                        "ÐÐ°Ð¿Ñ€Ð¸Ð¼ÐµÑ€ 600 Ñ‡Ð°ÑÐ° Ð·Ð° ÐµÐ´Ð½Ð° Ð³Ð¾Ð´Ð¸Ð½Ð°. Ð ÐµÐ°Ð»Ð½Ð°Ñ‚Ð° Ð»Ð¸Ð½Ð¸Ñ Ð¾ÑÑ‚Ð°Ð²Ð° Ð½ÐµÐ¿Ñ€Ð¾Ð¼ÐµÐ½ÐµÐ½Ð°, "
                        "Ð° Ð¿Ð»Ð°Ð½Ð¾Ð²Ð°Ñ‚Ð° Ð»Ð¸Ð½Ð¸Ñ ÑÐµ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»ÑÐ²Ð° Ð¿Ð»Ð°Ð²Ð½Ð¾, Ð¾ÑÐ½Ð¾Ð²Ð½Ð¾ Ñ‡Ñ€ÐµÐ· Z1â€“Z2."
                    ),
                )
                goal_influence = st.slider(
                    "Ð¢ÐµÐ¶ÐµÑÑ‚ Ð½Ð° Ð³Ð¾Ð´Ð¸ÑˆÐ½Ð°Ñ‚Ð° Ñ†ÐµÐ»",
                    0.0,
                    1.00,
                    float(preferences["annual_goal_influence"]),
                    0.05,
                    disabled=not can_edit,
                    help="0 Ð¾Ð·Ð½Ð°Ñ‡Ð°Ð²Ð° ÑÐ°Ð¼Ð¾ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸Ð¾Ð½Ð½Ð° Ñ†ÐµÐ»; Ð¿Ð¾-Ð²Ð¸ÑÐ¾ÐºÐ° ÑÑ‚Ð¾Ð¹Ð½Ð¾ÑÑ‚ Ð´Ð¾Ð¿ÑƒÑÐºÐ° Ð¾Ð³Ñ€Ð°Ð½Ð¸Ñ‡ÐµÐ½Ð° ÐºÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ Ð½Ð° ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð¸Ñ Ð¾Ð±ÐµÐ¼.",
                )
                max_factor = st.slider(
                    "ÐœÐ°ÐºÑÐ¸Ð¼Ð°Ð»Ð½Ð¾ ÑƒÐ²ÐµÐ»Ð¸Ñ‡ÐµÐ½Ð¸Ðµ Ð¾Ñ‚ Ð³Ð¾Ð´Ð¸ÑˆÐ½Ð°Ñ‚Ð° Ñ†ÐµÐ»",
                    1.00,
                    1.50,
                    float(preferences["max_volume_factor"]),
                    0.01,
                    disabled=not can_edit,
                )
                submit_goal = st.form_submit_button("Ð—Ð°Ð¿Ð°Ð·Ð¸ ÑÐµÐ·Ð¾Ð½Ð½Ð°Ñ‚Ð° Ñ†ÐµÐ»", disabled=not can_edit, width="stretch")
            if submit_goal:
                if pd.Timestamp(season_end) <= pd.Timestamp(season_start):
                    st.error("ÐšÑ€Ð°ÑÑ‚ Ð½Ð° ÑÐµÐ·Ð¾Ð½Ð° Ñ‚Ñ€ÑÐ±Ð²Ð° Ð´Ð° Ðµ ÑÐ»ÐµÐ´ Ð½Ð°Ñ‡Ð°Ð»Ð¾Ñ‚Ð¾.")
                else:
                    preferences["season_start"] = pd.Timestamp(season_start)
                    preferences["season_end"] = pd.Timestamp(season_end)
                    preferences["annual_target_hours"] = float(annual_target)
                    preferences["annual_goal_influence"] = float(goal_influence)
                    preferences["max_volume_factor"] = float(max_factor)
                    bundle["planning_preferences"][athlete_id] = normalize_preferences(
                        preferences, profile_code, date.today()
                    )
                    commit_bundle(
                        bundle,
                        "season_goal_update",
                        "Ð¡ÐµÐ·Ð¾Ð½Ð½Ð°Ñ‚Ð° Ð¾Ð±ÐµÐ¼Ð½Ð° Ñ†ÐµÐ» Ð¸ Ð¾Ñ‚Ñ‡ÐµÑ‚Ð½Ð¸ÑÑ‚ Ð¿ÐµÑ€Ð¸Ð¾Ð´ ÑÐ° Ð°ÐºÑ‚ÑƒÐ°Ð»Ð¸Ð·Ð¸Ñ€Ð°Ð½Ð¸.",
                        athlete_id,
                    )
        with right:
            st.plotly_chart(annual_goal_figure(context, analysis["volume_trajectory"]), width="stretch")
            st.plotly_chart(weekly_plan_vs_actual_figure(analysis["volume_trajectory"]), width="stretch")
            st.info(
                f"Ð¡Ñ‚Ð°Ñ‚ÑƒÑ: **{context['status']}**. {context.get('feasibility_status', '')}. "
                f"ÐŸÑ€Ð¸ Ð·Ð°Ð¿Ð°Ð·Ð²Ð°Ð½Ðµ Ð½Ð° Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ñ 4-ÑÐµÐ´Ð¼Ð¸Ñ‡ÐµÐ½ Ð¾Ð±ÐµÐ¼ Ð¿Ñ€Ð¾Ð³Ð½Ð¾Ð·Ð°Ñ‚Ð° Ðµ Ð¾ÐºÐ¾Ð»Ð¾ "
                f"**{context['forecast_hours']:.0f} h** Ð´Ð¾ ÐºÑ€Ð°Ñ Ð½Ð° Ð¿ÐµÑ€Ð¸Ð¾Ð´Ð°. "
                f"Ð’ÑŠÐ²ÐµÐ´ÐµÐ½Ð°Ñ‚Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð¿Ð¾ÐºÑ€Ð¸Ð²Ð° Ð¿Ñ€Ð¸Ð±Ð»Ð¸Ð·Ð¸Ñ‚ÐµÐ»Ð½Ð¾ **{context['season_history_coverage'] * 100:.0f}%** "
                "Ð¾Ñ‚ Ð¸Ð·Ð¼Ð¸Ð½Ð°Ð»Ð°Ñ‚Ð° Ñ‡Ð°ÑÑ‚ Ð½Ð° ÑÐµÐ·Ð¾Ð½Ð°. Ð¡Ð¸Ð½ÑÑ‚Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ‡ÐµÑÐºÐ° Ð»Ð¸Ð½Ð¸Ñ Ð½Ðµ ÑÐµ Ð¿Ñ€Ð¾Ð¼ÐµÐ½Ñ Ð¾Ñ‚ Ñ†ÐµÐ»Ñ‚Ð°; "
                "Ð¿Ñ€Ð¾Ð¼ÐµÐ½ÑÑ‚ ÑÐµ Ð±ÑŠÐ´ÐµÑ‰Ð¸ÑÑ‚ Ð°Ð´Ð°Ð¿Ñ‚Ð¸Ð²ÐµÐ½ Ð¿Ð»Ð°Ð½ Ð¸ Ñ†ÐµÐ»ÐµÐ²Ð°Ñ‚Ð° Ñ‚Ñ€Ð°ÐµÐºÑ‚Ð¾Ñ€Ð¸Ñ. Ð’Ð¸ÑÐ¾ÐºÐ¸Ñ‚Ðµ Ð¸Ð½Ñ‚ÐµÐ½Ð·Ð¸Ð²Ð½Ð¾ÑÑ‚Ð¸ "
                "Ð½Ðµ ÑÐµ ÑƒÐ²ÐµÐ»Ð¸Ñ‡Ð°Ð²Ð°Ñ‚ ÑÐ°Ð¼Ð¾ Ð·Ð° Ð´Ð¾ÑÑ‚Ð¸Ð³Ð°Ð½Ðµ Ð½Ð° Ñ‡Ð°ÑÐ¾Ð²Ð°Ñ‚Ð° Ñ†ÐµÐ»."
            )

    with tab_calendar:
        athlete_calendar = bundle["calendar"].loc[
            bundle["calendar"]["athlete_id"].astype(str) == athlete_id
        ].copy().sort_values("start_date")
        st.plotly_chart(calendar_timeline_figure(athlete_calendar), width="stretch")

        type_to_label = EVENT_TYPE_LABELS
        label_to_type = {label: code for code, label in type_to_label.items()}
        display = athlete_calendar.copy()
        display["type_label"] = display["type"].map(type_to_label).fillna(display["type"])
        display = display[
            ["event_id", "type_label", "name", "start_date", "end_date", "priority", "goal", "locked", "note"]
        ].rename(columns={"type_label": "Ð¢Ð¸Ð¿"}).reset_index(drop=True)
        edited = st.data_editor(
            display,
            width="stretch",
            hide_index=True,
            num_rows="dynamic" if can_edit else "fixed",
            disabled=["event_id"] if can_edit else list(display.columns),
            key=f"calendar_editor_{athlete_id}_{bundle['version']}",
            column_config={
                "event_id": st.column_config.TextColumn("ID", help="Ð¡ÑŠÐ·Ð´Ð°Ð²Ð° ÑÐµ Ð°Ð²Ñ‚Ð¾Ð¼Ð°Ñ‚Ð¸Ñ‡Ð½Ð¾ Ð·Ð° Ð½Ð¾Ð² Ñ€ÐµÐ´."),
                "Ð¢Ð¸Ð¿": st.column_config.SelectboxColumn("Ð¢Ð¸Ð¿", options=list(type_to_label.values()), required=True),
                "name": st.column_config.TextColumn("Ð˜Ð¼Ðµ", required=True),
                "start_date": st.column_config.DateColumn("ÐÐ°Ñ‡Ð°Ð»Ð¾", format="DD.MM.YYYY", required=True),
                "end_date": st.column_config.DateColumn("ÐšÑ€Ð°Ð¹", format="DD.MM.YYYY", required=True),
                "priority": st.column_config.SelectboxColumn("ÐŸÑ€Ð¸Ð¾Ñ€Ð¸Ñ‚ÐµÑ‚", options=["A", "B", "C"]),
                "goal": st.column_config.TextColumn("Ð¦ÐµÐ» / Ð°ÐºÑ†ÐµÐ½Ñ‚"),
                "locked": st.column_config.CheckboxColumn("Ð—Ð°ÐºÐ»ÑŽÑ‡ÐµÐ½Ð¾"),
                "note": st.column_config.TextColumn("Ð‘ÐµÐ»ÐµÐ¶ÐºÐ°"),
            },
        )
        st.caption(
            "Ð ÐµÐ´Ð¾Ð²ÐµÑ‚Ðµ Ð¼Ð¾Ð³Ð°Ñ‚ Ð´Ð° ÑÐµ Ð´Ð¾Ð±Ð°Ð²ÑÑ‚ Ð¸ Ð¸Ð·Ñ‚Ñ€Ð¸Ð²Ð°Ñ‚. ÐŸÑ€Ð¾Ð¼ÑÐ½Ð°Ñ‚Ð° Ð½Ð° Ð¾ÑÐ½Ð¾Ð²Ð½Ð¸Ñ ÑÑ‚Ð°Ñ€Ñ‚ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»ÑÐ²Ð° Ñ„Ð°Ð·Ð°Ñ‚Ð°, "
            "Ð¼ÐµÐ·Ð¾Ñ†Ð¸ÐºÐ»Ð¸Ñ‡Ð½Ð°Ñ‚Ð° Ð´Ð¸Ð½Ð°Ð¼Ð¸ÐºÐ° Ð¸ Ñ‚ÐµÐ¹Ð¿ÑŠÑ€Ð°; Ð»Ð°Ð³ÐµÑ€Ð¸Ñ‚Ðµ Ð¸ ÐºÐ¾Ð½Ñ‚Ñ€Ð¾Ð»Ð½Ð¸Ñ‚Ðµ ÑÑ‚Ð°Ñ€Ñ‚Ð¾Ð²Ðµ Ð¿Ñ€Ð¾Ð¼ÐµÐ½ÑÑ‚ ÐºÐ°Ð»ÐµÐ½Ð´Ð°Ñ€Ð½Ð¸Ñ Ñ„Ð°ÐºÑ‚Ð¾Ñ€."
        )
        if st.button("Ð—Ð°Ð¿Ð°Ð·Ð¸ ÐºÐ°Ð»ÐµÐ½Ð´Ð°Ñ€Ð° Ð¸ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»Ð¸", disabled=not can_edit, width="stretch"):
            rows: list[dict[str, Any]] = []
            errors: list[str] = []
            for row_index, row in edited.iterrows():
                name = str(row.get("name", "") or "").strip()
                if not name:
                    continue
                start_value = pd.to_datetime(row.get("start_date"), errors="coerce")
                end_value = pd.to_datetime(row.get("end_date"), errors="coerce")
                if pd.isna(start_value) or pd.isna(end_value):
                    errors.append(f"Ð ÐµÐ´ {row_index + 1}: Ð»Ð¸Ð¿ÑÐ²Ð° Ð²Ð°Ð»Ð¸Ð´Ð½Ð° Ð´Ð°Ñ‚Ð°.")
                    continue
                if end_value < start_value:
                    errors.append(f"Ð ÐµÐ´ {row_index + 1}: ÐºÑ€Ð°Ð¹Ð½Ð°Ñ‚Ð° Ð´Ð°Ñ‚Ð° Ðµ Ð¿Ñ€ÐµÐ´Ð¸ Ð½Ð°Ñ‡Ð°Ð»Ð½Ð°Ñ‚Ð°.")
                    continue
                label = str(row.get("Ð¢Ð¸Ð¿", ""))
                event_type = label_to_type.get(label, label if label in type_to_label else "TEST")
                event_id = str(row.get("event_id", "") or "").strip()
                if not event_id or event_id.lower() == "nan":
                    event_id = f"{athlete_id}-EV-{start_value.strftime('%Y%m%d')}-{row_index + 1}"
                locked_value = row.get("locked", False)
                locked = False if pd.isna(locked_value) else bool(locked_value)
                rows.append(
                    {
                        "event_id": event_id,
                        "athlete_id": athlete_id,
                        "type": event_type,
                        "name": name,
                        "start_date": pd.Timestamp(start_value).normalize(),
                        "end_date": pd.Timestamp(end_value).normalize(),
                        "priority": str(row.get("priority", "B") or "B"),
                        "goal": str(row.get("goal", "") or ""),
                        "locked": locked,
                        "note": str(row.get("note", "") or ""),
                    }
                )
            if errors:
                for error in errors:
                    st.error(error)
            else:
                other = bundle["calendar"].loc[
                    bundle["calendar"]["athlete_id"].astype(str) != athlete_id
                ].copy()
                athlete_new = pd.DataFrame(rows, columns=bundle["calendar"].columns)
                bundle["calendar"] = pd.concat([other, athlete_new], ignore_index=True).sort_values(
                    ["athlete_id", "start_date"]
                ).reset_index(drop=True)
                future_main = athlete_new.loc[
                    (athlete_new["type"] == "MAIN_RACE")
                    & (pd.to_datetime(athlete_new["start_date"]) >= pd.Timestamp.today().normalize())
                ]
                reason = "ÐšÐ°Ð»ÐµÐ½Ð´Ð°Ñ€ÑŠÑ‚ Ðµ Ð°ÐºÑ‚ÑƒÐ°Ð»Ð¸Ð·Ð¸Ñ€Ð°Ð½ Ð¸ Ð¿Ð»Ð°Ð½ÑŠÑ‚ Ðµ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»ÐµÐ½."
                if future_main.empty:
                    reason += " ÐÑÐ¼Ð° Ð±ÑŠÐ´ÐµÑ‰ Ð¾ÑÐ½Ð¾Ð²ÐµÐ½ ÑÑ‚Ð°Ñ€Ñ‚; Ð²Ñ€ÐµÐ¼ÐµÐ½Ð½Ð¾ ÑÐµ Ð¸Ð·Ð¿Ð¾Ð»Ð·Ð²Ð° Ð²Ð¸Ñ€Ñ‚ÑƒÐ°Ð»ÐµÐ½ 16-ÑÐµÐ´Ð¼Ð¸Ñ‡ÐµÐ½ Ñ…Ð¾Ñ€Ð¸Ð·Ð¾Ð½Ñ‚."
                commit_bundle(bundle, "calendar_update", reason, athlete_id)

    with tab_structure:
        weekday_options = list(WEEKDAY_LABELS.values())
        with st.form(f"weekly_structure_{athlete_id}"):
            c1, c2, c3 = st.columns(3)
            sessions_per_week = c1.number_input(
                "Ð‘Ñ€Ð¾Ð¹ Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÑŠÑ‡Ð½Ð¸ ÑÐµÑÐ¸Ð¸ ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð¾",
                min_value=1,
                max_value=14,
                value=int(preferences["sessions_per_week"]),
                step=1,
                disabled=not can_edit,
                help=help_text("weekly_structure"),
            )
            rest_days_labels = c1.multiselect(
                "Ð”Ð½Ð¸ Ð·Ð° Ð¿ÑŠÐ»Ð½Ð° Ð¿Ð¾Ñ‡Ð¸Ð²ÐºÐ°",
                weekday_options,
                default=[WEEKDAY_LABELS[day] for day in preferences["rest_days"]],
                disabled=not can_edit,
            )
            long_day_label = c1.selectbox(
                "Ð”ÐµÐ½ Ð·Ð° Ð´ÑŠÐ»Ð³Ð° Ð°ÐµÑ€Ð¾Ð±Ð½Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ°",
                weekday_options,
                index=weekday_options.index(WEEKDAY_LABELS[preferences["long_session_day"]]),
                disabled=not can_edit,
            )

            double_days_labels = c2.multiselect(
                "Ð Ð°Ð·Ñ€ÐµÑˆÐµÐ½Ð¸ Ð´Ð½Ð¸ Ñ Ð´Ð²Ðµ ÑÐµÑÐ¸Ð¸",
                weekday_options,
                default=[WEEKDAY_LABELS[day] for day in preferences["double_session_days"]],
                disabled=not can_edit,
            )
            intensity_days_labels = c2.multiselect(
                "ÐŸÑ€ÐµÐ´Ð¿Ð¾Ñ‡Ð¸Ñ‚Ð°Ð½Ð¸ Ð¸Ð½Ñ‚ÐµÐ½Ð·Ð¸Ð²Ð½Ð¸ Ð´Ð½Ð¸",
                weekday_options,
                default=[WEEKDAY_LABELS[day] for day in preferences["intensity_days"]],
                disabled=not can_edit,
            )
            strength_days_labels = c2.multiselect(
                "ÐŸÑ€ÐµÐ´Ð¿Ð¾Ñ‡Ð¸Ñ‚Ð°Ð½Ð¸ ÑÐ¸Ð»Ð¾Ð²Ð¸ Ð´Ð½Ð¸",
                weekday_options,
                default=[WEEKDAY_LABELS[day] for day in preferences["strength_days"]],
                disabled=not can_edit,
            )
            max_key = c2.number_input(
                "ÐœÐ°ÐºÑÐ¸Ð¼ÑƒÐ¼ ÐºÐ»ÑŽÑ‡Ð¾Ð²Ð¸ ÑÐµÑÐ¸Ð¸ ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð¾",
                min_value=0,
                max_value=8,
                value=int(preferences["max_key_sessions_per_week"]),
                step=1,
                disabled=not can_edit,
            )

            double_threshold = c3.checkbox(
                "Ð Ð°Ð·Ñ€ÐµÑˆÐ¸ Ð´Ð²Ð¾Ð¹Ð½Ð° Ð¿Ñ€Ð°Ð³Ð¾Ð²Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ°",
                value=bool(preferences["double_threshold_enabled"]),
                disabled=not can_edit,
                help=help_text("double_threshold"),
            )
            dt_day_label = c3.selectbox(
                "Ð”ÐµÐ½ Ð·Ð° Ð´Ð²Ð¾Ð¹Ð½Ð° Ð¿Ñ€Ð°Ð³Ð¾Ð²Ð°",
                weekday_options,
                index=weekday_options.index(WEEKDAY_LABELS[preferences["double_threshold_day"]]),
                disabled=not can_edit,
            )
            dt_components = c3.multiselect(
                "ÐŸÑ€Ð°Ð³Ð¾Ð²Ð° ÐºÐ¾Ð¼Ð±Ð¸Ð½Ð°Ñ†Ð¸Ñ",
                ["Z3", "Z4"],
                default=preferences["double_threshold_components"],
                disabled=not can_edit,
            )
            dt_readiness = c3.slider(
                "ÐœÐ¸Ð½Ð¸Ð¼Ð°Ð»Ð½Ð° Ð¸Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚",
                70.0,
                100.0,
                float(preferences["double_threshold_min_readiness"]),
                1.0,
                disabled=not can_edit,
            )
            dt_phase = c3.slider(
                "Ð”Ð¾Ð¿ÑƒÑÑ‚Ð¸Ð¼Ð° Ñ‡Ð°ÑÑ‚ Ð¾Ñ‚ Ð¿Ð¾Ð´Ð³Ð¾Ñ‚Ð¾Ð²ÐºÐ°Ñ‚Ð°",
                0.0,
                1.0,
                (
                    float(preferences["double_threshold_phase_min"]),
                    float(preferences["double_threshold_phase_max"]),
                ),
                0.05,
                disabled=not can_edit,
                help="0 = Ð½Ð°Ñ‡Ð°Ð»Ð¾ Ð½Ð° Ð¿Ð¾Ð´Ð³Ð¾Ñ‚Ð¾Ð²ÐºÐ°Ñ‚Ð°; 1 = Ð¾ÑÐ½Ð¾Ð²ÐµÐ½ ÑÑ‚Ð°Ñ€Ñ‚.",
            )
            submit_structure = st.form_submit_button(
                "Ð—Ð°Ð¿Ð°Ð·Ð¸ ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð°Ñ‚Ð° ÑÑ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð°", disabled=not can_edit, width="stretch"
            )

        if submit_structure:
            rest_codes = [WEEKDAY_BY_LABEL[label] for label in rest_days_labels]
            max_possible = 2 * (7 - len(rest_codes))
            dt_code = WEEKDAY_BY_LABEL[dt_day_label]
            errors: list[str] = []
            if max_possible < 1:
                errors.append("ÐÐµ Ð¼Ð¾Ð¶Ðµ Ð²ÑÐ¸Ñ‡ÐºÐ¸ Ð´Ð½Ð¸ Ð´Ð° Ð±ÑŠÐ´Ð°Ñ‚ Ð·Ð°Ð´Ð°Ð´ÐµÐ½Ð¸ ÐºÐ°Ñ‚Ð¾ Ð¿ÑŠÐ»Ð½Ð° Ð¿Ð¾Ñ‡Ð¸Ð²ÐºÐ°.")
            if int(sessions_per_week) > max_possible:
                errors.append(
                    f"ÐŸÑ€Ð¸ Ð¸Ð·Ð±Ñ€Ð°Ð½Ð¸Ñ‚Ðµ Ð¿Ð¾Ñ‡Ð¸Ð²Ð½Ð¸ Ð´Ð½Ð¸ ÑÐ° Ð²ÑŠÐ·Ð¼Ð¾Ð¶Ð½Ð¸ Ð¼Ð°ÐºÑÐ¸Ð¼ÑƒÐ¼ {max_possible} ÑÐµÑÐ¸Ð¸ (Ð´Ð¾ Ð´Ð²Ðµ Ð½Ð° Ð´ÐµÐ½)."
                )
            if double_threshold and dt_code in rest_codes:
                errors.append("Ð”ÐµÐ½ÑÑ‚ Ð·Ð° Ð´Ð²Ð¾Ð¹Ð½Ð° Ð¿Ñ€Ð°Ð³Ð¾Ð²Ð° Ð½Ðµ Ð¼Ð¾Ð¶Ðµ ÐµÐ´Ð½Ð¾Ð²Ñ€ÐµÐ¼ÐµÐ½Ð½Ð¾ Ð´Ð° Ðµ Ð´ÐµÐ½ Ð·Ð° Ð¿ÑŠÐ»Ð½Ð° Ð¿Ð¾Ñ‡Ð¸Ð²ÐºÐ°.")
            if double_threshold and int(max_key) < 2:
                errors.append("Ð—Ð° Ð´Ð²Ð¾Ð¹Ð½Ð° Ð¿Ñ€Ð°Ð³Ð¾Ð²Ð° ÑÐ° Ð½ÑƒÐ¶Ð½Ð¸ Ð¿Ð¾Ð½Ðµ Ð´Ð²Ðµ Ñ€Ð°Ð·Ñ€ÐµÑˆÐµÐ½Ð¸ ÐºÐ»ÑŽÑ‡Ð¾Ð²Ð¸ ÑÐµÑÐ¸Ð¸.")
            if errors:
                for error in errors:
                    st.error(error)
            else:
                preferences.update(
                    {
                        "sessions_per_week": int(sessions_per_week),
                        "rest_days": rest_codes,
                        "double_session_days": [WEEKDAY_BY_LABEL[label] for label in double_days_labels],
                        "long_session_day": WEEKDAY_BY_LABEL[long_day_label],
                        "intensity_days": [WEEKDAY_BY_LABEL[label] for label in intensity_days_labels],
                        "strength_days": [WEEKDAY_BY_LABEL[label] for label in strength_days_labels],
                        "max_key_sessions_per_week": int(max_key),
                        "double_threshold_enabled": bool(double_threshold),
                        "double_threshold_day": dt_code,
                        "double_threshold_components": dt_components or ["Z3", "Z4"],
                        "double_threshold_min_readiness": float(dt_readiness),
                        "double_threshold_phase_min": float(dt_phase[0]),
                        "double_threshold_phase_max": float(dt_phase[1]),
                    }
                )
                bundle["planning_preferences"][athlete_id] = normalize_preferences(
                    preferences, profile_code, date.today()
                )
                commit_bundle(
                    bundle,
                    "weekly_structure_update",
                    "Ð‘Ñ€Ð¾ÑÑ‚ ÑÐµÑÐ¸Ð¸, Ð¿Ð¾Ñ‡Ð¸Ð²Ð½Ð¸Ñ‚Ðµ Ð´Ð½Ð¸ Ð¸ Ð¿Ñ€Ð°Ð²Ð¸Ð»Ð°Ñ‚Ð° Ð·Ð° Ð´Ð²Ð¾Ð¹Ð½Ð¸ Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ¸ ÑÐ° Ð°ÐºÑ‚ÑƒÐ°Ð»Ð¸Ð·Ð¸Ñ€Ð°Ð½Ð¸.",
                    athlete_id,
                )

        st.subheader("ÐŸÑ€ÐµÐ´Ð²Ð°Ñ€Ð¸Ñ‚ÐµÐ»ÐµÐ½ ÑÐµÐ´Ð¼Ð¸Ñ‡ÐµÐ½ ÑÐºÐµÐ»ÐµÑ‚")
        preview = build_week_structure(date.today(), preferences).copy()
        preview["Ð¢Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ°"] = preview["planned_training"].map({True: "Ð”Ð°", False: "ÐŸÐ¾Ñ‡Ð¸Ð²ÐºÐ°"})
        st.dataframe(
            preview[["date", "day", "session_no", "time_of_day", "slot_type", "Ð¢Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ°"]],
            width="stretch",
            hide_index=True,
            column_config={"date": st.column_config.DateColumn("Ð”Ð°Ñ‚Ð°", format="DD.MM.YYYY")},
        )
        st.caption(
            "Ð¢Ð¾Ð²Ð° Ðµ ÑÑ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð½Ð¸ÑÑ‚ ÑÐºÐµÐ»ÐµÑ‚. ÐšÐ¾Ð½ÐºÑ€ÐµÑ‚Ð½Ð¸ÑÑ‚ Ñ„Ð¾ÐºÑƒÑ Ð¸ Ð¾Ð±ÐµÐ¼ ÑÐµ Ð¾Ð¿Ñ€ÐµÐ´ÐµÐ»ÑÑ‚ ÑÐ»ÐµÐ´ Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ° Ð½Ð° 7/40, "
            "Ð²ÑŠÐ·ÑÑ‚Ð°Ð½Ð¾Ð²ÑÐ²Ð°Ð½ÐµÑ‚Ð¾, Ð¼Ð¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³Ð°, Ñ‚ÐµÑÑ‚Ð¾Ð²ÐµÑ‚Ðµ, Ñ„Ð°Ð·Ð°Ñ‚Ð° Ð¸ ÐºÐ°Ð»ÐµÐ½Ð´Ð°Ñ€Ð½Ð¸Ñ‚Ðµ ÑÑŠÐ±Ð¸Ñ‚Ð¸Ñ."
        )


def render_history_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(analysis["athlete"]["athlete_id"])
    preferences = analysis["planning_preferences"]
    activities = bundle["activities"].loc[
        bundle["activities"]["athlete_id"].astype(str) == athlete_id
    ].copy()
    daily_history = daily_history_from_activities(bundle["activities"], athlete_id)
    total_minutes = 0.0
    if not daily_history.empty:
        total_minutes = float(daily_history[COMPONENTS].sum().sum())
    history_days = int((daily_history[COMPONENTS].sum(axis=1) > 0).sum()) if not daily_history.empty else 0

    page_header(
        "Ð˜ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð½Ð° Ð½Ð°Ñ‚Ð¾Ð²Ð°Ñ€Ð²Ð°Ð½ÐµÑ‚Ð¾ Ð¸ Ð½Ð°Ñ‡Ð°Ð»Ð½Ð¸ Ð´Ð°Ð½Ð½Ð¸",
        "Ð’ÑŠÐ²ÐµÐ´Ð¸ Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ð¿Ð¾ Ð·Ð¾Ð½Ð¸ Ð¸ ÑÐ¸Ð»Ð°. Ð˜ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð° ÑÑŠÐ·Ð´Ð°Ð²Ð° 40-Ð´Ð½ÐµÐ²Ð½Ð°Ñ‚Ð° Ð°Ð´Ð°Ð¿Ñ‚Ð°Ñ†Ð¸Ð¾Ð½Ð½Ð° Ð±Ð°Ð·Ð°, Tref, 7/40 Ð¸ Ð½Ð°Ñ‡Ð°Ð»Ð½Ð°Ñ‚Ð° Ð¿Ñ€Ð¾Ð³Ñ€Ð°Ð¼Ð°.",
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ÐÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ð¸", len(activities))
    c2.metric("Ð”Ð½Ð¸ Ñ Ð½Ð°Ñ‚Ð¾Ð²Ð°Ñ€Ð²Ð°Ð½Ðµ", history_days)
    c3.metric("ÐžÐ±Ñ‰Ð¾ Ð²ÑŠÐ²ÐµÐ´ÐµÐ½ Ð¾Ð±ÐµÐ¼", f"{total_minutes / 60.0:.1f} h")
    c4.metric(
        "ÐÐ°Ð´ÐµÐ¶Ð´Ð½Ð¾ÑÑ‚ Ð½Ð° 40-Ð´Ð½ÐµÐ²Ð½Ð°Ñ‚Ð° Ð±Ð°Ð·Ð°",
        f"{analysis['annual_context']['history_reliability'] * 100:.0f}%",
        help=help_text("manual_history"),
    )
    if history_days < 40:
        st.warning(
            "Ð˜Ð¼Ð° Ð¿Ð¾-Ð¼Ð°Ð»ÐºÐ¾ Ð¾Ñ‚ 40 Ð´Ð½Ð¸ Ñ Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ. Ð¡Ð¸ÑÑ‚ÐµÐ¼Ð°Ñ‚Ð° Ñ‰Ðµ Ñ€Ð°Ð±Ð¾Ñ‚Ð¸ ÑÑŠÑ ÑÑ‚Ð°Ð±Ð¸Ð»Ð¸Ð·Ð¸Ñ€Ð°Ñ‰Ð¸Ñ‚Ðµ Ð±Ð°Ð·Ð¾Ð²Ð¸ Ñ‚Ð¾Ð²Ð°Ñ€Ð¸, "
            "Ð½Ð¾ Ð½Ð°Ð´ÐµÐ¶Ð´Ð½Ð¾ÑÑ‚Ñ‚Ð° Ð½Ð° Ð¸Ð½Ð´Ð¸Ð²Ð¸Ð´ÑƒÐ°Ð»Ð½Ð¸Ñ 7/40 Ð¸ Tref Ñ‰Ðµ Ð±ÑŠÐ´Ðµ Ð¿Ð¾-Ð½Ð¸ÑÐºÐ°."
        )

    tab_add, tab_table, tab_weekly = st.tabs(
        ["Ð”Ð¾Ð±Ð°Ð²Ð¸ Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ°", "Ð¢Ð°Ð±Ð»Ð¸Ñ†Ð° Ð¸ CSV", "Ð‘ÑŠÑ€Ð·Ð¾ Ð²ÑŠÐ²ÐµÐ¶Ð´Ð°Ð½Ðµ Ð¿Ð¾ ÑÐµÐ´Ð¼Ð¸Ñ†Ð¸"]
    )

    with tab_add:
        with st.form(f"manual_activity_{athlete_id}"):
            c1, c2, c3 = st.columns(3)
            activity_date = c1.date_input(
                "Ð”Ð°Ñ‚Ð°",
                value=date.today() - timedelta(days=1),
                disabled=not can_edit,
            )
            sport = c1.text_input("Ð¡Ñ€ÐµÐ´ÑÑ‚Ð²Ð¾ / ÑÐ¿Ð¾Ñ€Ñ‚", value="Ð Ð¾Ð»ÐºÐ¾Ð²Ð¸ ÑÐºÐ¸", disabled=not can_edit)
            rpe = c1.slider("RPE Ð½Ð° ÑÐµÑÐ¸ÑÑ‚Ð°", 0.0, 10.0, 4.0, 0.5, disabled=not can_edit)
            z1 = c2.number_input("Z1 Â· Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸", 0.0, 600.0, 20.0, 5.0, disabled=not can_edit)
            z2 = c2.number_input("Z2 Â· Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸", 0.0, 600.0, 60.0, 5.0, disabled=not can_edit)
            z3 = c2.number_input("Z3 Â· Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸", 0.0, 180.0, 0.0, 2.0, disabled=not can_edit)
            z4 = c3.number_input("Z4 Â· Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸", 0.0, 120.0, 0.0, 1.0, disabled=not can_edit)
            z5 = c3.number_input("Z5 Â· Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸", 0.0, 90.0, 0.0, 1.0, disabled=not can_edit)

            st.markdown("**Ð¡Ð¸Ð»Ð¾Ð²Ð° Ñ€Ð°Ð±Ð¾Ñ‚Ð° Â· Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ð¿Ð¾ Ð²Ð¸Ð´**", help=help_text("strength_load"))
            s1, s2, s3, s4 = st.columns(4)
            str_stab = s1.number_input(
                "Ð¡Ñ‚Ð°Ð±Ð¸Ð»Ð¸Ð·Ð°Ñ†Ð¸Ñ Â· k 0.8",
                0.0,
                180.0,
                0.0,
                5.0,
                disabled=not can_edit,
                help="Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ã— 0.8.",
            )
            str_end = s2.number_input(
                "Ð¡Ð¸Ð»Ð¾Ð²Ð° Ð¸Ð·Ð´Ñ€ÑŠÐ¶Ð»Ð¸Ð²Ð¾ÑÑ‚ Â· k 1.0",
                0.0,
                180.0,
                0.0,
                5.0,
                disabled=not can_edit,
                help="Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ã— 1.0.",
            )
            str_max = s3.number_input(
                "ÐœÐ°ÐºÑÐ¸Ð¼Ð°Ð»Ð½Ð° ÑÐ¸Ð»Ð° Â· k 1.2",
                0.0,
                180.0,
                0.0,
                5.0,
                disabled=not can_edit,
                help="Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ã— 1.2.",
            )
            str_ply = s4.number_input(
                "ÐŸÐ»Ð¸Ð¾Ð¼ÐµÑ‚Ñ€Ð¸Ñ Â· k 1.4",
                0.0,
                120.0,
                0.0,
                5.0,
                disabled=not can_edit,
                help="Ð ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ã— 1.4.",
            )
            strength_real, strength_q, strength_k = strength_equivalent_minutes(
                {
                    "STR_STAB": str_stab,
                    "STR_END": str_end,
                    "STR_MAX": str_max,
                    "STR_PLY": str_ply,
                }
            )
            st.caption(
                f"Ð¡Ð¸Ð»Ð°: {strength_real:.1f} Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ â†’ {strength_q:.1f} ÐµÐºÐ²Ð¸Ð²Ð°Ð»ÐµÐ½Ñ‚Ð½Ð¸ Ð¼Ð¸Ð½ "
                f"(ÑÑ€ÐµÐ´ÐµÐ½ k {strength_k:.2f})."
            )
            note = st.text_area("Ð‘ÐµÐ»ÐµÐ¶ÐºÐ°", value="Ð ÑŠÑ‡Ð½Ð¾ Ð²ÑŠÐ²ÐµÐ´ÐµÐ½Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ°.", disabled=not can_edit)
            submit_activity = st.form_submit_button("Ð”Ð¾Ð±Ð°Ð²Ð¸ ÐºÑŠÐ¼ Ð¸ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð°", disabled=not can_edit, width="stretch")
        if submit_activity:
            table = pd.DataFrame(
                [
                    {
                        "date": activity_date,
                        "sport": sport,
                        "rpe": rpe,
                        "Z1": z1,
                        "Z2": z2,
                        "Z3": z3,
                        "Z4": z4,
                        "Z5": z5,
                        "STR_STAB": str_stab,
                        "STR_END": str_end,
                        "STR_MAX": str_max,
                        "STR_PLY": str_ply,
                        "note": note,
                    }
                ]
            )
            new_activity = daily_table_to_activities(
                table, athlete_id, preferences, source="manual_single_entry"
            )
            if new_activity.empty:
                st.error("Ð’ÑŠÐ²ÐµÐ´Ð¸ Ð¿Ð¾Ð½Ðµ ÐµÐ´Ð½Ð° Ð¼Ð¸Ð½ÑƒÑ‚Ð° Ð½Ð°Ñ‚Ð¾Ð²Ð°Ñ€Ð²Ð°Ð½Ðµ.")
            else:
                new_activity.loc[:, "activity_id"] = (
                    f"{athlete_id}-MAN-{pd.Timestamp(activity_date).strftime('%Y%m%d')}-V{bundle['version'] + 1}"
                )
                bundle["activities"] = pd.concat(
                    [bundle["activities"], new_activity], ignore_index=True
                ).sort_values(["athlete_id", "date"]).reset_index(drop=True)
                commit_bundle(
                    bundle,
                    "manual_activity_add",
                    "Ð ÑŠÑ‡Ð½Ð°Ñ‚Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ° Ðµ Ð´Ð¾Ð±Ð°Ð²ÐµÐ½Ð° Ð¸ Ð²ÑÐ¸Ñ‡ÐºÐ¸ Ð¼Ð¾Ð´ÐµÐ»Ð¸ ÑÐ° Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»ÐµÐ½Ð¸.",
                    athlete_id,
                )

    with tab_table:
        period = st.selectbox(
            "ÐŸÐµÑ€Ð¸Ð¾Ð´ Ð·Ð° Ñ€ÐµÐ´Ð°ÐºÑ†Ð¸Ñ",
            [40, 90, 180, 0],
            format_func=lambda value: "Ð¦ÑÐ»Ð°Ñ‚Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ" if value == 0 else f"ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð¸ {value} Ð´Ð½Ð¸",
            key=f"history_period_{athlete_id}",
        )
        if period == 0 or daily_history.empty:
            period_start = pd.Timestamp(daily_history["date"].min()).normalize() if not daily_history.empty else pd.Timestamp.today().normalize() - pd.Timedelta(days=39)
        else:
            period_start = pd.Timestamp.today().normalize() - pd.Timedelta(days=period - 1)
        period_end = pd.Timestamp.today().normalize()
        display_history = daily_history.loc[
            (pd.to_datetime(daily_history["date"]) >= period_start)
            & (pd.to_datetime(daily_history["date"]) <= period_end)
        ].copy().reset_index(drop=True)
        edited_history = st.data_editor(
            display_history,
            width="stretch",
            hide_index=True,
            num_rows="dynamic" if can_edit else "fixed",
            disabled=["STR", "STR_Q"] if can_edit else True,
            key=f"history_editor_{athlete_id}_{period}_{bundle['version']}",
            column_config={
                "date": st.column_config.DateColumn("Ð”Ð°Ñ‚Ð°", format="DD.MM.YYYY", required=True),
                "sport": st.column_config.TextColumn("Ð¡Ñ€ÐµÐ´ÑÑ‚Ð²Ð¾ / ÑÐ¿Ð¾Ñ€Ñ‚"),
                "rpe": st.column_config.NumberColumn("RPE", min_value=0.0, max_value=10.0, step=0.5),
                **{
                    component: st.column_config.NumberColumn(
                        f"{component} Â· Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½", min_value=0.0, max_value=700.0, step=1.0
                    )
                    for component in AEROBIC_COMPONENTS
                },
                "STR_STAB": st.column_config.NumberColumn(
                    "Ð¡Ñ‚Ð°Ð±Ð¸Ð»Ð¸Ð·Ð°Ñ†Ð¸Ñ Â· Ð¼Ð¸Ð½", min_value=0.0, max_value=300.0, step=1.0,
                    help="ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚ 0.8.",
                ),
                "STR_END": st.column_config.NumberColumn(
                    "Ð¡Ð¸Ð»Ð¾Ð²Ð° Ð¸Ð·Ð´Ñ€ÑŠÐ¶Ð»Ð¸Ð²Ð¾ÑÑ‚ Â· Ð¼Ð¸Ð½", min_value=0.0, max_value=300.0, step=1.0,
                    help="ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚ 1.0.",
                ),
                "STR_MAX": st.column_config.NumberColumn(
                    "ÐœÐ°ÐºÑÐ¸Ð¼Ð°Ð»Ð½Ð° ÑÐ¸Ð»Ð° Â· Ð¼Ð¸Ð½", min_value=0.0, max_value=240.0, step=1.0,
                    help="ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚ 1.2.",
                ),
                "STR_PLY": st.column_config.NumberColumn(
                    "ÐŸÐ»Ð¸Ð¾Ð¼ÐµÑ‚Ñ€Ð¸Ñ Â· Ð¼Ð¸Ð½", min_value=0.0, max_value=180.0, step=1.0,
                    help="ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚ 1.4.",
                ),
                "STR": st.column_config.NumberColumn(
                    "Ð¡Ð¸Ð»Ð° Â· Ð¾Ð±Ñ‰Ð¾ Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½", format="%.1f", help=help_text("strength_load")
                ),
                "STR_Q": st.column_config.NumberColumn(
                    "Ð¡Ð¸Ð»Ð° Â· ÐµÐºÐ². Ð¼Ð¸Ð½", format="%.1f", help=help_text("strength_load")
                ),
                "note": st.column_config.TextColumn("Ð‘ÐµÐ»ÐµÐ¶ÐºÐ°"),
            },
        )
        st.caption(
            f"â€žÐ—Ð°Ð¿Ð°Ð·Ð¸ Ñ‚Ð°Ð±Ð»Ð¸Ñ†Ð°Ñ‚Ð°â€œ Ð·Ð°Ð¼ÐµÐ½Ñ Ð¸ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð° Ð½Ð° ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚Ð° Ð·Ð° Ð¿ÐµÑ€Ð¸Ð¾Ð´Ð° {period_start.date()} â€“ {period_end.date()}. "
            "Ð ÐµÐ´Ð°ÐºÑ‚Ð¸Ñ€Ð°Ñ‚ ÑÐµ Ñ€ÐµÐ°Ð»Ð½Ð¸Ñ‚Ðµ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ð¿Ð¾ Ð·Ð¾Ð½Ð¸ Ð¸ Ð¿Ð¾ Ð²Ð¸Ð´ ÑÐ¸Ð»Ð°. ÐšÐ¾Ð»Ð¾Ð½Ð¸Ñ‚Ðµ â€žÐ¡Ð¸Ð»Ð° Â· Ð¾Ð±Ñ‰Ð¾â€œ Ð¸ â€žÐ¡Ð¸Ð»Ð° Â· ÐµÐºÐ².â€œ ÑÐµ Ð¸Ð·Ñ‡Ð¸ÑÐ»ÑÐ²Ð°Ñ‚ Ð°Ð²Ñ‚Ð¾Ð¼Ð°Ñ‚Ð¸Ñ‡Ð½Ð¾."
        )
        if st.button("Ð—Ð°Ð¿Ð°Ð·Ð¸ Ñ‚Ð°Ð±Ð»Ð¸Ñ†Ð°Ñ‚Ð° Ð¸ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»Ð¸", disabled=not can_edit, width="stretch"):
            try:
                normalized = daily_table_to_activities(
                    edited_history, athlete_id, preferences, source="manual_daily_table"
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                all_activities = bundle["activities"].copy()
                dates = pd.to_datetime(all_activities["date"]).dt.normalize()
                remove_mask = (
                    (all_activities["athlete_id"].astype(str) == athlete_id)
                    & (dates >= period_start)
                    & (dates <= period_end)
                )
                bundle["activities"] = pd.concat(
                    [all_activities.loc[~remove_mask], normalized], ignore_index=True
                ).sort_values(["athlete_id", "date"]).reset_index(drop=True)
                commit_bundle(
                    bundle,
                    "manual_history_replace",
                    "Ð”Ð½ÐµÐ²Ð½Ð°Ñ‚Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð·Ð° Ð¸Ð·Ð±Ñ€Ð°Ð½Ð¸Ñ Ð¿ÐµÑ€Ð¸Ð¾Ð´ Ðµ Ð·Ð°Ð¼ÐµÐ½ÐµÐ½Ð° Ð¸ Ð¼Ð¾Ð´ÐµÐ»ÑŠÑ‚ Ðµ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»ÐµÐ½.",
                    athlete_id,
                )

        st.divider()
        c1, c2 = st.columns([1, 1])
        with c1:
            st.download_button(
                "Ð˜Ð·Ñ‚ÐµÐ³Ð»Ð¸ CSV ÑˆÐ°Ð±Ð»Ð¾Ð½",
                dataframe_csv_bytes(history_template()),
                file_name="biathlon_history_template.csv",
                mime="text/csv",
                width="stretch",
            )
        with c2:
            uploaded = st.file_uploader(
                "ÐšÐ°Ñ‡Ð¸ CSV Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ",
                type=["csv"],
                key=f"history_upload_{athlete_id}",
                disabled=not can_edit,
            )
        if uploaded is not None:
            try:
                uploaded_df = pd.read_csv(uploaded)
            except Exception as exc:
                st.error(f"CSV Ñ„Ð°Ð¹Ð»ÑŠÑ‚ Ð½Ðµ Ð¼Ð¾Ð¶Ðµ Ð´Ð° Ð±ÑŠÐ´Ðµ Ð¿Ñ€Ð¾Ñ‡ÐµÑ‚ÐµÐ½: {exc}")
            else:
                st.dataframe(uploaded_df.head(20), width="stretch", hide_index=True)
                import_mode = st.radio(
                    "Ð ÐµÐ¶Ð¸Ð¼ Ð½Ð° Ð¸Ð¼Ð¿Ð¾Ñ€Ñ‚Ð°",
                    ["Ð”Ð¾Ð±Ð°Ð²Ð¸", "Ð—Ð°Ð¼ÐµÐ½Ð¸ Ð´Ð°Ñ‚Ð¸Ñ‚Ðµ Ð¾Ñ‚ Ñ„Ð°Ð¹Ð»Ð°", "Ð—Ð°Ð¼ÐµÐ½Ð¸ Ñ†ÑÐ»Ð°Ñ‚Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð½Ð° ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚Ð°"],
                    horizontal=True,
                    key=f"history_import_mode_{athlete_id}",
                )
                if st.button("Ð˜Ð¼Ð¿Ð¾Ñ€Ñ‚Ð¸Ñ€Ð°Ð¹ CSV", disabled=not can_edit, width="stretch"):
                    try:
                        imported = daily_table_to_activities(
                            uploaded_df, athlete_id, preferences, source="manual_csv_import"
                        )
                    except Exception as exc:
                        st.error(f"ÐÐµÐ²Ð°Ð»Ð¸Ð´ÐµÐ½ Ñ„Ð¾Ñ€Ð¼Ð°Ñ‚: {exc}")
                    else:
                        all_activities = bundle["activities"].copy()
                        keep = pd.Series(True, index=all_activities.index)
                        if import_mode == "Ð—Ð°Ð¼ÐµÐ½Ð¸ Ñ†ÑÐ»Ð°Ñ‚Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð½Ð° ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚Ð°":
                            keep &= all_activities["athlete_id"].astype(str) != athlete_id
                        elif import_mode == "Ð—Ð°Ð¼ÐµÐ½Ð¸ Ð´Ð°Ñ‚Ð¸Ñ‚Ðµ Ð¾Ñ‚ Ñ„Ð°Ð¹Ð»Ð°" and not imported.empty:
                            imported_dates = set(pd.to_datetime(imported["date"]).dt.normalize())
                            keep &= ~(
                                (all_activities["athlete_id"].astype(str) == athlete_id)
                                & pd.to_datetime(all_activities["date"]).dt.normalize().isin(imported_dates)
                            )
                        bundle["activities"] = pd.concat(
                            [all_activities.loc[keep], imported], ignore_index=True
                        ).sort_values(["athlete_id", "date"]).reset_index(drop=True)
                        commit_bundle(
                            bundle,
                            "history_csv_import",
                            f"CSV Ð¸ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð° Ðµ Ð¸Ð¼Ð¿Ð¾Ñ€Ñ‚Ð¸Ñ€Ð°Ð½Ð° Ð² Ñ€ÐµÐ¶Ð¸Ð¼ â€ž{import_mode}â€œ.",
                            athlete_id,
                        )

        with st.expander("Ð˜Ð·Ñ‡Ð¸ÑÑ‚Ð²Ð°Ð½Ðµ Ð½Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð°"):
            confirm_clear = st.checkbox(
                "ÐŸÐ¾Ñ‚Ð²ÑŠÑ€Ð¶Ð´Ð°Ð²Ð°Ð¼ Ð¸Ð·Ñ‚Ñ€Ð¸Ð²Ð°Ð½ÐµÑ‚Ð¾ Ð½Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð° Ð·Ð° Ñ‚Ð¾Ð·Ð¸ ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚",
                key=f"clear_history_confirm_{athlete_id}",
                disabled=not can_edit,
            )
            if st.button(
                "Ð˜Ð·Ñ‚Ñ€Ð¸Ð¹ Ð¸ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð° Ð½Ð° ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚Ð°",
                disabled=not can_edit or not confirm_clear,
                width="stretch",
            ):
                bundle["activities"] = bundle["activities"].loc[
                    bundle["activities"]["athlete_id"].astype(str) != athlete_id
                ].copy().reset_index(drop=True)
                commit_bundle(
                    bundle,
                    "history_clear",
                    "Ð˜ÑÑ‚Ð¾Ñ€Ð¸ÑÑ‚Ð° Ð½Ð° ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚Ð° Ðµ Ð¸Ð·Ñ‚Ñ€Ð¸Ñ‚Ð°. ÐŸÐ»Ð°Ð½ÑŠÑ‚ Ð²Ñ€ÐµÐ¼ÐµÐ½Ð½Ð¾ Ð¸Ð·Ð¿Ð¾Ð»Ð·Ð²Ð° ÑÑ‚Ð°Ð±Ð¸Ð»Ð¸Ð·Ð¸Ñ€Ð°Ñ‰Ð¸ Ð±Ð°Ð·Ð¾Ð²Ð¸ Ñ‚Ð¾Ð²Ð°Ñ€Ð¸.",
                    athlete_id,
                )

    with tab_weekly:
        st.warning(
            "Ð¢Ð¾Ð·Ð¸ Ñ€ÐµÐ¶Ð¸Ð¼ Ðµ Ð·Ð° Ð±ÑŠÑ€Ð·Ð¾ Ð¿ÑŠÑ€Ð²Ð¾Ð½Ð°Ñ‡Ð°Ð»Ð½Ð¾ ÑÑ‚Ð°Ñ€Ñ‚Ð¸Ñ€Ð°Ð½Ðµ, ÐºÐ¾Ð³Ð°Ñ‚Ð¾ Ñ€Ð°Ð·Ð¿Ð¾Ð»Ð°Ð³Ð°Ñˆ ÑÐ°Ð¼Ð¾ ÑÑŠÑ ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð¸ Ñ‚Ð¾Ñ‚Ð°Ð»Ð¸. "
            "Ð¡Ð¸ÑÑ‚ÐµÐ¼Ð°Ñ‚Ð° Ð³Ð¸ Ñ€Ð°Ð·Ð¿Ñ€ÐµÐ´ÐµÐ»Ñ Ð´ÐµÑ‚ÐµÑ€Ð¼Ð¸Ð½Ð¸Ñ€Ð°Ð½Ð¾ Ð¿Ð¾ Ð´Ð½Ð¸ Ð¸ Ð¼Ð°Ñ€ÐºÐ¸Ñ€Ð° Ð¸Ð·Ñ‚Ð¾Ñ‡Ð½Ð¸ÐºÐ° ÐºÐ°Ñ‚Ð¾ Ð¿Ñ€Ð¸Ð±Ð»Ð¸Ð·Ð¸Ñ‚ÐµÐ»ÐµÐ½. "
            "Ð—Ð° Ð½Ð°Ð¹-Ñ‚Ð¾Ñ‡ÐµÐ½ 7/40 Ð¸Ð·Ð¿Ð¾Ð»Ð·Ð²Ð°Ð¹ Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð´Ð½ÐµÐ²Ð½Ð¸ Ð´Ð°Ð½Ð½Ð¸."
        )
        last_monday = pd.Timestamp.today().normalize() - pd.Timedelta(days=pd.Timestamp.today().weekday() + 7)
        initial_weekly = pd.DataFrame(
            [
                {
                    "week_start": last_monday.date(),
                    "sessions": int(preferences["sessions_per_week"]),
                    "Z1": 120.0,
                    "Z2": 360.0,
                    "Z3": 30.0,
                    "Z4": 15.0,
                    "Z5": 6.0,
                    "STR_STAB": 10.0,
                    "STR_END": 25.0,
                    "STR_MAX": 10.0,
                    "STR_PLY": 0.0,
                    "rpe": 4.5,
                    "note": "ÐŸÑ€Ð¸Ð¼ÐµÑ€Ð½Ð° ÑÐµÐ´Ð¼Ð¸Ñ†Ð° â€” Ð¿Ñ€Ð¾Ð¼ÐµÐ½Ð¸ ÑÑ‚Ð¾Ð¹Ð½Ð¾ÑÑ‚Ð¸Ñ‚Ðµ.",
                }
            ]
        )
        weekly_editor = st.data_editor(
            initial_weekly,
            width="stretch",
            hide_index=True,
            num_rows="dynamic" if can_edit else "fixed",
            disabled=not can_edit,
            key=f"weekly_history_editor_{athlete_id}_{bundle['version']}",
            column_config={
                "week_start": st.column_config.DateColumn("ÐÐ°Ñ‡Ð°Ð»Ð¾ Ð½Ð° ÑÐµÐ´Ð¼Ð¸Ñ†Ð°Ñ‚Ð°", format="DD.MM.YYYY"),
                "sessions": st.column_config.NumberColumn("Ð¡ÐµÑÐ¸Ð¸", min_value=1, max_value=14, step=1),
                **{
                    component: st.column_config.NumberColumn(
                        f"{component} Â· Ð¾Ð±Ñ‰Ð¾ Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½", min_value=0.0, max_value=3000.0, step=5.0
                    )
                    for component in AEROBIC_COMPONENTS
                },
                "STR_STAB": st.column_config.NumberColumn(
                    "Ð¡Ñ‚Ð°Ð±Ð¸Ð»Ð¸Ð·Ð°Ñ†Ð¸Ñ Â· Ð¾Ð±Ñ‰Ð¾ Ð¼Ð¸Ð½", min_value=0.0, max_value=1000.0, step=5.0,
                    help="k = 0.8",
                ),
                "STR_END": st.column_config.NumberColumn(
                    "Ð¡Ð¸Ð»Ð¾Ð²Ð° Ð¸Ð·Ð´Ñ€ÑŠÐ¶Ð»Ð¸Ð²Ð¾ÑÑ‚ Â· Ð¾Ð±Ñ‰Ð¾ Ð¼Ð¸Ð½", min_value=0.0, max_value=1000.0, step=5.0,
                    help="k = 1.0",
                ),
                "STR_MAX": st.column_config.NumberColumn(
                    "ÐœÐ°ÐºÑÐ¸Ð¼Ð°Ð»Ð½Ð° ÑÐ¸Ð»Ð° Â· Ð¾Ð±Ñ‰Ð¾ Ð¼Ð¸Ð½", min_value=0.0, max_value=600.0, step=5.0,
                    help="k = 1.2",
                ),
                "STR_PLY": st.column_config.NumberColumn(
                    "ÐŸÐ»Ð¸Ð¾Ð¼ÐµÑ‚Ñ€Ð¸Ñ Â· Ð¾Ð±Ñ‰Ð¾ Ð¼Ð¸Ð½", min_value=0.0, max_value=400.0, step=5.0,
                    help="k = 1.4",
                ),
                "rpe": st.column_config.NumberColumn("Ð¡Ñ€ÐµÐ´Ð½Ð¾ RPE", min_value=0.0, max_value=10.0, step=0.5),
                "note": st.column_config.TextColumn("Ð‘ÐµÐ»ÐµÐ¶ÐºÐ°"),
            },
        )
        weekly_strength_real, weekly_strength_q, weekly_strength_k = strength_equivalent_minutes(
            {
                strength_type: float(weekly_editor[strength_type].fillna(0.0).sum())
                for strength_type in STRENGTH_TYPES
            }
        )
        st.caption(
            f"Ð¡Ð¸Ð»Ð¾Ð² ÑÐ±Ð¾Ñ€ Ð² Ñ‚Ð°Ð±Ð»Ð¸Ñ†Ð°Ñ‚Ð°: {weekly_strength_real:.1f} Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ â†’ "
            f"{weekly_strength_q:.1f} ÐµÐºÐ². Ð¼Ð¸Ð½ (ÑÑ€ÐµÐ´ÐµÐ½ k {weekly_strength_k:.2f})."
        )
        weekly_mode = st.radio(
            "Ð ÐµÐ¶Ð¸Ð¼",
            ["Ð”Ð¾Ð±Ð°Ð²Ð¸", "Ð—Ð°Ð¼ÐµÐ½Ð¸ ÑÐµÐ´Ð¼Ð¸Ñ†Ð¸Ñ‚Ðµ Ð¾Ñ‚ Ñ‚Ð°Ð±Ð»Ð¸Ñ†Ð°Ñ‚Ð°"],
            horizontal=True,
            key=f"weekly_history_mode_{athlete_id}",
        )
        if st.button("Ð¡ÑŠÐ·Ð´Ð°Ð¹ Ð´Ð½ÐµÐ²Ð½Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð¾Ñ‚ ÑÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð¸Ñ‚Ðµ Ð¾Ð±ÐµÐ¼Ð¸", disabled=not can_edit, width="stretch"):
            try:
                generated = weekly_totals_to_activities(weekly_editor, athlete_id, preferences)
            except Exception as exc:
                st.error(f"Ð¡ÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð¸Ñ‚Ðµ Ð´Ð°Ð½Ð½Ð¸ Ð½Ðµ Ð¼Ð¾Ð³Ð°Ñ‚ Ð´Ð° Ð±ÑŠÐ´Ð°Ñ‚ Ð¿Ñ€ÐµÐ¾Ð±Ñ€Ð°Ð·ÑƒÐ²Ð°Ð½Ð¸: {exc}")
            else:
                all_activities = bundle["activities"].copy()
                keep = pd.Series(True, index=all_activities.index)
                if weekly_mode == "Ð—Ð°Ð¼ÐµÐ½Ð¸ ÑÐµÐ´Ð¼Ð¸Ñ†Ð¸Ñ‚Ðµ Ð¾Ñ‚ Ñ‚Ð°Ð±Ð»Ð¸Ñ†Ð°Ñ‚Ð°" and not generated.empty:
                    min_date = pd.to_datetime(generated["date"]).min().normalize()
                    max_date = pd.to_datetime(generated["date"]).max().normalize()
                    dates = pd.to_datetime(all_activities["date"]).dt.normalize()
                    keep &= ~(
                        (all_activities["athlete_id"].astype(str) == athlete_id)
                        & (dates >= min_date)
                        & (dates <= max_date)
                    )
                bundle["activities"] = pd.concat(
                    [all_activities.loc[keep], generated], ignore_index=True
                ).sort_values(["athlete_id", "date"]).reset_index(drop=True)
                commit_bundle(
                    bundle,
                    "weekly_history_import",
                    "Ð¡ÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð¸Ñ‚Ðµ Ð¾Ð±ÐµÐ¼Ð¸ ÑÐ° Ñ€Ð°Ð·Ð¿Ñ€ÐµÐ´ÐµÐ»ÐµÐ½Ð¸ Ð² Ð´Ð½ÐµÐ²Ð½Ð° Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð¸ Ð¼Ð¾Ð´ÐµÐ»ÑŠÑ‚ Ðµ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»ÐµÐ½.",
                    athlete_id,
                )


def render_monitoring_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(analysis["athlete"]["athlete_id"])
    page_header("Ð”Ð½ÐµÐ²ÐµÐ½ Ð¼Ð¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³", "Ð ÑŠÑ‡Ð½Ð¸ ÑÑƒÐ±ÐµÐºÑ‚Ð¸Ð²Ð½Ð¸ Ð¸ Ð¾Ð±ÐµÐºÑ‚Ð¸Ð²Ð½Ð¸ Ð¿Ð¾ÐºÐ°Ð·Ð°Ñ‚ÐµÐ»Ð¸ Ñ Ñ‚ÐµÐºÑƒÑ‰Ð° ÑÑ‚Ð¾Ð¹Ð½Ð¾ÑÑ‚, 7/40 Ñ‚ÐµÐ½Ð´ÐµÐ½Ñ†Ð¸Ñ Ð¸ ÐºÑ€Ð¸Ñ‚Ð¸Ñ‡Ð½Ð¸ Ñ„Ð»Ð°Ð³Ð¾Ð²Ðµ.")
    athlete_wellness = bundle["wellness"].loc[bundle["wellness"]["athlete_id"] == athlete_id].sort_values("date")
    latest = athlete_wellness.iloc[-1]

    with st.form("wellness_form"):
        st.subheader(f"Ð¡ÑƒÑ‚Ñ€ÐµÑˆÐµÐ½ Ð·Ð°Ð¿Ð¸Ñ Â· {date.today().strftime('%d.%m.%Y')}")
        c1, c2, c3 = st.columns(3)
        sleep_quality = c1.slider("ÐšÐ°Ñ‡ÐµÑÑ‚Ð²Ð¾ Ð½Ð° ÑÑŠÐ½Ñ", 1.0, 10.0, float(latest["sleep_quality"]), 0.5, help=help_text("monitoring"), disabled=not can_edit)
        fatigue = c1.slider("ÐžÐ±Ñ‰Ð° ÑƒÐ¼Ð¾Ñ€Ð°", 0.0, 10.0, float(latest["fatigue"]), 0.5, help=help_text("monitoring"), disabled=not can_edit)
        stress = c1.slider("ÐŸÑÐ¸Ñ…Ð¾Ð»Ð¾Ð³Ð¸Ñ‡ÐµÑÐºÐ¸ ÑÑ‚Ñ€ÐµÑ", 0.0, 10.0, float(latest["stress"]), 0.5, disabled=not can_edit)
        soreness_legs = c2.slider("Ð‘Ð¾Ð»ÐµÐ·Ð½ÐµÐ½Ð¾ÑÑ‚ Â· ÐºÑ€Ð°ÐºÐ°", 0.0, 10.0, float(latest["soreness_legs"]), 0.5, disabled=not can_edit)
        soreness_upper = c2.slider("Ð‘Ð¾Ð»ÐµÐ·Ð½ÐµÐ½Ð¾ÑÑ‚ Â· Ð³Ð¾Ñ€Ð½Ð° Ñ‡Ð°ÑÑ‚", 0.0, 10.0, float(latest["soreness_upper"]), 0.5, disabled=not can_edit)
        pain = c2.slider("Ð‘Ð¾Ð»ÐºÐ° / ÑÐ¸Ð¼Ð¿Ñ‚Ð¾Ð¼", 0.0, 10.0, float(latest["pain"]), 0.5, help=help_text("monitoring"), disabled=not can_edit)
        motivation = c3.slider("ÐœÐ¾Ñ‚Ð¸Ð²Ð°Ñ†Ð¸Ñ / Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚", 1.0, 10.0, float(latest["motivation"]), 0.5, disabled=not can_edit)
        morning_hr = c3.number_input("Ð¡ÑƒÑ‚Ñ€ÐµÑˆÐµÐ½ Ð¿ÑƒÐ»Ñ", 30.0, 120.0, float(latest["morning_hr"]), 1.0, disabled=not can_edit)
        hrv = c3.number_input("HRV Â· ms", 5.0, 250.0, float(latest["hrv"]), 1.0, disabled=not can_edit)
        sleep_hours = c3.number_input("ÐŸÑ€Ð¾Ð´ÑŠÐ»Ð¶Ð¸Ñ‚ÐµÐ»Ð½Ð¾ÑÑ‚ Ð½Ð° ÑÑŠÐ½Ñ Â· h", 3.0, 12.0, float(latest["sleep_hours"]), 0.25, disabled=not can_edit)
        illness = st.checkbox("Ð¡Ð¸Ð¼Ð¿Ñ‚Ð¾Ð¼Ð¸ / Ð·Ð°Ð±Ð¾Ð»ÑÐ²Ð°Ð½Ðµ", value=bool(latest.get("illness", False)), disabled=not can_edit)
        note = st.text_area("Ð‘ÐµÐ»ÐµÐ¶ÐºÐ°", value="", disabled=not can_edit)
        submitted = st.form_submit_button("Ð—Ð°Ð¿Ð¸ÑˆÐ¸ Ð¸ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»Ð¸", disabled=not can_edit, width="stretch")

    if submitted:
        values = {
            "athlete_id": athlete_id,
            "date": pd.Timestamp(date.today()),
            "sleep_quality": sleep_quality,
            "fatigue": fatigue,
            "soreness_legs": soreness_legs,
            "soreness_upper": soreness_upper,
            "stress": stress,
            "motivation": motivation,
            "pain": pain,
            "illness": illness,
            "morning_hr": morning_hr,
            "hrv": hrv,
            "sleep_hours": sleep_hours,
            "weight_kg": float(latest["weight_kg"]),
            "session_rpe": float(latest.get("session_rpe", fatigue)),
            "execution_quality": int(latest.get("execution_quality", 4)),
            "source": "manual_streamlit",
            "reliability": 1.0,
            "note": note,
        }
        mask = (bundle["wellness"]["athlete_id"] == athlete_id) & (pd.to_datetime(bundle["wellness"]["date"]).dt.date == date.today())
        if mask.any():
            for key, value in values.items():
                bundle["wellness"].loc[mask, key] = value
        else:
            bundle["wellness"] = pd.concat([bundle["wellness"], pd.DataFrame([values])], ignore_index=True)
        commit_bundle(bundle, "wellness_update", "Ð¡ÑƒÑ‚Ñ€ÐµÑˆÐ½Ð¸Ñ‚Ðµ Ð¿Ð¾ÐºÐ°Ð·Ð°Ñ‚ÐµÐ»Ð¸ ÑÐ° Ð·Ð°Ð¿Ð¸ÑÐ°Ð½Ð¸ Ð¸ Ð±ÑŠÐ´ÐµÑ‰Ð¸ÑÑ‚ Ð¿Ð»Ð°Ð½ Ðµ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»ÐµÐ½.", athlete_id)

    details = analysis["metric_details"].reset_index().rename(
        columns={
            "label": "ÐŸÐ¾ÐºÐ°Ð·Ð°Ñ‚ÐµÐ»",
            "current": "Ð¢ÐµÐºÑƒÑ‰Ð¾",
            "mean7": "Ð¡Ñ€ÐµÐ´Ð½Ð¾ 7 Ð´Ð½Ð¸",
            "mean40": "Ð¡Ñ€ÐµÐ´Ð½Ð¾ 40 Ð´Ð½Ð¸",
            "index_7_40": "7/40",
            "z_favorable": "ÐŸÐ¾ÑÐ¾Ñ‡Ð½Ð¾ Z",
            "score": "ÐžÑ†ÐµÐ½ÐºÐ°",
            "reliability": "ÐÐ°Ð´ÐµÐ¶Ð´Ð½Ð¾ÑÑ‚",
            "critical": "ÐšÑ€Ð¸Ñ‚Ð¸Ñ‡ÐµÐ½ Ñ„Ð»Ð°Ð³",
        }
    )
    details["ÐÐ°Ð´ÐµÐ¶Ð´Ð½Ð¾ÑÑ‚"] *= 100.0
    st.dataframe(
        details[["ÐŸÐ¾ÐºÐ°Ð·Ð°Ñ‚ÐµÐ»", "Ð¢ÐµÐºÑƒÑ‰Ð¾", "Ð¡Ñ€ÐµÐ´Ð½Ð¾ 7 Ð´Ð½Ð¸", "Ð¡Ñ€ÐµÐ´Ð½Ð¾ 40 Ð´Ð½Ð¸", "7/40", "ÐŸÐ¾ÑÐ¾Ñ‡Ð½Ð¾ Z", "ÐžÑ†ÐµÐ½ÐºÐ°", "ÐÐ°Ð´ÐµÐ¶Ð´Ð½Ð¾ÑÑ‚", "ÐšÑ€Ð¸Ñ‚Ð¸Ñ‡ÐµÐ½ Ñ„Ð»Ð°Ð³"]],
        width="stretch",
        hide_index=True,
        column_config={
            "7/40": st.column_config.NumberColumn(format="%.2f", help=help_text("monitoring")),
            "ÐžÑ†ÐµÐ½ÐºÐ°": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
            "ÐÐ°Ð´ÐµÐ¶Ð´Ð½Ð¾ÑÑ‚": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f", help=help_text("quality")),
        },
    )

    metric = st.selectbox(
        "ÐŸÐ¾ÐºÐ°Ð·Ð°Ñ‚ÐµÐ» Ð·Ð° Ð³Ñ€Ð°Ñ„Ð¸ÐºÐ°",
        list(METRIC_DEFINITIONS),
        format_func=lambda m: METRIC_DEFINITIONS[m]["label"],
        key="monitor_chart_metric",
    )
    st.plotly_chart(monitoring_history_figure(bundle["wellness"], athlete_id, metric), width="stretch")


def render_tests_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(analysis["athlete"]["athlete_id"])
    page_header("ÐšÐ¾Ð½Ñ‚Ñ€Ð¾Ð»Ð½Ð¸ Ñ‚ÐµÑÑ‚Ð¾Ð²Ðµ", "Ð ÑŠÑ‡Ð½Ð¸ Ñ€ÐµÐ·ÑƒÐ»Ñ‚Ð°Ñ‚Ð¸, Ð²Ð°Ð»Ð¸Ð´Ð½Ð¾ÑÑ‚, ÑÑ€Ð°Ð²Ð½Ð¸Ð¼Ð¾ÑÑ‚, Ð´Ð¸Ð½Ð°Ð¼Ð¸ÐºÐ° Ð¸ Ð¾Ð³Ñ€Ð°Ð½Ð¸Ñ‡ÐµÐ½Ð¾ ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚Ð½Ð¾ Ð²Ð»Ð¸ÑÐ½Ð¸Ðµ.")
    test_code = st.selectbox("Ð¢ÐµÑÑ‚", list(TEST_DEFINITIONS), format_func=lambda code: TEST_DEFINITIONS[code]["label"], key="test_code")
    definition = TEST_DEFINITIONS[test_code]
    st.plotly_chart(test_history_figure(bundle["tests"], athlete_id, test_code), width="stretch")

    history = bundle["tests"].loc[(bundle["tests"]["athlete_id"] == athlete_id) & (bundle["tests"]["test_code"] == test_code)].sort_values("date")
    latest = history.iloc[-1]
    with st.form("test_entry_form"):
        c1, c2, c3 = st.columns(3)
        test_date = c1.date_input("Ð”Ð°Ñ‚Ð°", value=date.today(), disabled=not can_edit)
        primary = c1.number_input(
            f"{definition['primary_label']} Â· {definition['primary_unit']}",
            value=float(latest["primary_value"]),
            step=0.1,
            disabled=not can_edit,
        )
        secondary = c2.number_input(
            f"{definition['secondary_label']} Â· {definition['secondary_unit']}",
            value=float(latest["secondary_value"]),
            step=0.1,
            disabled=not can_edit,
        )
        comparability = c2.slider("Ð¡Ñ€Ð°Ð²Ð½Ð¸Ð¼Ð¾ÑÑ‚", 0.0, 1.0, 0.95, 0.05, help=help_text("tests"), disabled=not can_edit)
        valid = c3.checkbox("Ð’Ð°Ð»Ð¸Ð´ÐµÐ½ Ñ‚ÐµÑÑ‚", value=True, disabled=not can_edit)
        protocol = c3.text_input("Ð’ÐµÑ€ÑÐ¸Ñ Ð½Ð° Ð¿Ñ€Ð¾Ñ‚Ð¾ÐºÐ¾Ð»Ð°", value="1.0", disabled=not can_edit)
        note = st.text_area("Ð£ÑÐ»Ð¾Ð²Ð¸Ñ Ð¸ Ð±ÐµÐ»ÐµÐ¶ÐºÐ°", value="Ð ÑŠÑ‡Ð½Ð¾ Ð²ÑŠÐ²ÐµÐ´ÐµÐ½ Ñ‚ÐµÑÑ‚Ð¾Ð² Ñ€ÐµÐ·ÑƒÐ»Ñ‚Ð°Ñ‚.", disabled=not can_edit)
        submitted = st.form_submit_button("Ð”Ð¾Ð±Ð°Ð²Ð¸ Ñ‚ÐµÑÑ‚ Ð¸ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»Ð¸", disabled=not can_edit, width="stretch")

    if submitted:
        new_row = {
            "test_id": f"{athlete_id}-{test_code}-{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}",
            "athlete_id": athlete_id,
            "date": pd.Timestamp(test_date),
            "test_code": test_code,
            "protocol_version": protocol,
            "primary_value": primary,
            "secondary_value": secondary,
            "valid": valid,
            "comparability": comparability,
            "conditions": note,
            "note": note,
        }
        bundle["tests"] = pd.concat([bundle["tests"], pd.DataFrame([new_row])], ignore_index=True)
        commit_bundle(bundle, "test_add", f"Ð”Ð¾Ð±Ð°Ð²ÐµÐ½ Ðµ Ð½Ð¾Ð² Ñ€ÐµÐ·ÑƒÐ»Ñ‚Ð°Ñ‚ Ð·Ð° â€ž{definition['label']}â€œ.", athlete_id)

    st.subheader("ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð° Ð²Ð°Ð»Ð¸Ð´Ð½Ð° Ð´Ð¸Ð½Ð°Ð¼Ð¸ÐºÐ°")
    if analysis["test_details"].empty:
        st.info("ÐÑÐ¼Ð° Ð´Ð¾ÑÑ‚Ð°Ñ‚ÑŠÑ‡Ð½Ð¾ Ð´Ð°Ð½Ð½Ð¸ Ð·Ð° ÑÑ€Ð°Ð²Ð½ÐµÐ½Ð¸Ðµ.")
    else:
        display = analysis["test_details"].reset_index().rename(
            columns={
                "label": "Ð¢ÐµÑÑ‚",
                "date": "Ð”Ð°Ñ‚Ð°",
                "primary_change_pct": "ÐŸÑ€Ð¾Ð¼ÑÐ½Ð° Â· Ð¾ÑÐ½Ð¾Ð²ÐµÐ½ %",
                "secondary_change_pct": "ÐŸÑ€Ð¾Ð¼ÑÐ½Ð° Â· Ð²Ñ‚Ð¾Ñ€Ð¸Ñ‡ÐµÐ½ %",
                "composite_change_pct": "ÐšÐ¾Ð¼Ð¿Ð»ÐµÐºÑÐ½Ð° Ð¿Ñ€Ð¾Ð¼ÑÐ½Ð° %",
                "comparability": "Ð¡Ñ€Ð°Ð²Ð½Ð¸Ð¼Ð¾ÑÑ‚",
                "reliability": "ÐÐ°Ð´ÐµÐ¶Ð´Ð½Ð¾ÑÑ‚",
                "valid": "Ð’Ð°Ð»Ð¸Ð´ÐµÐ½",
            }
        )
        st.dataframe(display[["Ð¢ÐµÑÑ‚", "Ð”Ð°Ñ‚Ð°", "ÐŸÑ€Ð¾Ð¼ÑÐ½Ð° Â· Ð¾ÑÐ½Ð¾Ð²ÐµÐ½ %", "ÐŸÑ€Ð¾Ð¼ÑÐ½Ð° Â· Ð²Ñ‚Ð¾Ñ€Ð¸Ñ‡ÐµÐ½ %", "ÐšÐ¾Ð¼Ð¿Ð»ÐµÐºÑÐ½Ð° Ð¿Ñ€Ð¾Ð¼ÑÐ½Ð° %", "Ð¡Ñ€Ð°Ð²Ð½Ð¸Ð¼Ð¾ÑÑ‚", "ÐÐ°Ð´ÐµÐ¶Ð´Ð½Ð¾ÑÑ‚", "Ð’Ð°Ð»Ð¸Ð´ÐµÐ½"]], width="stretch", hide_index=True)
    adjustments = analysis["test_adjustments"].rename("ÐšÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ").reset_index().rename(columns={"index": "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚"})
    adjustments["ÐšÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ"] *= 100.0
    st.dataframe(adjustments, width="stretch", hide_index=True)
    st.caption("ÐŸÐ¾Ð»Ð¾Ð¶Ð¸Ñ‚ÐµÐ»Ð½Ð°Ñ‚Ð° ÐºÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ Ðµ Ð¾Ð³Ñ€Ð°Ð½Ð¸Ñ‡ÐµÐ½Ð° Ð´Ð¾ +5%, Ð¾Ñ‚Ñ€Ð¸Ñ†Ð°Ñ‚ÐµÐ»Ð½Ð°Ñ‚Ð° â€” Ð´Ð¾ âˆ’10%, Ð¸ ÑÐµ Ð¿Ñ€Ð¸Ð»Ð°Ð³Ð° ÑÐ°Ð¼Ð¾ Ð² ÐºÐ¾Ð½Ñ‚ÐµÐºÑÑ‚Ð° Ð½Ð° Ñ‚ÐµÐºÑƒÑ‰Ð°Ñ‚Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚.")


def comparison_metrics(before: dict[str, Any], after: dict[str, Any], component: str) -> None:
    before_first = before["weekly_targets"].loc[(before["weekly_targets"]["week_no"] == 1) & (before["weekly_targets"]["component"] == component)].iloc[0]
    after_first = after["weekly_targets"].loc[(after["weekly_targets"]["week_no"] == 1) & (after["weekly_targets"]["component"] == component)].iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    b = float(before["load_stats"].loc[component, "index_7_40"])
    a = float(after["load_stats"].loc[component, "index_7_40"])
    c1.metric(f"{component} 7/40", f"{a:.2f}", f"{a-b:+.2f}", help=help_text("seven_forty"))
    b = float(before["load_readiness"].loc[component, "readiness"])
    a = float(after["load_readiness"].loc[component, "readiness"])
    c2.metric(f"{component} readiness", f"{a:.0f}%", f"{a-b:+.0f} Ð¿.Ð¿.", help=help_text("readiness"))
    b = float(before_first["target_effective_week"])
    a = float(after_first["target_effective_week"])
    c3.metric("Ð¡ÐµÐ´Ð¼Ð¸Ñ‡Ð½Ð° Ñ†ÐµÐ» E", f"{a:.1f}", f"{a-b:+.1f}", help=help_text("weekly_target"))
    b = float(before["integrated"].loc[component, "adaptive_multiplier"])
    a = float(after["integrated"].loc[component, "adaptive_multiplier"])
    c4.metric("ÐÐ´Ð°Ð¿Ñ‚Ð¸Ð²ÐµÐ½ Ð¼Ð½Ð¾Ð¶Ð¸Ñ‚ÐµÐ»", f"{a:.2f}", f"{a-b:+.2f}", help=help_text("adaptive_multiplier"))


def plan_diff(before: dict[str, Any], after: dict[str, Any]) -> pd.DataFrame:
    """Ð¡Ñ€Ð°Ð²Ð½ÑÐ²Ð° ÑÐµÑÐ¸Ñ ÑÑŠÑ ÑÑŠÐ¾Ñ‚Ð²ÐµÑ‚Ð½Ð°Ñ‚Ð° ÑÐµÑÐ¸Ñ, Ð±ÐµÐ· Ð´ÐµÐºÐ°Ñ€Ñ‚Ð¾Ð²Ð¾ ÑƒÐ¼Ð½Ð¾Ð¶ÐµÐ½Ð¸Ðµ Ð¿Ñ€Ð¸ Ð´Ð²Ð¾Ð¹Ð½Ð¸ Ð´Ð½Ð¸."""

    keys = ["date", "session_no"]
    left = before["plan"][[*keys, "time_of_day", "focus", "method", "total_real_min"]].rename(
        columns={
            "time_of_day": "Ð§Ð°ÑÑ‚ Ð½Ð° Ð´ÐµÐ½Ñ Â· Ð¿Ñ€ÐµÐ´Ð¸",
            "focus": "Ð¤Ð¾ÐºÑƒÑ Â· Ð¿Ñ€ÐµÐ´Ð¸",
            "method": "ÐœÐµÑ‚Ð¾Ð´ Â· Ð¿Ñ€ÐµÐ´Ð¸",
            "total_real_min": "ÐœÐ¸Ð½ÑƒÑ‚Ð¸ Â· Ð¿Ñ€ÐµÐ´Ð¸",
        }
    )
    right = after["plan"][[*keys, "time_of_day", "focus", "method", "total_real_min"]].rename(
        columns={
            "time_of_day": "Ð§Ð°ÑÑ‚ Ð½Ð° Ð´ÐµÐ½Ñ Â· ÑÐ»ÐµÐ´",
            "focus": "Ð¤Ð¾ÐºÑƒÑ Â· ÑÐ»ÐµÐ´",
            "method": "ÐœÐµÑ‚Ð¾Ð´ Â· ÑÐ»ÐµÐ´",
            "total_real_min": "ÐœÐ¸Ð½ÑƒÑ‚Ð¸ Â· ÑÐ»ÐµÐ´",
        }
    )
    merged = left.merge(right, on=keys, how="outer").sort_values(keys).reset_index(drop=True)
    merged["ÐœÐ¸Ð½ÑƒÑ‚Ð¸ Â· Ð¿Ñ€ÐµÐ´Ð¸"] = pd.to_numeric(merged["ÐœÐ¸Ð½ÑƒÑ‚Ð¸ Â· Ð¿Ñ€ÐµÐ´Ð¸"], errors="coerce").fillna(0.0)
    merged["ÐœÐ¸Ð½ÑƒÑ‚Ð¸ Â· ÑÐ»ÐµÐ´"] = pd.to_numeric(merged["ÐœÐ¸Ð½ÑƒÑ‚Ð¸ Â· ÑÐ»ÐµÐ´"], errors="coerce").fillna(0.0)
    merged["Î” Ð¼Ð¸Ð½"] = merged["ÐœÐ¸Ð½ÑƒÑ‚Ð¸ Â· ÑÐ»ÐµÐ´"] - merged["ÐœÐ¸Ð½ÑƒÑ‚Ð¸ Â· Ð¿Ñ€ÐµÐ´Ð¸"]
    return merged


def render_simulator_page(bundle: dict[str, Any], before: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(before["athlete"]["athlete_id"])
    page_header("Ð¡Ð¸Ð¼ÑƒÐ»Ð°Ñ‚Ð¾Ñ€ â€žÐšÐ°ÐºÐ²Ð¾ Ñ‰Ðµ ÑÑ‚Ð°Ð½Ðµ, Ð°ÐºÐ¾â€œ", "ÐŸÑ€Ð¾Ð¼ÑÐ½Ð°Ñ‚Ð° ÑÐµ Ð¸Ð·Ñ‡Ð¸ÑÐ»ÑÐ²Ð° Ð²ÑŠÑ€Ñ…Ñƒ ÐºÐ¾Ð¿Ð¸Ðµ. ÐžÐ´Ð¾Ð±Ñ€ÐµÐ½Ð¾Ñ‚Ð¾ ÑÑŠÑÑ‚Ð¾ÑÐ½Ð¸Ðµ Ð½Ðµ ÑÐµ Ð¿Ñ€Ð¾Ð¼ÐµÐ½Ñ, Ð´Ð¾ÐºÐ°Ñ‚Ð¾ Ð½Ðµ Ð½Ð°Ñ‚Ð¸ÑÐ½ÐµÑ‚Ðµ â€žÐŸÑ€Ð¸ÐµÐ¼Ð¸ ÑÑ†ÐµÐ½Ð°Ñ€Ð¸Ñâ€œ.")
    scenario_type = st.selectbox(
        "Ð¡Ñ†ÐµÐ½Ð°Ñ€Ð¸Ð¹",
        ["ÐÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚ Â· Z3 Ð²Ñ€ÐµÐ¼Ðµ Ð¸ Ð¿Ð¾Ð·Ð¸Ñ†Ð¸Ñ", "ÐœÐ¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³ Â· Ñ‚Ñ€Ð¸ Ð½ÐµÐ±Ð»Ð°Ð³Ð¾Ð¿Ñ€Ð¸ÑÑ‚Ð½Ð¸ Ð´Ð½Ð¸", "ÐšÐ¾Ð½Ñ‚Ñ€Ð¾Ð»ÐµÐ½ Ñ‚ÐµÑÑ‚", "ÐšÐ°Ð»ÐµÐ½Ð´Ð°Ñ€ Â· Ð¾ÑÐ½Ð¾Ð²ÐµÐ½ ÑÑ‚Ð°Ñ€Ñ‚"],
        key="scenario_type",
    )
    scenario_bundle = deepcopy(bundle)
    scenario_description = ""
    focus_component = "Z3"

    if scenario_type.startswith("ÐÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚"):
        eligible = before["activities"].loc[before["activities"]["real_Z3"] > 3].sort_values("date", ascending=False).head(25)
        labels = {row["activity_id"]: f"{pd.Timestamp(row['date']).date()} Â· Z3 {row['real_Z3']:.0f} Ð¼Ð¸Ð½" for _, row in eligible.iterrows()}
        activity_id = st.selectbox("ÐÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚", eligible["activity_id"].tolist(), format_func=lambda x: labels[x], key="scenario_activity")
        current = eligible.loc[eligible["activity_id"] == activity_id].iloc[0]
        c1, c2 = st.columns(2)
        new_z3 = c1.slider("Ð ÐµÐ°Ð»Ð½Ð¾ Ð²Ñ€ÐµÐ¼Ðµ Ð² Z3 Â· Ð¼Ð¸Ð½", 0.0, 80.0, float(current["real_Z3"]), 1.0, help=help_text("real_equivalent"))
        new_pos = c2.slider("Ð¡Ñ€ÐµÐ´Ð½Ð° Ð¿Ð¾Ð·Ð¸Ñ†Ð¸Ñ Ð² Z3 Â· %", 5, 95, int(round(float(current["pos_Z3"]) * 100)), 1, help=help_text("real_equivalent")) / 100.0
        mask = scenario_bundle["activities"]["activity_id"] == activity_id
        scenario_bundle["activities"].loc[mask, "real_Z3"] = new_z3
        scenario_bundle["activities"].loc[mask, "pos_Z3"] = new_pos
        scenario_description = f"ÐÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚ {activity_id}: Z3 â†’ {new_z3:.0f} Ð¼Ð¸Ð½ Ð¿Ñ€Ð¸ Ð¿Ð¾Ð·Ð¸Ñ†Ð¸Ñ {new_pos*100:.0f}% Ð¾Ñ‚ Ð·Ð¾Ð½Ð°Ñ‚Ð°."
        focus_component = "Z3"
    elif scenario_type.startswith("ÐœÐ¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³"):
        c1, c2, c3, c4 = st.columns(4)
        sleep = c1.slider("Ð¡ÑŠÐ½ /10", 1.0, 10.0, 4.0, 0.5)
        fatigue = c2.slider("Ð£Ð¼Ð¾Ñ€Ð° /10", 0.0, 10.0, 8.0, 0.5)
        pain = c3.slider("Ð‘Ð¾Ð»ÐºÐ° /10", 0.0, 10.0, 2.0, 0.5)
        hr_delta = c4.slider("Ð¡ÑƒÑ‚Ñ€ÐµÑˆÐµÐ½ Ð¿ÑƒÐ»Ñ Â· Î”", 0, 15, 6, 1)
        athlete_rows = scenario_bundle["wellness"].loc[scenario_bundle["wellness"]["athlete_id"] == athlete_id]
        baseline_hr = float(athlete_rows.sort_values("date").tail(40)["morning_hr"].mean())
        for offset in range(3):
            target_date = date.today() - timedelta(days=offset)
            mask = (scenario_bundle["wellness"]["athlete_id"] == athlete_id) & (pd.to_datetime(scenario_bundle["wellness"]["date"]).dt.date == target_date)
            scenario_bundle["wellness"].loc[mask, "sleep_quality"] = sleep
            scenario_bundle["wellness"].loc[mask, "fatigue"] = fatigue
            scenario_bundle["wellness"].loc[mask, "pain"] = pain
            scenario_bundle["wellness"].loc[mask, "morning_hr"] = baseline_hr + hr_delta
        scenario_description = f"ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð¸Ñ‚Ðµ 3 Ð´Ð½Ð¸: ÑÑŠÐ½ {sleep:.1f}, ÑƒÐ¼Ð¾Ñ€Ð° {fatigue:.1f}, Ð±Ð¾Ð»ÐºÐ° {pain:.1f}, ÑÑƒÑ‚Ñ€ÐµÑˆÐµÐ½ Ð¿ÑƒÐ»Ñ +{hr_delta}."
        focus_component = "Z4"
    elif scenario_type.startswith("ÐšÐ¾Ð½Ñ‚Ñ€Ð¾Ð»ÐµÐ½"):
        test_code = st.selectbox("Ð¢ÐµÑÑ‚", list(TEST_DEFINITIONS), format_func=lambda c: TEST_DEFINITIONS[c]["label"], key="scenario_test")
        subset = scenario_bundle["tests"].loc[(scenario_bundle["tests"]["athlete_id"] == athlete_id) & (scenario_bundle["tests"]["test_code"] == test_code)].sort_values("date")
        latest = subset.iloc[-1]
        c1, c2 = st.columns(2)
        change = c1.slider("ÐŸÑ€Ð¾Ð¼ÑÐ½Ð° Ð½Ð° Ð¾ÑÐ½Ð¾Ð²Ð½Ð¸Ñ Ñ€ÐµÐ·ÑƒÐ»Ñ‚Ð°Ñ‚ Â· %", -12.0, 12.0, 4.0, 0.5)
        comparability = c2.slider("Ð¡Ñ€Ð°Ð²Ð½Ð¸Ð¼Ð¾ÑÑ‚", 0.4, 1.0, 0.95, 0.05)
        direction = TEST_DEFINITIONS[test_code]["primary_direction"]
        new_value = float(latest["primary_value"]) * (1.0 + change / 100.0 * direction)
        mask = scenario_bundle["tests"]["test_id"] == latest["test_id"]
        scenario_bundle["tests"].loc[mask, "primary_value"] = new_value
        scenario_bundle["tests"].loc[mask, "comparability"] = comparability
        scenario_description = f"ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð¸ÑÑ‚ Ñ‚ÐµÑÑ‚ Ðµ Ð¿Ñ€Ð¾Ð¼ÐµÐ½ÐµÐ½ Ñ Ð¿Ð¾ÑÐ¾Ñ‡Ð½Ð¾ Ð¿Ð¾Ð´Ð¾Ð±Ñ€ÐµÐ½Ð¸Ðµ {change:+.1f}% Ð¸ ÑÑ€Ð°Ð²Ð½Ð¸Ð¼Ð¾ÑÑ‚ {comparability:.2f}."
        focus_component = "Z3" if test_code == "Z3_20MIN" else "Z5"
    else:
        main_mask = (
            (scenario_bundle["calendar"]["athlete_id"].astype(str) == athlete_id)
            & (scenario_bundle["calendar"]["type"] == "MAIN_RACE")
        )
        future_main = scenario_bundle["calendar"].loc[main_mask].copy()
        if not future_main.empty:
            future_main["start_date"] = pd.to_datetime(future_main["start_date"]).dt.normalize()
            future_main = future_main.loc[future_main["start_date"] >= pd.Timestamp.today().normalize()]
        current_date = (
            pd.Timestamp(future_main.sort_values("start_date").iloc[0]["start_date"]).date()
            if not future_main.empty
            else date.today() + timedelta(weeks=16)
        )
        initial_weeks = int(np.clip(round((current_date - date.today()).days / 7), 6, 24))
        weeks = st.slider("Ð¡ÐµÐ´Ð¼Ð¸Ñ†Ð¸ Ð´Ð¾ Ð¾ÑÐ½Ð¾Ð²Ð½Ð¸Ñ ÑÑ‚Ð°Ñ€Ñ‚", 6, 24, initial_weeks, 1)
        new_date = date.today() + timedelta(weeks=weeks)
        if main_mask.any():
            first_index = scenario_bundle["calendar"].loc[main_mask].index[0]
            scenario_bundle["calendar"].loc[first_index, "start_date"] = pd.Timestamp(new_date)
            scenario_bundle["calendar"].loc[first_index, "end_date"] = pd.Timestamp(new_date)
        else:
            new_row = {
                "event_id": f"{athlete_id}-SIM-MAIN",
                "athlete_id": athlete_id,
                "type": "MAIN_RACE",
                "name": "Ð¡Ð¸Ð¼ÑƒÐ»Ð°Ñ†Ð¸Ð¾Ð½ÐµÐ½ Ð¾ÑÐ½Ð¾Ð²ÐµÐ½ ÑÑ‚Ð°Ñ€Ñ‚",
                "start_date": pd.Timestamp(new_date),
                "end_date": pd.Timestamp(new_date),
                "priority": "A",
                "goal": "ÐŸÐ¸ÐºÐ¾Ð²Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚",
                "locked": False,
                "note": "Ð”Ð¾Ð±Ð°Ð²ÐµÐ½ Ð¾Ñ‚ ÑÐ¸Ð¼ÑƒÐ»Ð°Ñ‚Ð¾Ñ€Ð°.",
            }
            scenario_bundle["calendar"] = pd.concat(
                [scenario_bundle["calendar"], pd.DataFrame([new_row])], ignore_index=True
            )
        scenario_description = f"ÐžÑÐ½Ð¾Ð²Ð½Ð¸ÑÑ‚ ÑÑ‚Ð°Ñ€Ñ‚ Ðµ Ð¿Ñ€ÐµÐ¼ÐµÑÑ‚ÐµÐ½/Ð·Ð°Ð´Ð°Ð´ÐµÐ½ Ð½Ð° {new_date.strftime('%d.%m.%Y')} ({weeks} ÑÐµÐ´Ð¼Ð¸Ñ†Ð¸)."
        focus_component = "Z4"

    scenario_bundle["version"] = int(bundle["version"]) + 1
    after = analyze_athlete(scenario_bundle, athlete_id, generate_plan=True)
    st.markdown(f'<div class="soft-box"><b>ÐÐºÑ‚Ð¸Ð²ÐµÐ½ ÑÑ†ÐµÐ½Ð°Ñ€Ð¸Ð¹:</b> {scenario_description}</div>', unsafe_allow_html=True)
    comparison_metrics(before, after, focus_component)
    st.subheader("ÐŸÑ€Ð¾Ð¼ÑÐ½Ð° Ð½Ð° Ð¿Ñ€Ð¾Ð³Ñ€Ð°Ð¼Ð°Ñ‚Ð°")
    st.dataframe(plan_diff(before, after), width="stretch", hide_index=True)

    with st.expander("Ð¢ÐµÑ…Ð½Ð¸Ñ‡ÐµÑÐºÐ¾ Ð¾Ð±ÑÑÐ½ÐµÐ½Ð¸Ðµ Ð½Ð° ÑÑ†ÐµÐ½Ð°Ñ€Ð¸Ñ"):
        st.write(after["integrated"].loc[focus_component, "reason"])
        st.json(after["decision_snapshot"], expanded=False)

    if st.button("ÐŸÑ€Ð¸ÐµÐ¼Ð¸ ÑÑ†ÐµÐ½Ð°Ñ€Ð¸Ñ ÐºÐ°Ñ‚Ð¾ Ð½Ð¾Ð²Ð° Ð²ÐµÑ€ÑÐ¸Ñ", disabled=not can_edit, type="primary", width="stretch"):
        scenario_bundle["audit_log"] = bundle.get("audit_log", [])
        commit_bundle(scenario_bundle, "scenario_accept", "Ð¡Ñ†ÐµÐ½Ð°Ñ€Ð¸ÑÑ‚ Ðµ Ð¿Ñ€Ð¸ÐµÑ‚: " + scenario_description, athlete_id)


def render_profile_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(analysis["athlete"]["athlete_id"])
    athlete_index = bundle["athletes"].index[bundle["athletes"]["athlete_id"] == athlete_id][0]
    athlete = bundle["athletes"].loc[athlete_index]
    page_header("ÐŸÑ€Ð¾Ñ„Ð¸Ð» Ð½Ð° ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚Ð°", "Ð¡Ñ‚Ð°Ñ‚Ð¸Ñ‡Ð½Ð¸ Ð´Ð°Ð½Ð½Ð¸, Ð¸Ð½Ð´Ð¸Ð²Ð¸Ð´ÑƒÐ°Ð»Ð½Ð¸ Ð·Ð¾Ð½Ð¸, ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚Ð½Ð° Ð±Ð°Ð·Ð°, Ð¿Ð¾Ð½Ð¾ÑÐ¸Ð¼Ð¾ÑÑ‚ Ð¸ ÐºÐ°Ñ‡ÐµÑÑ‚Ð²Ð¾ Ð½Ð° Ð´Ð°Ð½Ð½Ð¸Ñ‚Ðµ.")

    tab_profile, tab_zones, tab_tolerance = st.tabs(["ÐžÑÐ½Ð¾Ð²Ð½Ð¸ Ð´Ð°Ð½Ð½Ð¸", "Ð—Ð¾Ð½Ð¸ Ð¸ Ñ‚ÐµÐ³Ð»Ð°", "ÐŸÑ€Ð¾Ñ„Ð¸Ð» Ð½Ð° Ð¿Ð¾Ð½Ð¾ÑÐ¸Ð¼Ð¾ÑÑ‚"])
    with tab_profile:
        with st.form("athlete_profile_form"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Ð˜Ð¼Ðµ", value=str(athlete["name"]), disabled=not can_edit)
            category = c1.text_input("ÐšÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ", value=str(athlete["category"]), disabled=not can_edit)
            age = c2.number_input("Ð’ÑŠÐ·Ñ€Ð°ÑÑ‚", 14, 60, int(athlete["age"]), disabled=not can_edit)
            height = c2.number_input("Ð ÑŠÑÑ‚ Â· cm", 140, 220, int(athlete["height_cm"]), disabled=not can_edit)
            weight = c3.number_input("ÐœÐ°ÑÐ° Â· kg", 40.0, 130.0, float(athlete["weight_kg"]), 0.5, disabled=not can_edit)
            experience = c3.number_input("Ð¡Ð¿Ð¾Ñ€Ñ‚ÐµÐ½ ÑÑ‚Ð°Ð¶ Â· Ð³Ð¾Ð´Ð¸Ð½Ð¸", 0, 40, int(athlete["experience_years"]), disabled=not can_edit)
            availability = st.text_input("ÐÐ°Ð»Ð¸Ñ‡Ð½Ð¾ÑÑ‚", value=str(athlete["availability"]), disabled=not can_edit)
            submitted = st.form_submit_button("Ð—Ð°Ð¿Ð°Ð·Ð¸ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»Ð°", disabled=not can_edit, width="stretch")
        if submitted:
            updates = {
                "name": name,
                "category": category,
                "age": age,
                "height_cm": height,
                "weight_kg": weight,
                "experience_years": experience,
                "availability": availability,
            }
            for key, value in updates.items():
                bundle["athletes"].at[athlete_index, key] = value
            commit_bundle(bundle, "athlete_profile_update", "ÐžÑÐ½Ð¾Ð²Ð½Ð¸ÑÑ‚ Ð¿Ñ€Ð¾Ñ„Ð¸Ð» Ð½Ð° ÑÐ¿Ð¾Ñ€Ñ‚Ð¸ÑÑ‚Ð° Ðµ Ð°ÐºÑ‚ÑƒÐ°Ð»Ð¸Ð·Ð¸Ñ€Ð°Ð½.", athlete_id)

        quality = {
            "Ð˜ÑÑ‚Ð¾Ñ€Ð¸Ñ Ð½Ð° Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ð¸": len(analysis["activities"]),
            "Ð”Ð½Ð¸ Ð¼Ð¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³": int((bundle["wellness"]["athlete_id"] == athlete_id).sum()),
            "ÐšÐ¾Ð½Ñ‚Ñ€Ð¾Ð»Ð½Ð¸ Ñ‚ÐµÑÑ‚Ð¾Ð²Ðµ": int((bundle["tests"]["athlete_id"] == athlete_id).sum()),
            "ÐÐ°Ð´ÐµÐ¶Ð´Ð½Ð¾ÑÑ‚ Ð½Ð° 40-Ð´Ð½ÐµÐ²Ð½Ð°Ñ‚Ð° Ð±Ð°Ð·Ð°": float(analysis["load_stats"]["reliability"].mean() * 100),
        }
        st.dataframe(pd.DataFrame([quality]), width="stretch", hide_index=True)

    with tab_zones:
        zones = bundle["zone_profiles"][athlete_id].copy()
        editable_cols = ["component", "hr_low", "hr_high", "weight_low", "weight_high", "power"]
        edited = st.data_editor(
            zones[editable_cols],
            width="stretch",
            hide_index=True,
            disabled=["component"] if can_edit else editable_cols,
            key=f"zone_editor_{athlete_id}_{bundle['version']}",
            column_config={
                "component": "Ð—Ð¾Ð½Ð°",
                "hr_low": st.column_config.NumberColumn("ÐŸÑƒÐ»Ñ Â· Ð´Ð¾Ð»Ð½Ð°", min_value=50, max_value=230),
                "hr_high": st.column_config.NumberColumn("ÐŸÑƒÐ»Ñ Â· Ð³Ð¾Ñ€Ð½Ð°", min_value=50, max_value=230),
                "weight_low": st.column_config.NumberColumn("Ð¢ÐµÐ³Ð»Ð¾ Â· Ð´Ð¾Ð»Ð½Ð¾", min_value=1.0, help=help_text("real_equivalent")),
                "weight_high": st.column_config.NumberColumn("Ð¢ÐµÐ³Ð»Ð¾ Â· Ð³Ð¾Ñ€Ð½Ð¾", min_value=1.0, help=help_text("real_equivalent")),
                "power": st.column_config.NumberColumn("Ð¡Ñ‚ÐµÐ¿ÐµÐ½ p", min_value=0.2, max_value=4.0, step=0.05),
            },
        )
        if can_edit and st.button("Ð—Ð°Ð¿Ð°Ð·Ð¸ Ð·Ð¾Ð½Ð¸Ñ‚Ðµ Ð¸ Ð¿Ñ€ÐµÐ¸Ð·Ñ‡Ð¸ÑÐ»Ð¸", width="stretch"):
            valid = True
            if (edited["hr_low"] >= edited["hr_high"]).any() or (edited["weight_low"] <= 0).any() or (edited["weight_high"] < edited["weight_low"]).any():
                valid = False
                st.error("ÐŸÑ€Ð¾Ð²ÐµÑ€ÐµÑ‚Ðµ Ð³Ñ€Ð°Ð½Ð¸Ñ†Ð¸Ñ‚Ðµ Ð¸ Ñ‚ÐµÐ³Ð»Ð°Ñ‚Ð°: Ð´Ð¾Ð»Ð½Ð°Ñ‚Ð° Ð³Ñ€Ð°Ð½Ð¸Ñ†Ð° Ñ‚Ñ€ÑÐ±Ð²Ð° Ð´Ð° Ðµ Ð¿Ð¾-Ð¼Ð°Ð»ÐºÐ°, Ð° Ñ‚ÐµÐ³Ð»Ð°Ñ‚Ð° â€” Ð¿Ð¾Ð»Ð¾Ð¶Ð¸Ñ‚ÐµÐ»Ð½Ð¸ Ð¸ Ð½ÐµÐ½Ð°Ð¼Ð°Ð»ÑÐ²Ð°Ñ‰Ð¸.")
            if valid:
                for col in editable_cols:
                    zones[col] = edited[col].values
                zones["version"] = int(zones["version"].max()) + 1
                bundle["zone_profiles"][athlete_id] = zones
                commit_bundle(bundle, "zone_profile_update", "Ð˜Ð½Ð´Ð¸Ð²Ð¸Ð´ÑƒÐ°Ð»Ð½Ð¸Ñ‚Ðµ Ð·Ð¾Ð½Ð¸ Ð¸ Ð²ÑŠÑ‚Ñ€ÐµÑˆÐ½Ð¾Ð·Ð¾Ð½Ð¾Ð²Ð¸ Ñ‚ÐµÐ³Ð»Ð° ÑÐ° Ð¿Ñ€Ð¾Ð¼ÐµÐ½ÐµÐ½Ð¸.", athlete_id)
        for i in range(len(edited) - 1):
            if abs(float(edited.iloc[i]["weight_high"]) - float(edited.iloc[i + 1]["weight_low"])) > 1e-6:
                st.warning("Ð˜Ð¼Ð° Ð¿Ñ€ÐµÐºÑŠÑÐ²Ð°Ð½Ðµ Ð¼ÐµÐ¶Ð´Ñƒ Ð³Ð¾Ñ€Ð½Ð¾Ñ‚Ð¾ Ñ‚ÐµÐ³Ð»Ð¾ Ð½Ð° ÐµÐ´Ð½Ð° Ð·Ð¾Ð½Ð° Ð¸ Ð´Ð¾Ð»Ð½Ð¾Ñ‚Ð¾ Ñ‚ÐµÐ³Ð»Ð¾ Ð½Ð° ÑÐ»ÐµÐ´Ð²Ð°Ñ‰Ð°Ñ‚Ð°. Ð¢Ð¾Ð²Ð° Ðµ Ð´Ð¾Ð¿ÑƒÑÑ‚Ð¸Ð¼Ð¾ Ð·Ð° Ñ‚ÐµÑÑ‚, Ð½Ð¾ Ð¼Ð¾Ð¶Ðµ Ð´Ð° ÑÑŠÐ·Ð´Ð°Ð´Ðµ Ð¸Ð·ÐºÑƒÑÑ‚Ð²ÐµÐ½ ÑÐºÐ¾Ðº.")
                break

        st.subheader("Ð¡Ð¸Ð»Ð¾Ð²Ð¸ ÐºÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚Ð¸")
        st.caption(
            "ÐŸÑ€Ð¸ ÑÐ¸Ð»Ð°Ñ‚Ð° Ð½ÑÐ¼Ð° Ð¿ÑƒÐ»ÑÐ¾Ð²Ð¸ Ð³Ñ€Ð°Ð½Ð¸Ñ†Ð¸. Ð ÐµÐ°Ð»Ð½Ð¸Ñ‚Ðµ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ ÑÐµ Ð²ÑŠÐ²ÐµÐ¶Ð´Ð°Ñ‚ Ð¿Ð¾ Ð²Ð¸Ð´ Ð¸ ÑÐµ ÑƒÐ¼Ð½Ð¾Ð¶Ð°Ð²Ð°Ñ‚ Ð¿Ð¾ Ñ„Ð¸ÐºÑÐ¸Ñ€Ð°Ð½Ð¸Ñ ÐºÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚."
        )
        strength_coefficients = pd.DataFrame(
            [
                {
                    "ÐšÐ¾Ð´": strength_type,
                    "Ð’Ð¸Ð´ ÑÐ¸Ð»Ð°": STRENGTH_LABELS[strength_type],
                    "ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚": STRENGTH_COEFFICIENTS[strength_type],
                    "10 Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ð´Ð°Ð²Ð°Ñ‚": 10.0 * STRENGTH_COEFFICIENTS[strength_type],
                }
                for strength_type in STRENGTH_TYPES
            ]
        )
        st.dataframe(
            strength_coefficients,
            width="stretch",
            hide_index=True,
            column_config={
                "ÐšÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚": st.column_config.NumberColumn(format="%.1f", help=help_text("strength_load")),
                "10 Ñ€ÐµÐ°Ð»Ð½Ð¸ Ð¼Ð¸Ð½ÑƒÑ‚Ð¸ Ð´Ð°Ð²Ð°Ñ‚": st.column_config.NumberColumn("Ð•ÐºÐ². Ð¼Ð¸Ð½ Ð¿Ñ€Ð¸ 10 Ñ€ÐµÐ°Ð»Ð½Ð¸", format="%.1f"),
            },
        )

    with tab_tolerance:
        rows = []
        for component in COMPONENTS:
            rec = bundle["parameters"]["recovery"][component]
            rows.append(
                {
                    "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚": component,
                    "E40 / Ð´ÐµÐ½": analysis["load_stats"].loc[component, "E40_daily"],
                    "Tref": analysis["load_stats"].loc[component, "Tref"],
                    "Ð§ÑƒÐ²ÑÑ‚Ð²Ð¸Ñ‚ÐµÐ»Ð½Ð¾ÑÑ‚ s": rec["sensitivity"],
                    "Ï„ Â· Ð´Ð½Ð¸": rec["tau_days"],
                    "Ð¢Ð¾Ð²Ð°Ñ€Ð½Ð° readiness": analysis["load_readiness"].loc[component, "readiness"],
                    "ÐœÐ¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³": analysis["monitoring_by_component"].loc[component, "monitoring_score"],
                    "Ð¢ÐµÑÑ‚Ð¾Ð²Ð° ÐºÐ¾Ñ€ÐµÐºÑ†Ð¸Ñ %": analysis["test_adjustments"].get(component, 0.0) * 100,
                    "Ð˜Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚": analysis["integrated"].loc[component, "integrated_readiness"],
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_models_page() -> None:
    page_header("ÐœÐ¾Ð´ÐµÐ»Ð¸ Ð¸ Ð¾Ð±ÑÑÐ½ÐµÐ½Ð¸Ñ", "Ð’ÑÑÐºÐ° Ð¾ÑÐ½Ð¾Ð²Ð½Ð° Ð¼ÐµÑ‚Ñ€Ð¸ÐºÐ° Ð² Ð¿Ñ€Ð¸Ð»Ð¾Ð¶ÐµÐ½Ð¸ÐµÑ‚Ð¾ Ð¸Ð¼Ð° â“ tooltip Ñ ÐºÑ€Ð°Ñ‚ÐºÐ¾ Ð¾Ð±ÑÑÐ½ÐµÐ½Ð¸Ðµ Ð¸ Ð»Ð¸Ð½Ðº ÐºÑŠÐ¼ Ñ‚Ð°Ð·Ð¸ ÑÑ‚Ñ€Ð°Ð½Ð¸Ñ†Ð°.")
    titles = explanation_titles()
    requested = str(st.query_params.get("topic", "seven_forty"))
    if requested not in EXPLANATIONS:
        requested = "seven_forty"
    topic = st.selectbox("ÐœÐ¾Ð´ÐµÐ» / Ð¸Ð½Ð´ÐµÐºÑ", list(EXPLANATIONS), index=list(EXPLANATIONS).index(requested), format_func=lambda key: titles[key], key="model_topic")
    if str(st.query_params.get("topic", "")) != topic:
        st.query_params["topic"] = topic

    item = EXPLANATIONS[topic]
    st.header(item["title"])
    st.markdown(item["body"])

    st.divider()
    st.subheader("ÐŸÐ¾ÑÐ»ÐµÐ´Ð¾Ð²Ð°Ñ‚ÐµÐ»Ð½Ð¾ÑÑ‚ Ð½Ð° Ð¸Ð·Ñ‡Ð¸ÑÐ»Ð¸Ñ‚ÐµÐ»Ð½Ð¸Ñ pipeline")
    st.markdown(
        "1. ÐÐ¾Ñ€Ð¼Ð°Ð»Ð¸Ð·Ð¸Ñ€Ð°Ð½Ðµ Ð½Ð° Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ñ‚Ð° â†’ 2. Ð·Ð¾Ð½Ð¸Ñ€Ð°Ð½Ðµ Ð½Ð° Ð²ÑÑÐºÐ° ÑÐµÐºÑƒÐ½Ð´Ð° Ð¸ ÐºÐ»Ð°ÑÐ¸Ñ„Ð¸Ñ†Ð¸Ñ€Ð°Ð½Ðµ Ð½Ð° ÑÐ¸Ð»Ð¾Ð²Ð¸Ñ Ð²Ð¸Ð´ â†’ "
        "3. Ñ€ÐµÐ°Ð»Ð½Ð¾ Ð²Ñ€ÐµÐ¼Ðµ, ÐºÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚Ð¸ Ð¸ `Q` â†’ 4. ÐºÐ°ÑÐºÐ°Ð´Ð° Ð¸ Ñ€Ð°Ð·Ð»Ð¸Ð² Ð´Ð¾ `E` â†’ "
        "5. `E7`, `E40`, `B`, `7/40`, `Tref` â†’ 6. ÑƒÐ¼Ð¾Ñ€Ð° Ð¸ readiness â†’ "
        "7. Ð¼Ð¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð¸Ð½Ð³ â†’ 8. ÐºÐ¾Ð½Ñ‚Ñ€Ð¾Ð»Ð½Ð¸ Ñ‚ÐµÑÑ‚Ð¾Ð²Ðµ â†’ 9. Ð¸Ð½Ñ‚ÐµÐ³Ñ€Ð¸Ñ€Ð°Ð½Ð° Ð³Ð¾Ñ‚Ð¾Ð²Ð½Ð¾ÑÑ‚ â†’ 10. Ð¿ÐµÑ€Ð¸Ð¾Ð´Ð¸Ð·Ð°Ñ†Ð¸Ð¾Ð½Ð½Ð° Ñ†ÐµÐ» â†’ "
        "11. Ð°Ð´Ð°Ð¿Ñ‚Ð¸Ð²ÐµÐ½ Ð¼Ð½Ð¾Ð¶Ð¸Ñ‚ÐµÐ» â†’ 12. Ð¾Ð±Ñ€Ð°Ñ‚Ð½Ð¾ Ñ€ÐµÑˆÐµÐ½Ð¸Ðµ `E â†’ Q` â†’ 13. Ñ€Ð°Ð·Ð¿Ñ€ÐµÐ´ÐµÐ»ÐµÐ½Ð¸Ðµ Ð¿Ð¾ Ð´Ð½Ð¸ â†’ "
        "14. Ð¸Ð·Ð±Ð¾Ñ€ Ð¸ Ð´Ð¾Ð·Ð¸Ñ€Ð°Ð½Ðµ Ð½Ð° Ð¼ÐµÑ‚Ð¾Ð´ â†’ 15. Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ° Ð½Ð° Ð¾Ð³Ñ€Ð°Ð½Ð¸Ñ‡ÐµÐ½Ð¸ÑÑ‚Ð° â†’ 16. DecisionSnapshot."
    )
    st.info("ÐÐ°Ñ‡Ð°Ð»Ð½Ð¸Ñ‚Ðµ ÐºÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚Ð¸ ÑÐ° ÐµÐºÑÐ¿ÐµÑ€Ñ‚Ð½Ð¸ Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€Ð¸ Ð·Ð° MVP Ð¸ Ð²Ð°Ð»Ð¸Ð´Ð¸Ñ€Ð°Ð½Ðµ. Ð¢Ðµ Ð½Ðµ ÑÐ° ÑƒÐ½Ð¸Ð²ÐµÑ€ÑÐ°Ð»Ð½Ð¸ Ð½Ð¾Ñ€Ð¼Ð¸ Ð¸ ÑÐ° Ð²Ð¸Ð´Ð¸Ð¼Ð¸ Ð² ÐµÐºÑÐ¿ÐµÑ€Ñ‚Ð½Ð¸Ñ‚Ðµ Ð½Ð°ÑÑ‚Ñ€Ð¾Ð¹ÐºÐ¸.")

    with st.expander("Ð’ÑÐ¸Ñ‡ÐºÐ¸ Ð¿Ð¾Ð½ÑÑ‚Ð¸Ñ"):
        for key, value in EXPLANATIONS.items():
            st.markdown(f"### {value['title']}")
            st.write(value["short"])


def render_settings_page(bundle: dict[str, Any], role: str, athlete_id: str) -> None:
    page_header("Ð•ÐºÑÐ¿ÐµÑ€Ñ‚Ð½Ð¸ Ð½Ð°ÑÑ‚Ñ€Ð¾Ð¹ÐºÐ¸", "ÐÐ°Ñ‡Ð°Ð»Ð½Ð¸ Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€Ð¸, Ð²ÑŠÐ·ÑÑ‚Ð°Ð½Ð¾Ð²ÑÐ²Ð°Ð½Ðµ, ÐºÐ°ÑÐºÐ°Ð´Ð°, Ð±Ð°Ð·Ð° Ð¾Ñ‚ Ð¼ÐµÑ‚Ð¾Ð´Ð¸ Ð¸ Ð¶ÑƒÑ€Ð½Ð°Ð» Ð½Ð° Ð²ÐµÑ€ÑÐ¸Ð¸Ñ‚Ðµ.")
    can_edit = role in EXPERT_ROLES
    if not can_edit:
        st.info("Ð ÐµÐ´Ð°ÐºÑ†Ð¸ÑÑ‚Ð° Ð½Ð° ÐµÐºÑÐ¿ÐµÑ€Ñ‚Ð½Ð¸Ñ‚Ðµ ÐºÐ¾ÐµÑ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚Ð¸ Ðµ Ð´Ð¾ÑÑ‚ÑŠÐ¿Ð½Ð° ÑÐ°Ð¼Ð¾ Ð·Ð° Ñ€Ð¾Ð»ÑÑ‚Ð° â€žÐ“Ð»Ð°Ð²ÐµÐ½ Ñ‚Ñ€ÐµÐ½ÑŒÐ¾Ñ€â€œ. Ð”Ð°Ð½Ð½Ð¸Ñ‚Ðµ Ð¿Ð¾-Ð´Ð¾Ð»Ñƒ ÑÐ° Ð² Ñ€ÐµÐ¶Ð¸Ð¼ Ð¿Ñ€ÐµÐ³Ð»ÐµÐ´.")
    params = bundle["parameters"]
    tab_general, tab_components, tab_cascade, tab_methods, tab_audit = st.tabs(
        ["ÐžÐ±Ñ‰Ð¸ Ð¿Ñ€Ð°Ð²Ð¸Ð»Ð°", "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚Ð½Ð¸ Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€Ð¸", "ÐšÐ°ÑÐºÐ°Ð´Ð°", "ÐœÐµÑ‚Ð¾Ð´Ð¸", "Ð–ÑƒÑ€Ð½Ð°Ð»"]
    )

    with tab_general:
        with st.form("general_settings"):
            c1, c2, c3 = st.columns(3)
            spill_threshold = c1.slider("ÐŸÑ€Ð°Ð³ Ð·Ð° Ñ€Ð°Ð·Ð»Ð¸Ð² Â· Ð´ÑÐ» Ð¾Ñ‚ Tref", 0.20, 0.90, float(params["spill_threshold_fraction"]), 0.05, disabled=not can_edit)
            spill_fraction = c1.slider("Ð Ð°Ð·Ð»Ð¸Ð² ÐºÑŠÐ¼ Ð¿Ð¾-Ð²Ð¸ÑÐ¾ÐºÐ° Ð·Ð¾Ð½Ð°", 0.0, 0.50, float(params["spill_fraction"]), 0.05, disabled=not can_edit)
            key_fraction = c2.slider("ÐŸÑ€Ð°Ð³ Ð·Ð° ÐºÐ»ÑŽÑ‡Ð¾Ð² ÑÑ‚Ð¸Ð¼ÑƒÐ»", 0.20, 0.80, float(params["key_stimulus_fraction"]), 0.05, disabled=not can_edit)
            key_readiness = c2.slider("Readiness Ð·Ð° ÐºÐ»ÑŽÑ‡Ð¾Ð²Ð° ÑÐµÑÐ¸Ñ", 70.0, 100.0, float(params["key_readiness_threshold"]), 1.0, disabled=not can_edit)
            full_recovery = c3.slider("ÐŸÑ€Ð°ÐºÑ‚Ð¸Ñ‡ÐµÑÐºÐ¾ Ð¿ÑŠÐ»Ð½Ð¾ Ð²ÑŠÐ·ÑÑ‚Ð°Ð½Ð¾Ð²ÑÐ²Ð°Ð½Ðµ", 90.0, 99.0, float(params["practical_full_recovery"]), 1.0, disabled=not can_edit)
            current_weight = c3.slider("Ð¢ÐµÐ¶ÐµÑÑ‚ Ð½Ð° Ñ‚ÐµÐºÑƒÑ‰Ð¸Ñ Ð¿Ð¾ÐºÐ°Ð·Ð°Ñ‚ÐµÐ»", 0.30, 0.90, float(params["current_metric_weight"]), 0.05, disabled=not can_edit)
            submit = st.form_submit_button("Ð—Ð°Ð¿Ð°Ð·Ð¸ Ð¾Ð±Ñ‰Ð¸Ñ‚Ðµ Ð¿Ñ€Ð°Ð²Ð¸Ð»Ð°", disabled=not can_edit, width="stretch")
        if submit:
            params["spill_threshold_fraction"] = spill_threshold
            params["spill_fraction"] = spill_fraction
            params["key_stimulus_fraction"] = key_fraction
            params["key_readiness_threshold"] = key_readiness
            params["practical_full_recovery"] = full_recovery
            params["current_metric_weight"] = current_weight
            commit_bundle(bundle, "parameter_update", "ÐžÐ±Ñ‰Ð¸Ñ‚Ðµ Ð°Ð»Ð³Ð¾Ñ€Ð¸Ñ‚Ð¼Ð¸Ñ‡Ð½Ð¸ Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€Ð¸ ÑÐ° Ð°ÐºÑ‚ÑƒÐ°Ð»Ð¸Ð·Ð¸Ñ€Ð°Ð½Ð¸.", athlete_id)

    with tab_components:
        rows = []
        for component in COMPONENTS:
            rec = params["recovery"][component]
            rows.append(
                {
                    "component": component,
                    "base_load": params["base_loads"][component],
                    "sensitivity": rec["sensitivity"],
                    "tau_days": rec["tau_days"],
                    "fmax": rec["fmax"],
                }
            )
        edited = st.data_editor(
            pd.DataFrame(rows),
            width="stretch",
            hide_index=True,
            disabled=["component"] if can_edit else list(pd.DataFrame(rows).columns),
            key=f"component_params_{bundle['version']}",
            column_config={
                "component": "ÐšÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚",
                "base_load": st.column_config.NumberColumn("Ð‘Ð°Ð·Ð¾Ð² Ñ‚Ð¾Ð²Ð°Ñ€ B0", min_value=0.1, help=help_text("seven_forty")),
                "sensitivity": st.column_config.NumberColumn("Ð§ÑƒÐ²ÑÑ‚Ð²Ð¸Ñ‚ÐµÐ»Ð½Ð¾ÑÑ‚ s", min_value=0.1, max_value=3.0, step=0.05),
                "tau_days": st.column_config.NumberColumn("Ï„ Â· Ð´Ð½Ð¸", min_value=0.2, max_value=5.0, step=0.05),
                "fmax": st.column_config.NumberColumn("F max", min_value=100.0, max_value=300.0, step=5.0),
            },
        )
        if st.button("Ð—Ð°Ð¿Ð°Ð·Ð¸ ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚Ð½Ð¸Ñ‚Ðµ Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€Ð¸", disabled=not can_edit, width="stretch"):
            for _, row in edited.iterrows():
                component = str(row["component"])
                params["base_loads"][component] = float(row["base_load"])
                params["recovery"][component] = {
                    "sensitivity": float(row["sensitivity"]),
                    "tau_days": float(row["tau_days"]),
                    "fmax": float(row["fmax"]),
                }
            commit_bundle(bundle, "component_parameter_update", "Ð‘Ð°Ð·Ð¾Ð²Ð¸Ñ‚Ðµ Ñ‚Ð¾Ð²Ð°Ñ€Ð¸ Ð¸ Ð²ÑŠÐ·ÑÑ‚Ð°Ð½Ð¾Ð²Ð¸Ñ‚ÐµÐ»Ð½Ð¸Ñ‚Ðµ Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€Ð¸ ÑÐ° Ð°ÐºÑ‚ÑƒÐ°Ð»Ð¸Ð·Ð¸Ñ€Ð°Ð½Ð¸.", athlete_id)

    with tab_cascade:
        cascade_df = pd.DataFrame(params["cascade"]).T.reindex(index=COMPONENTS, columns=COMPONENTS)
        cascade_df.index.name = "Ð¿Ñ€Ð¸ÐµÐ¼Ð°Ñ‰ ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚"
        edited = st.data_editor(
            cascade_df,
            width="stretch",
            key=f"cascade_{bundle['version']}",
            disabled=not can_edit,
            column_config={c: st.column_config.NumberColumn(c, min_value=0.0, max_value=1.5, step=0.05) for c in COMPONENTS},
        )
        st.caption("Ð ÐµÐ´ = Ð¿Ñ€Ð¸ÐµÐ¼Ð°Ñ‰ ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚; ÐºÐ¾Ð»Ð¾Ð½Ð° = Ð¸Ð·Ñ‚Ð¾Ñ‡Ð½Ð¸Ðº Ð½Ð° Ð´Ð¸Ñ€ÐµÐºÑ‚ÐµÐ½ Ñ‚Ð¾Ð²Ð°Ñ€. Ð”Ð¸Ð°Ð³Ð¾Ð½Ð°Ð»ÑŠÑ‚ Ñ‚Ñ€ÑÐ±Ð²Ð° Ð´Ð° Ð¾ÑÑ‚Ð°Ð½Ðµ 1.0.")
        if st.button("Ð—Ð°Ð¿Ð°Ð·Ð¸ Ð¼Ð°Ñ‚Ñ€Ð¸Ñ†Ð°Ñ‚Ð°", disabled=not can_edit, width="stretch"):
            for component in COMPONENTS:
                edited.loc[component, component] = 1.0
            params["cascade"] = {
                receiver: {source: float(edited.loc[receiver, source]) for source in COMPONENTS} for receiver in COMPONENTS
            }
            commit_bundle(bundle, "cascade_update", "ÐœÐ°Ñ‚Ñ€Ð¸Ñ†Ð°Ñ‚Ð° Ð½Ð° Ñ„Ð¸Ð·Ð¸Ð¾Ð»Ð¾Ð³Ð¸Ñ‡Ð½Ð¸Ñ‚Ðµ Ð²Ð·Ð°Ð¸Ð¼Ð¾Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ñ Ðµ Ð°ÐºÑ‚ÑƒÐ°Ð»Ð¸Ð·Ð¸Ñ€Ð°Ð½Ð°.", athlete_id)

    with tab_methods:
        component = st.selectbox("Ð¤Ð¸Ð»Ñ‚ÑŠÑ€ Ð¿Ð¾ ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚", COMPONENTS, key="method_component")
        methods = bundle["methods"].loc[bundle["methods"]["component"] == component].copy().reset_index(drop=True)
        edited = st.data_editor(
            methods,
            width="stretch",
            hide_index=True,
            disabled=["method_code", "component"] if can_edit else list(methods.columns),
            key=f"methods_{component}_{bundle['version']}",
            num_rows="dynamic" if can_edit else "fixed",
        )
        if st.button("Ð—Ð°Ð¿Ð°Ð·Ð¸ Ð¼ÐµÑ‚Ð¾Ð´Ð¸Ñ‚Ðµ Ð·Ð° ÐºÐ¾Ð¼Ð¿Ð¾Ð½ÐµÐ½Ñ‚Ð°", disabled=not can_edit, width="stretch"):
            other = bundle["methods"].loc[bundle["methods"]["component"] != component]
            bundle["methods"] = pd.concat([other, edited], ignore_index=True)
            commit_bundle(bundle, "methods_update", f"Ð‘Ð°Ð·Ð°Ñ‚Ð° Ð¾Ñ‚ Ð¼ÐµÑ‚Ð¾Ð´Ð¸ Ð·Ð° {component} Ðµ Ð°ÐºÑ‚ÑƒÐ°Ð»Ð¸Ð·Ð¸Ñ€Ð°Ð½Ð°.", athlete_id)

    with tab_audit:
        audit = pd.DataFrame(bundle.get("audit_log", []))
        if audit.empty:
            st.info("Ð’ÑÐµ Ð¾Ñ‰Ðµ Ð½ÑÐ¼Ð° Ñ€ÑŠÑ‡Ð½Ð¸ Ð¿Ñ€Ð¾Ð¼ÐµÐ½Ð¸ Ð² Ñ‚ÐµÐºÑƒÑ‰Ð°Ñ‚Ð° Ð´ÐµÐ¼Ð¾ ÑÐµÑÐ¸Ñ.")
        else:
            st.dataframe(audit.sort_values("timestamp", ascending=False), width="stretch", hide_index=True)
        st.download_button(
            "Ð˜Ð·Ñ‚ÐµÐ³Ð»Ð¸ Ð¶ÑƒÑ€Ð½Ð°Ð»Ð° Â· CSV",
            dataframe_csv_bytes(audit) if not audit.empty else b"",
            file_name=f"audit_log_v{bundle['version']}.csv",
            mime="text/csv",
            width="stretch",
        )


initialize_state()
bundle = st.session_state.bundle
page, athlete_id, role = sync_navigation(bundle)
demo_banner(bundle["version"])
render_flash()

can_edit = role in EDIT_ROLES
if page == "team":
    render_team_page(bundle)
elif page == "models":
    render_models_page()
elif page == "settings":
    render_settings_page(bundle, role, athlete_id)
else:
    analysis = analyze_athlete(bundle, athlete_id, generate_plan=True)
    if page == "dashboard":
        render_dashboard_page(analysis)
    elif page == "load":
        render_load_page(analysis)
    elif page == "recovery":
        render_recovery_page(analysis)
    elif page == "plan":
        render_plan_page(bundle, analysis, can_edit)
    elif page == "calendar":
        render_calendar_goals_page(bundle, analysis, can_edit)
    elif page == "history":
        render_history_page(bundle, analysis, can_edit)
    elif page == "monitoring":
        render_monitoring_page(bundle, analysis, can_edit)
    elif page == "tests":
        render_tests_page(bundle, analysis, can_edit)
    elif page == "simulator":
        render_simulator_page(bundle, analysis, can_edit)
    elif page == "profile":
        render_profile_page(bundle, analysis, can_edit)
