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
    test_history_figure,
    weekly_plan_vs_actual_figure,
    weekly_targets_figure,
)
from biathlon.constants import (
    COMPONENT_LABELS,
    COMPONENT_SHORT,
    COMPONENTS,
    EDIT_ROLES,
    EXPERT_ROLES,
    METRIC_DEFINITIONS,
    ROLE_LABELS,
    TEST_DEFINITIONS,
)
from biathlon.demo_data import DEMO_SEED, generate_activity_stream, generate_demo_bundle
from biathlon.explanations import EXPLANATIONS, explanation_titles, help_text
from biathlon.physiology import analyze_activity_stream
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
    page_title="Biathlon LoadLab · MVP 0.4",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_css()

PAGES = {
    "team": "Отборно табло",
    "dashboard": "Спортист · преглед",
    "load": "Натоварване и 7/40",
    "recovery": "Възстановяване",
    "plan": "Адаптивна програма",
    "calendar": "Календар и цели",
    "history": "История и начални данни",
    "monitoring": "Дневен мониторинг",
    "tests": "Контролни тестове",
    "simulator": "Симулатор „Какво ако“",
    "profile": "Профил и зони",
    "models": "Модели и обяснения",
    "settings": "Експертни настройки",
}

PAGE_ICONS = {
    "team": "👥",
    "dashboard": "🏁",
    "load": "📈",
    "recovery": "🔄",
    "plan": "📅",
    "calendar": "🗓️",
    "history": "🧾",
    "monitoring": "🫀",
    "tests": "🧪",
    "simulator": "🧭",
    "profile": "👤",
    "models": "❓",
    "settings": "⚙️",
}


def initialize_state() -> None:
    if "bundle" not in st.session_state:
        st.session_state.bundle = generate_demo_bundle(seed=DEMO_SEED, history_days=150)
    if "role" not in st.session_state:
        st.session_state.role = "Главен треньор"
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

    role = st.sidebar.selectbox("Роля", ROLE_LABELS, key="role", help="Ролята променя правото за редакция. Наблюдателят работи само в режим преглед.")

    name_map = bundle["athletes"].set_index("athlete_id")["name"].to_dict()
    athlete_id = st.sidebar.selectbox(
        "Спортист",
        valid_athletes,
        key="athlete_id",
        format_func=lambda value: f"{value} · {name_map[value]}",
    )

    page = st.sidebar.radio(
        "Навигация",
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
    st.sidebar.caption(f"Версия на данните: {bundle['version']} · seed: {bundle['seed']}")
    st.sidebar.caption("Решенията са тренировъчна подкрепа, не медицинска диагноза.")
    with st.sidebar.expander("Управление на демото"):
        confirmed = st.checkbox("Потвърждавам нулиране", key="reset_confirm")
        if st.button("Нулирай всички тестови данни", disabled=not confirmed, width="stretch"):
            st.session_state.bundle = generate_demo_bundle(seed=DEMO_SEED, history_days=150)
            st.session_state.flash = ("success", "Демото е върнато към началния повторяем сценарий.")
            st.rerun()
    return page, athlete_id, role


def render_team_page(bundle: dict[str, Any]) -> None:
    page_header("Отборно табло", "Три различни демонстрационни профила с еднаква календарна цел и различна вътрешна реакция.")
    summary = team_summary(bundle)
    mean_readiness = float(summary["Интегрирана готовност"].mean())
    flags = int((summary["Твърд флаг"] == "Да").sum())
    main_events = bundle["calendar"].loc[
        (bundle["calendar"]["type"] == "MAIN_RACE")
        & (pd.to_datetime(bundle["calendar"]["start_date"]).dt.normalize() >= pd.Timestamp.today().normalize())
    ].copy()
    days_to_main = None
    if not main_events.empty:
        days_to_main = int((pd.to_datetime(main_events["start_date"]).min().date() - date.today()).days)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Спортисти", len(summary))
    c2.metric("Средна готовност", f"{mean_readiness:.0f}/100", help=help_text("integrated_readiness"))
    c3.metric("Твърди флагове", flags, help=help_text("monitoring"))
    c4.metric(
        "До основния старт",
        f"{days_to_main} дни" if days_to_main is not None else "Не е зададен",
        help=help_text("periodization"),
    )

    display = summary.drop(columns=["athlete_id"]).copy()
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "Интегрирана готовност": st.column_config.ProgressColumn(
                "Интегрирана готовност",
                help=help_text("integrated_readiness"),
                min_value=0,
                max_value=100,
                format="%.1f",
            )
        },
    )

    st.subheader("Индивидуализация на еднакъв календар")
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
<p>Готовност: <b>{row['Интегрирана готовност']:.0f}/100</b><br>
Статус: {row['Статус']}<br>
Най-слаб компонент: {row['Най-слаб компонент']}</p>
</div>
""",
                unsafe_allow_html=True,
            )
            if st.button("Отвори профила", key=f"open_{athlete_id}", width="stretch"):
                # Не променяме директно st.session_state.athlete_id/nav_page тук,
                # защото тези ключове вече са свързани със sidebar widgets.
                # Променяме само URL параметрите и при следващия rerun sync_navigation()
                # ще синхронизира избрания спортист и страницата преди widget-ите да се създадат.
                st.query_params["page"] = "dashboard"
                st.query_params["athlete"] = athlete_id
                st.rerun()

    st.info(
        "Профил A понася добре аеробния обем; профил B реагира неблагоприятно на натрупване в Z3–Z4; "
        "профил C е чувствителен към Z5 и силови блокове. Това води до различни адаптивни множители и различна програма."
    )


def component_summary_table(analysis: dict[str, Any]) -> pd.DataFrame:
    first_week = analysis["weekly_targets"].loc[analysis["weekly_targets"]["week_no"] == 1].set_index("component")
    table = analysis["load_stats"].join(analysis["integrated"], how="left")
    table = table.join(first_week[["target_index", "target_effective_week", "status"]], how="left", rsuffix="_target")
    table = table.reset_index().rename(
        columns={
            "component": "Компонент",
            "index_7_40": "7/40",
            "Tref": "Tref",
            "load_readiness": "Readiness",
            "monitoring_score": "Мониторинг",
            "test_adjustment": "Тестова корекция",
            "integrated_readiness": "Интегрирана готовност",
            "adaptive_multiplier": "Множител",
            "target_index": "Целеви 7/40",
            "target_effective_week": "Седмична цел E",
            "status": "Роля в мезоцикъла",
        }
    )
    table["Тестова корекция"] = table["Тестова корекция"] * 100.0
    return table[
        [
            "Компонент",
            "7/40",
            "Tref",
            "Readiness",
            "Мониторинг",
            "Тестова корекция",
            "Интегрирана готовност",
            "Множител",
            "Целеви 7/40",
            "Седмична цел E",
            "Роля в мезоцикъла",
        ]
    ]


def render_dashboard_page(analysis: dict[str, Any]) -> None:
    athlete = analysis["athlete"]
    page_header(str(athlete["name"]), f"{athlete['profile_name']} · {athlete['category']}")
    status_badge(analysis["status"], analysis["hard_flag"])
    if analysis["hard_reasons"]:
        st.warning("Активни ограничения: " + "; ".join(analysis["hard_reasons"]))

    max_component = analysis["load_stats"]["index_7_40"].idxmax()
    min_component = analysis["integrated"]["integrated_readiness"].idxmin()
    next_event = analysis["next_event"]
    days_to_event = (pd.Timestamp(next_event["start_date"]).date() - date.today()).days if next_event else None
    next_key = analysis["plan"].loc[analysis["plan"]["focus"].isin(["Z3", "Z4", "Z5", "STR"])].head(1)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Интегрирана готовност",
        f"{analysis['global_readiness']:.0f}/100",
        help=help_text("integrated_readiness"),
    )
    c2.metric(
        f"Най-висок 7/40 · {max_component}",
        f"{analysis['load_stats'].loc[max_component, 'index_7_40']:.2f}",
        help=help_text("seven_forty"),
    )
    c3.metric(
        f"Най-ниска готовност · {min_component}",
        f"{analysis['integrated'].loc[min_component, 'integrated_readiness']:.0f}/100",
        help=help_text("readiness"),
    )
    c4.metric(
        "Следващо събитие",
        f"{days_to_event} дни" if days_to_event is not None else "—",
        delta=str(next_event["name"]) if next_event else None,
        delta_color="off",
        help=help_text("periodization"),
    )

    reduced = analysis["integrated"].loc[analysis["integrated"]["adaptive_multiplier"] < 0.95]
    increased = analysis["integrated"].loc[analysis["integrated"]["adaptive_multiplier"] > 1.001]
    reason_lines = []
    if not reduced.empty:
        reason_lines.append("Намалени/задържани: " + ", ".join(f"{c} ×{r.adaptive_multiplier:.2f}" for c, r in reduced.iterrows()))
    if not increased.empty:
        reason_lines.append("Допуснато умерено изграждане: " + ", ".join(f"{c} ×{r.adaptive_multiplier:.2f}" for c, r in increased.iterrows()))
    if not next_key.empty:
        row = next_key.iloc[0]
        reason_lines.append(f"Следващ специфичен стимул: {row['date'].date()} · {row['focus']} · {row['method']}")
    st.markdown(
        '<div class="reason-box"><b>Последно адаптивно решение</b><br>' + "<br>".join(reason_lines or ["Планът следва базовата периодизация без допълнителна корекция."]) + "</div>",
        unsafe_allow_html=True,
    )

    st.subheader("Компонентно решение")
    table = component_summary_table(analysis)
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={
            "7/40": st.column_config.NumberColumn("7/40", format="%.2f", help=help_text("seven_forty")),
            "Tref": st.column_config.NumberColumn("Tref", format="%.1f", help=help_text("tref")),
            "Readiness": st.column_config.ProgressColumn("Readiness", min_value=0, max_value=100, format="%.0f", help=help_text("readiness")),
            "Мониторинг": st.column_config.ProgressColumn("Мониторинг", min_value=0, max_value=100, format="%.0f", help=help_text("monitoring")),
            "Интегрирана готовност": st.column_config.ProgressColumn(
                "Интегрирана готовност", min_value=0, max_value=100, format="%.0f", help=help_text("integrated_readiness")
            ),
            "Множител": st.column_config.NumberColumn("Множител", format="%.2f", help=help_text("adaptive_multiplier")),
            "Целеви 7/40": st.column_config.NumberColumn("Целеви 7/40", format="%.2f", help=help_text("weekly_target")),
        },
    )

    left, right = st.columns(2)
    with left:
        st.plotly_chart(readiness_figure(analysis["readiness_history"], days=35), width="stretch")
    with right:
        first_six = analysis["weekly_targets"].loc[analysis["weekly_targets"]["week_no"] <= 6]
        st.plotly_chart(weekly_targets_figure(first_six, "target_index"), width="stretch")


def render_load_page(analysis: dict[str, Any]) -> None:
    page_header("Натоварване и индекс 7/40", "Реално време, вътрешнозоново претегляне, директен и ефективен товар.")
    component = st.selectbox("Компонент", COMPONENTS, format_func=lambda c: COMPONENT_LABELS[c], key="load_component")
    row = analysis["load_stats"].loc[component]
    readiness = analysis["load_readiness"].loc[component]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("7/40", f"{row['index_7_40']:.2f}", help=help_text("seven_forty"))
    c2.metric("E7 · средно/ден", f"{row['E7_daily']:.1f}", help=help_text("effective_load"))
    c3.metric("E40 · средно/ден", f"{row['E40_daily']:.1f}", help=help_text("effective_load"))
    c4.metric("Tref", f"{row['Tref']:.1f}", help=help_text("tref"))
    c5.metric("Readiness", f"{readiness['readiness']:.0f}%", help=help_text("readiness"))

    left, right = st.columns(2)
    with left:
        st.plotly_chart(index_7_40_figure(analysis["rolling_load"], component), width="stretch")
    with right:
        st.plotly_chart(effective_load_figure(analysis["rolling_load"], component), width="stretch")

    st.subheader("Детайл на изпълнена тестова активност")
    summaries = analysis["activity_summaries"].sort_values("date", ascending=False).head(40).copy()
    if summaries.empty:
        st.info(
            "Все още няма въведена тренировъчна история за този спортист. "
            "Добави дневни или седмични данни от страницата „История и начални данни“; "
            "след това тук ще се появи анализът реално време → Q → E."
        )
        return

    label_map = {
        row["activity_id"]: f"{pd.Timestamp(row['date']).date()} · {row['sport']} · {row['moving_min']:.0f} мин"
        for _, row in summaries.iterrows()
    }
    selected_id = st.selectbox("Активност", summaries["activity_id"].tolist(), format_func=lambda value: label_map[value], key="activity_detail")
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
                "component": "Компонент",
                "real_min": "Реално време",
                "q_min": "Еквивалентно Q",
                "avg_k": "Среден k",
                "valid_seconds": "Валидни секунди",
            }
        ),
        width="stretch",
        hide_index=True,
        column_config={
            "Реално време": st.column_config.NumberColumn(format="%.2f", help=help_text("real_equivalent")),
            "Еквивалентно Q": st.column_config.NumberColumn(format="%.2f", help=help_text("real_equivalent")),
            "Среден k": st.column_config.NumberColumn(format="%.3f", help=help_text("real_equivalent")),
        },
    )
    st.caption("Потокът е синтетичен, но е на едносекундна решетка и преминава през същата аналитична функция, която може да обработва бъдещ реален входен адаптер.")


def render_recovery_page(analysis: dict[str, Any]) -> None:
    page_header("Динамика на възстановяването", "Остатъчна умора, експоненциално затихване и прогнозен момент за следващ ключов стимул.")
    selected_components = st.multiselect(
        "Компоненти",
        COMPONENTS,
        default=["Z2", "Z3", "Z4", "Z5"],
        format_func=lambda c: COMPONENT_SHORT[c],
        key="recovery_components",
    )
    st.plotly_chart(readiness_figure(analysis["readiness_history"], selected_components or COMPONENTS, days=60), width="stretch")

    current = analysis["load_readiness"].join(analysis["integrated"][["integrated_readiness", "hard_flag"]]).reset_index()
    current = current.rename(
        columns={
            "component": "Компонент",
            "fatigue": "Остатъчна умора",
            "readiness": "Товарна readiness",
            "days_to_full": "Дни до практическо възстановяване",
            "integrated_readiness": "Интегрирана готовност",
            "hard_flag": "Твърд флаг",
        }
    )
    st.dataframe(
        current,
        width="stretch",
        hide_index=True,
        column_config={
            "Товарна readiness": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f", help=help_text("readiness")),
            "Интегрирана готовност": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%.1f", help=help_text("integrated_readiness")
            ),
            "Дни до практическо възстановяване": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    component = st.selectbox("Прогноза без нов стимул", COMPONENTS, format_func=lambda c: COMPONENT_LABELS[c], key="recovery_forecast_component")
    fatigue = float(analysis["load_readiness"].loc[component, "fatigue"])
    tau = float(st.session_state.bundle["parameters"]["recovery"][component]["tau_days"])
    days = np.linspace(0, 7, 57)
    forecast = 100.0 - fatigue * np.exp(-days / max(tau, 1e-9))
    fig = go.Figure(go.Scatter(x=days, y=forecast, mode="lines", name=component))
    fig.add_hline(y=90, line_dash="dot", annotation_text="ключов стимул")
    fig.add_hline(y=float(st.session_state.bundle["parameters"]["practical_full_recovery"]), line_dash="dot", annotation_text="практически възстановен")
    fig.update_layout(title=f"Прогнозна крива без ново натоварване · {component}", xaxis_title="Дни", yaxis_title="Readiness %", yaxis_range=[0, 102], height=380)
    st.plotly_chart(fig, width="stretch")

    recent = analysis["readiness_history"].loc[
        pd.to_datetime(analysis["readiness_history"]["date"]) >= pd.Timestamp.today().normalize() - pd.Timedelta(days=14)
    ].copy()
    recent = recent.loc[recent["impulse"] > 0.05].sort_values(["date", "component"], ascending=[False, True]).head(30)
    with st.expander("Последни тренировъчни импулси"):
        st.dataframe(recent, width="stretch", hide_index=True)


def render_plan_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    page_header(
        "Адаптивна тренировъчна програма",
        "Седмична компонентна цел → годишен контекст → седмична структура → директен товар → реално време → конкретен метод.",
    )
    plan = analysis["plan"].copy()
    training_rows = plan.loc[plan["focus"] != "REST"] if not plan.empty else plan
    snapshot = analysis["decision_snapshot"].get("plan", {})
    preferences = analysis["planning_preferences"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Тренировъчни сесии",
        f"{len(training_rows)}/{preferences['sessions_per_week']}",
        help=help_text("weekly_structure"),
    )
    c2.metric("Планиран обем", f"{training_rows['total_real_min'].sum() / 60.0:.1f} h")
    rest_labels = [WEEKDAY_LABELS[day] for day in preferences["rest_days"]]
    c3.metric("Почивни дни", ", ".join(rest_labels) if rest_labels else "Няма")
    c4.metric(
        "Двойна прагова",
        "Активна" if snapshot.get("double_threshold_active") else "Неактивна",
        help=help_text("double_threshold"),
    )

    for warning in snapshot.get("warnings", []):
        st.warning(warning)

    tab_volume, tab_wave, tab_week = st.tabs(
        ["Реално срещу план", "Дългосрочна динамика", "Следващи 7 дни"]
    )

    with tab_volume:
        st.plotly_chart(weekly_plan_vs_actual_figure(analysis["volume_trajectory"]), width="stretch")
        context = analysis["annual_context"]
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Последни 4 седмици", f"{context['recent_weekly_hours']:.1f} h/седм.")
        v2.metric("План · следващи 7 дни", f"{training_rows['total_real_min'].sum() / 60.0:.1f} h")
        v3.metric("Нужно средно до края", f"{context['required_weekly_hours']:.1f} h/седм.")
        v4.metric("Корекция от сезонната цел", f"×{context['volume_factor']:.3f}")
        st.caption(
            "Линията „Реално изпълнено“ се формира само от историята и не се променя при редакция "
            "на сезонната цел. Линията „Адаптивен план“ се преизчислява след всяка промяна на целта, "
            "календара, readiness или историята."
        )
        if context.get("factor_limited"):
            st.warning(context.get("feasibility_status", "Корекцията е ограничена от защитен лимит."))

    with tab_wave:
        metric = st.radio(
            "Графика",
            ["target_effective_week", "target_index"],
            horizontal=True,
            format_func=lambda x: "Ефективен товар" if x == "target_effective_week" else "Целеви 7/40",
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
                "component": "Компонент",
                "target_effective": "Цел E",
                "planned_effective": "План E",
                "target_direct_q": "Цел Q",
                "planned_direct_q": "План Q",
                "remaining_direct_q": "Остатък Q",
                "completion_pct": "Изпълнение %",
            }
        )
        st.dataframe(
            comp_display,
            width="stretch",
            hide_index=True,
            column_config={"Изпълнение %": st.column_config.ProgressColumn(min_value=0, max_value=130, format="%.0f")},
        )

        editor_columns = [
            "date",
            "day",
            "session_no",
            "time_of_day",
            "focus",
            "method",
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
            disabled=["date", "day", "session_no", "time_of_day", "focus", "method"] if can_edit else list(editable.columns),
            column_config={
                "date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"),
                "day": "Ден",
                "session_no": st.column_config.NumberColumn("Сесия", format="%d"),
                "time_of_day": "Част на деня",
                "focus": "Фокус",
                "method": "Метод",
                "total_real_min": st.column_config.NumberColumn("Реални минути", min_value=0.0, max_value=360.0, step=5.0),
                "status": st.column_config.SelectboxColumn("Статус", options=["Предложена", "Одобрена", "Отхвърлена"]),
                "locked": st.column_config.CheckboxColumn("Заключена"),
                "coach_note": st.column_config.TextColumn("Бележка на треньора"),
            },
        )
        b1, b2, b3 = st.columns([1, 1, 2])
        if b1.button("Запази редакциите", disabled=not can_edit, width="stretch"):
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
                "Редакциите на седмичната програма са записани като нова версия.",
                str(analysis["athlete"]["athlete_id"]),
            )
        if b2.button("Одобри всички сесии", disabled=not can_edit, width="stretch"):
            for _, row in plan.iterrows():
                key = (
                    f"{analysis['athlete']['athlete_id']}|"
                    f"{pd.Timestamp(row['date']).date().isoformat()}|{int(row['session_no'])}"
                )
                current = bundle["plan_overrides"].get(key, {})
                current.update(
                    {
                        "status": "Одобрена",
                        "locked": bool(current.get("locked", False)),
                        "coach_note": current.get("coach_note", ""),
                    }
                )
                bundle["plan_overrides"][key] = current
            commit_bundle(
                bundle,
                "plan_approve",
                "Всички сесии от текущата програма са одобрени.",
                str(analysis["athlete"]["athlete_id"]),
            )
        b3.caption(
            "Броят сесии, почивните дни и двойните тренировки се управляват от „Календар и цели“. "
            "Заключените записи се пазят като треньорски override."
        )

        d1, d2 = st.columns(2)
        d1.download_button(
            "Изтегли програмата · CSV",
            dataframe_csv_bytes(plan),
            file_name=f"plan_{analysis['athlete']['athlete_id']}_{date.today().isoformat()}.csv",
            mime="text/csv",
            width="stretch",
        )
        d2.download_button(
            "Изтегли DecisionSnapshot · JSON",
            json_bytes(analysis["decision_snapshot"]),
            file_name=f"decision_snapshot_{analysis['athlete']['athlete_id']}_{date.today().isoformat()}.json",
            mime="application/json",
            width="stretch",
        )

        st.subheader("Описание по сесии")
        for _, row in plan.iterrows():
            label = (
                f"{pd.Timestamp(row['date']).strftime('%d.%m')} · {row['day']} · "
                f"{row['time_of_day']} · {row['focus']} · {row['method']}"
            )
            with st.expander(label):
                st.write(row["description"])
                st.markdown(f"**Обяснение на решението:** {row['explanation']}")
                details = pd.DataFrame(
                    {
                        "Компонент": COMPONENTS,
                        "Реални минути": [row[f"real_{c}"] for c in COMPONENTS],
                        "Директно Q": [row[f"q_{c}"] for c in COMPONENTS],
                        "Ефективно E": [row[f"e_{c}"] for c in COMPONENTS],
                    }
                )
                st.dataframe(details, width="stretch", hide_index=True)


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
        "Календар, сезонни цели и седмична структура",
        "Редактирай основни и контролни стартове, лагери, годишната обемна цел и правилата за разпределение на сесиите.",
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Сезонна цел", f"{context['target_hours']:.0f} h", help=help_text("annual_goal"))
    c2.metric("Изпълнено", f"{context['completed_hours']:.1f} h", f"{context['progress_pct']:.1f}%")
    c3.metric("Нужно средно до края", f"{context['required_weekly_hours']:.1f} h/седм.")
    c4.metric("Обемен фактор", f"{context['volume_factor']:.3f}", help=help_text("annual_goal"))

    tab_goal, tab_calendar, tab_structure = st.tabs(
        ["Сезонна цел", "Стартове, лагери и тестове", "Седмична структура"]
    )

    with tab_goal:
        left, right = st.columns([1, 1.25])
        with left:
            with st.form(f"season_goal_{athlete_id}"):
                season_start = st.date_input(
                    "Начало на отчетния сезон",
                    value=pd.Timestamp(preferences["season_start"]).date(),
                    disabled=not can_edit,
                    help=help_text("annual_goal"),
                )
                season_end = st.date_input(
                    "Край на отчетния сезон",
                    value=pd.Timestamp(preferences["season_end"]).date(),
                    disabled=not can_edit,
                )
                annual_target = st.number_input(
                    "Целеви обем за сезона · часове",
                    min_value=50.0,
                    max_value=1500.0,
                    value=float(preferences["annual_target_hours"]),
                    step=10.0,
                    disabled=not can_edit,
                    help=(
                        "Например 600 часа за една година. Реалната линия остава непроменена, "
                        "а плановата линия се преизчислява плавно, основно чрез Z1–Z2."
                    ),
                )
                goal_influence = st.slider(
                    "Тежест на годишната цел",
                    0.0,
                    1.00,
                    float(preferences["annual_goal_influence"]),
                    0.05,
                    disabled=not can_edit,
                    help="0 означава само информационна цел; по-висока стойност допуска ограничена корекция на седмичния обем.",
                )
                max_factor = st.slider(
                    "Максимално увеличение от годишната цел",
                    1.00,
                    1.50,
                    float(preferences["max_volume_factor"]),
                    0.01,
                    disabled=not can_edit,
                )
                submit_goal = st.form_submit_button("Запази сезонната цел", disabled=not can_edit, width="stretch")
            if submit_goal:
                if pd.Timestamp(season_end) <= pd.Timestamp(season_start):
                    st.error("Краят на сезона трябва да е след началото.")
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
                        "Сезонната обемна цел и отчетният период са актуализирани.",
                        athlete_id,
                    )
        with right:
            st.plotly_chart(annual_goal_figure(context, analysis["volume_trajectory"]), width="stretch")
            st.plotly_chart(weekly_plan_vs_actual_figure(analysis["volume_trajectory"]), width="stretch")
            st.info(
                f"Статус: **{context['status']}**. {context.get('feasibility_status', '')}. "
                f"При запазване на последния 4-седмичен обем прогнозата е около "
                f"**{context['forecast_hours']:.0f} h** до края на периода. "
                f"Въведената история покрива приблизително **{context['season_history_coverage'] * 100:.0f}%** "
                "от изминалата част на сезона. Синята историческа линия не се променя от целта; "
                "променят се бъдещият адаптивен план и целевата траектория. Високите интензивности "
                "не се увеличават само за достигане на часовата цел."
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
        ].rename(columns={"type_label": "Тип"}).reset_index(drop=True)
        edited = st.data_editor(
            display,
            width="stretch",
            hide_index=True,
            num_rows="dynamic" if can_edit else "fixed",
            disabled=["event_id"] if can_edit else list(display.columns),
            key=f"calendar_editor_{athlete_id}_{bundle['version']}",
            column_config={
                "event_id": st.column_config.TextColumn("ID", help="Създава се автоматично за нов ред."),
                "Тип": st.column_config.SelectboxColumn("Тип", options=list(type_to_label.values()), required=True),
                "name": st.column_config.TextColumn("Име", required=True),
                "start_date": st.column_config.DateColumn("Начало", format="DD.MM.YYYY", required=True),
                "end_date": st.column_config.DateColumn("Край", format="DD.MM.YYYY", required=True),
                "priority": st.column_config.SelectboxColumn("Приоритет", options=["A", "B", "C"]),
                "goal": st.column_config.TextColumn("Цел / акцент"),
                "locked": st.column_config.CheckboxColumn("Заключено"),
                "note": st.column_config.TextColumn("Бележка"),
            },
        )
        st.caption(
            "Редовете могат да се добавят и изтриват. Промяната на основния старт преизчислява фазата, "
            "мезоцикличната динамика и тейпъра; лагерите и контролните стартове променят календарния фактор."
        )
        if st.button("Запази календара и преизчисли", disabled=not can_edit, width="stretch"):
            rows: list[dict[str, Any]] = []
            errors: list[str] = []
            for row_index, row in edited.iterrows():
                name = str(row.get("name", "") or "").strip()
                if not name:
                    continue
                start_value = pd.to_datetime(row.get("start_date"), errors="coerce")
                end_value = pd.to_datetime(row.get("end_date"), errors="coerce")
                if pd.isna(start_value) or pd.isna(end_value):
                    errors.append(f"Ред {row_index + 1}: липсва валидна дата.")
                    continue
                if end_value < start_value:
                    errors.append(f"Ред {row_index + 1}: крайната дата е преди началната.")
                    continue
                label = str(row.get("Тип", ""))
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
                reason = "Календарът е актуализиран и планът е преизчислен."
                if future_main.empty:
                    reason += " Няма бъдещ основен старт; временно се използва виртуален 16-седмичен хоризонт."
                commit_bundle(bundle, "calendar_update", reason, athlete_id)

    with tab_structure:
        weekday_options = list(WEEKDAY_LABELS.values())
        with st.form(f"weekly_structure_{athlete_id}"):
            c1, c2, c3 = st.columns(3)
            sessions_per_week = c1.number_input(
                "Брой тренировъчни сесии седмично",
                min_value=1,
                max_value=14,
                value=int(preferences["sessions_per_week"]),
                step=1,
                disabled=not can_edit,
                help=help_text("weekly_structure"),
            )
            rest_days_labels = c1.multiselect(
                "Дни за пълна почивка",
                weekday_options,
                default=[WEEKDAY_LABELS[day] for day in preferences["rest_days"]],
                disabled=not can_edit,
            )
            long_day_label = c1.selectbox(
                "Ден за дълга аеробна тренировка",
                weekday_options,
                index=weekday_options.index(WEEKDAY_LABELS[preferences["long_session_day"]]),
                disabled=not can_edit,
            )

            double_days_labels = c2.multiselect(
                "Разрешени дни с две сесии",
                weekday_options,
                default=[WEEKDAY_LABELS[day] for day in preferences["double_session_days"]],
                disabled=not can_edit,
            )
            intensity_days_labels = c2.multiselect(
                "Предпочитани интензивни дни",
                weekday_options,
                default=[WEEKDAY_LABELS[day] for day in preferences["intensity_days"]],
                disabled=not can_edit,
            )
            strength_days_labels = c2.multiselect(
                "Предпочитани силови дни",
                weekday_options,
                default=[WEEKDAY_LABELS[day] for day in preferences["strength_days"]],
                disabled=not can_edit,
            )
            max_key = c2.number_input(
                "Максимум ключови сесии седмично",
                min_value=0,
                max_value=8,
                value=int(preferences["max_key_sessions_per_week"]),
                step=1,
                disabled=not can_edit,
            )

            double_threshold = c3.checkbox(
                "Разреши двойна прагова тренировка",
                value=bool(preferences["double_threshold_enabled"]),
                disabled=not can_edit,
                help=help_text("double_threshold"),
            )
            dt_day_label = c3.selectbox(
                "Ден за двойна прагова",
                weekday_options,
                index=weekday_options.index(WEEKDAY_LABELS[preferences["double_threshold_day"]]),
                disabled=not can_edit,
            )
            dt_components = c3.multiselect(
                "Прагова комбинация",
                ["Z3", "Z4"],
                default=preferences["double_threshold_components"],
                disabled=not can_edit,
            )
            dt_readiness = c3.slider(
                "Минимална интегрирана готовност",
                70.0,
                100.0,
                float(preferences["double_threshold_min_readiness"]),
                1.0,
                disabled=not can_edit,
            )
            dt_phase = c3.slider(
                "Допустима част от подготовката",
                0.0,
                1.0,
                (
                    float(preferences["double_threshold_phase_min"]),
                    float(preferences["double_threshold_phase_max"]),
                ),
                0.05,
                disabled=not can_edit,
                help="0 = начало на подготовката; 1 = основен старт.",
            )
            submit_structure = st.form_submit_button(
                "Запази седмичната структура", disabled=not can_edit, width="stretch"
            )

        if submit_structure:
            rest_codes = [WEEKDAY_BY_LABEL[label] for label in rest_days_labels]
            max_possible = 2 * (7 - len(rest_codes))
            dt_code = WEEKDAY_BY_LABEL[dt_day_label]
            errors: list[str] = []
            if max_possible < 1:
                errors.append("Не може всички дни да бъдат зададени като пълна почивка.")
            if int(sessions_per_week) > max_possible:
                errors.append(
                    f"При избраните почивни дни са възможни максимум {max_possible} сесии (до две на ден)."
                )
            if double_threshold and dt_code in rest_codes:
                errors.append("Денят за двойна прагова не може едновременно да е ден за пълна почивка.")
            if double_threshold and int(max_key) < 2:
                errors.append("За двойна прагова са нужни поне две разрешени ключови сесии.")
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
                    "Броят сесии, почивните дни и правилата за двойни тренировки са актуализирани.",
                    athlete_id,
                )

        st.subheader("Предварителен седмичен скелет")
        preview = build_week_structure(date.today(), preferences).copy()
        preview["Тренировка"] = preview["planned_training"].map({True: "Да", False: "Почивка"})
        st.dataframe(
            preview[["date", "day", "session_no", "time_of_day", "slot_type", "Тренировка"]],
            width="stretch",
            hide_index=True,
            column_config={"date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY")},
        )
        st.caption(
            "Това е структурният скелет. Конкретният фокус и обем се определят след проверка на 7/40, "
            "възстановяването, мониторинга, тестовете, фазата и календарните събития."
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
        "История на натоварването и начални данни",
        "Въведи реални минути по зони и сила. Историята създава 40-дневната адаптационна база, Tref, 7/40 и началната програма.",
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Активности", len(activities))
    c2.metric("Дни с натоварване", history_days)
    c3.metric("Общо въведен обем", f"{total_minutes / 60.0:.1f} h")
    c4.metric(
        "Надеждност на 40-дневната база",
        f"{analysis['annual_context']['history_reliability'] * 100:.0f}%",
        help=help_text("manual_history"),
    )
    if history_days < 40:
        st.warning(
            "Има по-малко от 40 дни с история. Системата ще работи със стабилизиращите базови товари, "
            "но надеждността на индивидуалния 7/40 и Tref ще бъде по-ниска."
        )

    tab_add, tab_table, tab_weekly = st.tabs(
        ["Добави тренировка", "Таблица и CSV", "Бързо въвеждане по седмици"]
    )

    with tab_add:
        with st.form(f"manual_activity_{athlete_id}"):
            c1, c2, c3 = st.columns(3)
            activity_date = c1.date_input(
                "Дата",
                value=date.today() - timedelta(days=1),
                disabled=not can_edit,
            )
            sport = c1.text_input("Средство / спорт", value="Ролкови ски", disabled=not can_edit)
            rpe = c1.slider("RPE на сесията", 0.0, 10.0, 4.0, 0.5, disabled=not can_edit)
            z1 = c2.number_input("Z1 · реални минути", 0.0, 600.0, 20.0, 5.0, disabled=not can_edit)
            z2 = c2.number_input("Z2 · реални минути", 0.0, 600.0, 60.0, 5.0, disabled=not can_edit)
            z3 = c2.number_input("Z3 · реални минути", 0.0, 180.0, 0.0, 2.0, disabled=not can_edit)
            z4 = c3.number_input("Z4 · реални минути", 0.0, 120.0, 0.0, 1.0, disabled=not can_edit)
            z5 = c3.number_input("Z5 · реални минути", 0.0, 90.0, 0.0, 1.0, disabled=not can_edit)
            strength = c3.number_input("Сила · реални минути", 0.0, 180.0, 0.0, 5.0, disabled=not can_edit)
            note = st.text_area("Бележка", value="Ръчно въведена тренировка.", disabled=not can_edit)
            submit_activity = st.form_submit_button("Добави към историята", disabled=not can_edit, width="stretch")
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
                        "STR": strength,
                        "note": note,
                    }
                ]
            )
            new_activity = daily_table_to_activities(
                table, athlete_id, preferences, source="manual_single_entry"
            )
            if new_activity.empty:
                st.error("Въведи поне една минута натоварване.")
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
                    "Ръчната тренировка е добавена и всички модели са преизчислени.",
                    athlete_id,
                )

    with tab_table:
        period = st.selectbox(
            "Период за редакция",
            [40, 90, 180, 0],
            format_func=lambda value: "Цялата история" if value == 0 else f"Последни {value} дни",
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
            disabled=not can_edit,
            key=f"history_editor_{athlete_id}_{period}_{bundle['version']}",
            column_config={
                "date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY", required=True),
                "sport": st.column_config.TextColumn("Средство / спорт"),
                "rpe": st.column_config.NumberColumn("RPE", min_value=0.0, max_value=10.0, step=0.5),
                **{
                    component: st.column_config.NumberColumn(
                        f"{component} · мин", min_value=0.0, max_value=700.0, step=1.0
                    )
                    for component in COMPONENTS
                },
                "note": st.column_config.TextColumn("Бележка"),
            },
        )
        st.caption(
            f"„Запази таблицата“ заменя историята на спортиста за периода {period_start.date()} – {period_end.date()}. "
            "Един ред представлява сумарното реално време за деня."
        )
        if st.button("Запази таблицата и преизчисли", disabled=not can_edit, width="stretch"):
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
                    "Дневната история за избрания период е заменена и моделът е преизчислен.",
                    athlete_id,
                )

        st.divider()
        c1, c2 = st.columns([1, 1])
        with c1:
            st.download_button(
                "Изтегли CSV шаблон",
                dataframe_csv_bytes(history_template()),
                file_name="biathlon_history_template.csv",
                mime="text/csv",
                width="stretch",
            )
        with c2:
            uploaded = st.file_uploader(
                "Качи CSV история",
                type=["csv"],
                key=f"history_upload_{athlete_id}",
                disabled=not can_edit,
            )
        if uploaded is not None:
            try:
                uploaded_df = pd.read_csv(uploaded)
            except Exception as exc:
                st.error(f"CSV файлът не може да бъде прочетен: {exc}")
            else:
                st.dataframe(uploaded_df.head(20), width="stretch", hide_index=True)
                import_mode = st.radio(
                    "Режим на импорта",
                    ["Добави", "Замени датите от файла", "Замени цялата история на спортиста"],
                    horizontal=True,
                    key=f"history_import_mode_{athlete_id}",
                )
                if st.button("Импортирай CSV", disabled=not can_edit, width="stretch"):
                    try:
                        imported = daily_table_to_activities(
                            uploaded_df, athlete_id, preferences, source="manual_csv_import"
                        )
                    except Exception as exc:
                        st.error(f"Невалиден формат: {exc}")
                    else:
                        all_activities = bundle["activities"].copy()
                        keep = pd.Series(True, index=all_activities.index)
                        if import_mode == "Замени цялата история на спортиста":
                            keep &= all_activities["athlete_id"].astype(str) != athlete_id
                        elif import_mode == "Замени датите от файла" and not imported.empty:
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
                            f"CSV историята е импортирана в режим „{import_mode}“.",
                            athlete_id,
                        )

        with st.expander("Изчистване на историята"):
            confirm_clear = st.checkbox(
                "Потвърждавам изтриването на историята за този спортист",
                key=f"clear_history_confirm_{athlete_id}",
                disabled=not can_edit,
            )
            if st.button(
                "Изтрий историята на спортиста",
                disabled=not can_edit or not confirm_clear,
                width="stretch",
            ):
                bundle["activities"] = bundle["activities"].loc[
                    bundle["activities"]["athlete_id"].astype(str) != athlete_id
                ].copy().reset_index(drop=True)
                commit_bundle(
                    bundle,
                    "history_clear",
                    "Историята на спортиста е изтрита. Планът временно използва стабилизиращи базови товари.",
                    athlete_id,
                )

    with tab_weekly:
        st.warning(
            "Този режим е за бързо първоначално стартиране, когато разполагаш само със седмични тотали. "
            "Системата ги разпределя детерминирано по дни и маркира източника като приблизителен. "
            "За най-точен 7/40 използвай реални дневни данни."
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
                    "STR": 45.0,
                    "rpe": 4.5,
                    "note": "Примерна седмица — промени стойностите.",
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
                "week_start": st.column_config.DateColumn("Начало на седмицата", format="DD.MM.YYYY"),
                "sessions": st.column_config.NumberColumn("Сесии", min_value=1, max_value=14, step=1),
                **{
                    component: st.column_config.NumberColumn(
                        f"{component} · общо мин", min_value=0.0, max_value=3000.0, step=5.0
                    )
                    for component in COMPONENTS
                },
                "rpe": st.column_config.NumberColumn("Средно RPE", min_value=0.0, max_value=10.0, step=0.5),
                "note": st.column_config.TextColumn("Бележка"),
            },
        )
        weekly_mode = st.radio(
            "Режим",
            ["Добави", "Замени седмиците от таблицата"],
            horizontal=True,
            key=f"weekly_history_mode_{athlete_id}",
        )
        if st.button("Създай дневна история от седмичните обеми", disabled=not can_edit, width="stretch"):
            try:
                generated = weekly_totals_to_activities(weekly_editor, athlete_id, preferences)
            except Exception as exc:
                st.error(f"Седмичните данни не могат да бъдат преобразувани: {exc}")
            else:
                all_activities = bundle["activities"].copy()
                keep = pd.Series(True, index=all_activities.index)
                if weekly_mode == "Замени седмиците от таблицата" and not generated.empty:
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
                    "Седмичните обеми са разпределени в дневна история и моделът е преизчислен.",
                    athlete_id,
                )


def render_monitoring_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(analysis["athlete"]["athlete_id"])
    page_header("Дневен мониторинг", "Ръчни субективни и обективни показатели с текуща стойност, 7/40 тенденция и критични флагове.")
    athlete_wellness = bundle["wellness"].loc[bundle["wellness"]["athlete_id"] == athlete_id].sort_values("date")
    latest = athlete_wellness.iloc[-1]

    with st.form("wellness_form"):
        st.subheader(f"Сутрешен запис · {date.today().strftime('%d.%m.%Y')}")
        c1, c2, c3 = st.columns(3)
        sleep_quality = c1.slider("Качество на съня", 1.0, 10.0, float(latest["sleep_quality"]), 0.5, help=help_text("monitoring"), disabled=not can_edit)
        fatigue = c1.slider("Обща умора", 0.0, 10.0, float(latest["fatigue"]), 0.5, help=help_text("monitoring"), disabled=not can_edit)
        stress = c1.slider("Психологически стрес", 0.0, 10.0, float(latest["stress"]), 0.5, disabled=not can_edit)
        soreness_legs = c2.slider("Болезненост · крака", 0.0, 10.0, float(latest["soreness_legs"]), 0.5, disabled=not can_edit)
        soreness_upper = c2.slider("Болезненост · горна част", 0.0, 10.0, float(latest["soreness_upper"]), 0.5, disabled=not can_edit)
        pain = c2.slider("Болка / симптом", 0.0, 10.0, float(latest["pain"]), 0.5, help=help_text("monitoring"), disabled=not can_edit)
        motivation = c3.slider("Мотивация / готовност", 1.0, 10.0, float(latest["motivation"]), 0.5, disabled=not can_edit)
        morning_hr = c3.number_input("Сутрешен пулс", 30.0, 120.0, float(latest["morning_hr"]), 1.0, disabled=not can_edit)
        hrv = c3.number_input("HRV · ms", 5.0, 250.0, float(latest["hrv"]), 1.0, disabled=not can_edit)
        sleep_hours = c3.number_input("Продължителност на съня · h", 3.0, 12.0, float(latest["sleep_hours"]), 0.25, disabled=not can_edit)
        illness = st.checkbox("Симптоми / заболяване", value=bool(latest.get("illness", False)), disabled=not can_edit)
        note = st.text_area("Бележка", value="", disabled=not can_edit)
        submitted = st.form_submit_button("Запиши и преизчисли", disabled=not can_edit, width="stretch")

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
        commit_bundle(bundle, "wellness_update", "Сутрешните показатели са записани и бъдещият план е преизчислен.", athlete_id)

    details = analysis["metric_details"].reset_index().rename(
        columns={
            "label": "Показател",
            "current": "Текущо",
            "mean7": "Средно 7 дни",
            "mean40": "Средно 40 дни",
            "index_7_40": "7/40",
            "z_favorable": "Посочно Z",
            "score": "Оценка",
            "reliability": "Надеждност",
            "critical": "Критичен флаг",
        }
    )
    details["Надеждност"] *= 100.0
    st.dataframe(
        details[["Показател", "Текущо", "Средно 7 дни", "Средно 40 дни", "7/40", "Посочно Z", "Оценка", "Надеждност", "Критичен флаг"]],
        width="stretch",
        hide_index=True,
        column_config={
            "7/40": st.column_config.NumberColumn(format="%.2f", help=help_text("monitoring")),
            "Оценка": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
            "Надеждност": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f", help=help_text("quality")),
        },
    )

    metric = st.selectbox(
        "Показател за графика",
        list(METRIC_DEFINITIONS),
        format_func=lambda m: METRIC_DEFINITIONS[m]["label"],
        key="monitor_chart_metric",
    )
    st.plotly_chart(monitoring_history_figure(bundle["wellness"], athlete_id, metric), width="stretch")


def render_tests_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(analysis["athlete"]["athlete_id"])
    page_header("Контролни тестове", "Ръчни резултати, валидност, сравнимост, динамика и ограничено компонентно влияние.")
    test_code = st.selectbox("Тест", list(TEST_DEFINITIONS), format_func=lambda code: TEST_DEFINITIONS[code]["label"], key="test_code")
    definition = TEST_DEFINITIONS[test_code]
    st.plotly_chart(test_history_figure(bundle["tests"], athlete_id, test_code), width="stretch")

    history = bundle["tests"].loc[(bundle["tests"]["athlete_id"] == athlete_id) & (bundle["tests"]["test_code"] == test_code)].sort_values("date")
    latest = history.iloc[-1]
    with st.form("test_entry_form"):
        c1, c2, c3 = st.columns(3)
        test_date = c1.date_input("Дата", value=date.today(), disabled=not can_edit)
        primary = c1.number_input(
            f"{definition['primary_label']} · {definition['primary_unit']}",
            value=float(latest["primary_value"]),
            step=0.1,
            disabled=not can_edit,
        )
        secondary = c2.number_input(
            f"{definition['secondary_label']} · {definition['secondary_unit']}",
            value=float(latest["secondary_value"]),
            step=0.1,
            disabled=not can_edit,
        )
        comparability = c2.slider("Сравнимост", 0.0, 1.0, 0.95, 0.05, help=help_text("tests"), disabled=not can_edit)
        valid = c3.checkbox("Валиден тест", value=True, disabled=not can_edit)
        protocol = c3.text_input("Версия на протокола", value="1.0", disabled=not can_edit)
        note = st.text_area("Условия и бележка", value="Ръчно въведен тестов резултат.", disabled=not can_edit)
        submitted = st.form_submit_button("Добави тест и преизчисли", disabled=not can_edit, width="stretch")

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
        commit_bundle(bundle, "test_add", f"Добавен е нов резултат за „{definition['label']}“.", athlete_id)

    st.subheader("Последна валидна динамика")
    if analysis["test_details"].empty:
        st.info("Няма достатъчно данни за сравнение.")
    else:
        display = analysis["test_details"].reset_index().rename(
            columns={
                "label": "Тест",
                "date": "Дата",
                "primary_change_pct": "Промяна · основен %",
                "secondary_change_pct": "Промяна · вторичен %",
                "composite_change_pct": "Комплексна промяна %",
                "comparability": "Сравнимост",
                "reliability": "Надеждност",
                "valid": "Валиден",
            }
        )
        st.dataframe(display[["Тест", "Дата", "Промяна · основен %", "Промяна · вторичен %", "Комплексна промяна %", "Сравнимост", "Надеждност", "Валиден"]], width="stretch", hide_index=True)
    adjustments = analysis["test_adjustments"].rename("Корекция").reset_index().rename(columns={"index": "Компонент"})
    adjustments["Корекция"] *= 100.0
    st.dataframe(adjustments, width="stretch", hide_index=True)
    st.caption("Положителната корекция е ограничена до +5%, отрицателната — до −10%, и се прилага само в контекста на текущата готовност.")


def comparison_metrics(before: dict[str, Any], after: dict[str, Any], component: str) -> None:
    before_first = before["weekly_targets"].loc[(before["weekly_targets"]["week_no"] == 1) & (before["weekly_targets"]["component"] == component)].iloc[0]
    after_first = after["weekly_targets"].loc[(after["weekly_targets"]["week_no"] == 1) & (after["weekly_targets"]["component"] == component)].iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    b = float(before["load_stats"].loc[component, "index_7_40"])
    a = float(after["load_stats"].loc[component, "index_7_40"])
    c1.metric(f"{component} 7/40", f"{a:.2f}", f"{a-b:+.2f}", help=help_text("seven_forty"))
    b = float(before["load_readiness"].loc[component, "readiness"])
    a = float(after["load_readiness"].loc[component, "readiness"])
    c2.metric(f"{component} readiness", f"{a:.0f}%", f"{a-b:+.0f} п.п.", help=help_text("readiness"))
    b = float(before_first["target_effective_week"])
    a = float(after_first["target_effective_week"])
    c3.metric("Седмична цел E", f"{a:.1f}", f"{a-b:+.1f}", help=help_text("weekly_target"))
    b = float(before["integrated"].loc[component, "adaptive_multiplier"])
    a = float(after["integrated"].loc[component, "adaptive_multiplier"])
    c4.metric("Адаптивен множител", f"{a:.2f}", f"{a-b:+.2f}", help=help_text("adaptive_multiplier"))


def plan_diff(before: dict[str, Any], after: dict[str, Any]) -> pd.DataFrame:
    """Сравнява сесия със съответната сесия, без декартово умножение при двойни дни."""

    keys = ["date", "session_no"]
    left = before["plan"][[*keys, "time_of_day", "focus", "method", "total_real_min"]].rename(
        columns={
            "time_of_day": "Част на деня · преди",
            "focus": "Фокус · преди",
            "method": "Метод · преди",
            "total_real_min": "Минути · преди",
        }
    )
    right = after["plan"][[*keys, "time_of_day", "focus", "method", "total_real_min"]].rename(
        columns={
            "time_of_day": "Част на деня · след",
            "focus": "Фокус · след",
            "method": "Метод · след",
            "total_real_min": "Минути · след",
        }
    )
    merged = left.merge(right, on=keys, how="outer").sort_values(keys).reset_index(drop=True)
    merged["Минути · преди"] = pd.to_numeric(merged["Минути · преди"], errors="coerce").fillna(0.0)
    merged["Минути · след"] = pd.to_numeric(merged["Минути · след"], errors="coerce").fillna(0.0)
    merged["Δ мин"] = merged["Минути · след"] - merged["Минути · преди"]
    return merged


def render_simulator_page(bundle: dict[str, Any], before: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(before["athlete"]["athlete_id"])
    page_header("Симулатор „Какво ще стане, ако“", "Промяната се изчислява върху копие. Одобреното състояние не се променя, докато не натиснете „Приеми сценария“.")
    scenario_type = st.selectbox(
        "Сценарий",
        ["Активност · Z3 време и позиция", "Мониторинг · три неблагоприятни дни", "Контролен тест", "Календар · основен старт"],
        key="scenario_type",
    )
    scenario_bundle = deepcopy(bundle)
    scenario_description = ""
    focus_component = "Z3"

    if scenario_type.startswith("Активност"):
        eligible = before["activities"].loc[before["activities"]["real_Z3"] > 3].sort_values("date", ascending=False).head(25)
        labels = {row["activity_id"]: f"{pd.Timestamp(row['date']).date()} · Z3 {row['real_Z3']:.0f} мин" for _, row in eligible.iterrows()}
        activity_id = st.selectbox("Активност", eligible["activity_id"].tolist(), format_func=lambda x: labels[x], key="scenario_activity")
        current = eligible.loc[eligible["activity_id"] == activity_id].iloc[0]
        c1, c2 = st.columns(2)
        new_z3 = c1.slider("Реално време в Z3 · мин", 0.0, 80.0, float(current["real_Z3"]), 1.0, help=help_text("real_equivalent"))
        new_pos = c2.slider("Средна позиция в Z3 · %", 5, 95, int(round(float(current["pos_Z3"]) * 100)), 1, help=help_text("real_equivalent")) / 100.0
        mask = scenario_bundle["activities"]["activity_id"] == activity_id
        scenario_bundle["activities"].loc[mask, "real_Z3"] = new_z3
        scenario_bundle["activities"].loc[mask, "pos_Z3"] = new_pos
        scenario_description = f"Активност {activity_id}: Z3 → {new_z3:.0f} мин при позиция {new_pos*100:.0f}% от зоната."
        focus_component = "Z3"
    elif scenario_type.startswith("Мониторинг"):
        c1, c2, c3, c4 = st.columns(4)
        sleep = c1.slider("Сън /10", 1.0, 10.0, 4.0, 0.5)
        fatigue = c2.slider("Умора /10", 0.0, 10.0, 8.0, 0.5)
        pain = c3.slider("Болка /10", 0.0, 10.0, 2.0, 0.5)
        hr_delta = c4.slider("Сутрешен пулс · Δ", 0, 15, 6, 1)
        athlete_rows = scenario_bundle["wellness"].loc[scenario_bundle["wellness"]["athlete_id"] == athlete_id]
        baseline_hr = float(athlete_rows.sort_values("date").tail(40)["morning_hr"].mean())
        for offset in range(3):
            target_date = date.today() - timedelta(days=offset)
            mask = (scenario_bundle["wellness"]["athlete_id"] == athlete_id) & (pd.to_datetime(scenario_bundle["wellness"]["date"]).dt.date == target_date)
            scenario_bundle["wellness"].loc[mask, "sleep_quality"] = sleep
            scenario_bundle["wellness"].loc[mask, "fatigue"] = fatigue
            scenario_bundle["wellness"].loc[mask, "pain"] = pain
            scenario_bundle["wellness"].loc[mask, "morning_hr"] = baseline_hr + hr_delta
        scenario_description = f"Последните 3 дни: сън {sleep:.1f}, умора {fatigue:.1f}, болка {pain:.1f}, сутрешен пулс +{hr_delta}."
        focus_component = "Z4"
    elif scenario_type.startswith("Контролен"):
        test_code = st.selectbox("Тест", list(TEST_DEFINITIONS), format_func=lambda c: TEST_DEFINITIONS[c]["label"], key="scenario_test")
        subset = scenario_bundle["tests"].loc[(scenario_bundle["tests"]["athlete_id"] == athlete_id) & (scenario_bundle["tests"]["test_code"] == test_code)].sort_values("date")
        latest = subset.iloc[-1]
        c1, c2 = st.columns(2)
        change = c1.slider("Промяна на основния резултат · %", -12.0, 12.0, 4.0, 0.5)
        comparability = c2.slider("Сравнимост", 0.4, 1.0, 0.95, 0.05)
        direction = TEST_DEFINITIONS[test_code]["primary_direction"]
        new_value = float(latest["primary_value"]) * (1.0 + change / 100.0 * direction)
        mask = scenario_bundle["tests"]["test_id"] == latest["test_id"]
        scenario_bundle["tests"].loc[mask, "primary_value"] = new_value
        scenario_bundle["tests"].loc[mask, "comparability"] = comparability
        scenario_description = f"Последният тест е променен с посочно подобрение {change:+.1f}% и сравнимост {comparability:.2f}."
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
        weeks = st.slider("Седмици до основния старт", 6, 24, initial_weeks, 1)
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
                "name": "Симулационен основен старт",
                "start_date": pd.Timestamp(new_date),
                "end_date": pd.Timestamp(new_date),
                "priority": "A",
                "goal": "Пикова готовност",
                "locked": False,
                "note": "Добавен от симулатора.",
            }
            scenario_bundle["calendar"] = pd.concat(
                [scenario_bundle["calendar"], pd.DataFrame([new_row])], ignore_index=True
            )
        scenario_description = f"Основният старт е преместен/зададен на {new_date.strftime('%d.%m.%Y')} ({weeks} седмици)."
        focus_component = "Z4"

    scenario_bundle["version"] = int(bundle["version"]) + 1
    after = analyze_athlete(scenario_bundle, athlete_id, generate_plan=True)
    st.markdown(f'<div class="soft-box"><b>Активен сценарий:</b> {scenario_description}</div>', unsafe_allow_html=True)
    comparison_metrics(before, after, focus_component)
    st.subheader("Промяна на програмата")
    st.dataframe(plan_diff(before, after), width="stretch", hide_index=True)

    with st.expander("Техническо обяснение на сценария"):
        st.write(after["integrated"].loc[focus_component, "reason"])
        st.json(after["decision_snapshot"], expanded=False)

    if st.button("Приеми сценария като нова версия", disabled=not can_edit, type="primary", width="stretch"):
        scenario_bundle["audit_log"] = bundle.get("audit_log", [])
        commit_bundle(scenario_bundle, "scenario_accept", "Сценарият е приет: " + scenario_description, athlete_id)


def render_profile_page(bundle: dict[str, Any], analysis: dict[str, Any], can_edit: bool) -> None:
    athlete_id = str(analysis["athlete"]["athlete_id"])
    athlete_index = bundle["athletes"].index[bundle["athletes"]["athlete_id"] == athlete_id][0]
    athlete = bundle["athletes"].loc[athlete_index]
    page_header("Профил на спортиста", "Статични данни, индивидуални зони, компонентна база, поносимост и качество на данните.")

    tab_profile, tab_zones, tab_tolerance = st.tabs(["Основни данни", "Зони и тегла", "Профил на поносимост"])
    with tab_profile:
        with st.form("athlete_profile_form"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Име", value=str(athlete["name"]), disabled=not can_edit)
            category = c1.text_input("Категория", value=str(athlete["category"]), disabled=not can_edit)
            age = c2.number_input("Възраст", 14, 60, int(athlete["age"]), disabled=not can_edit)
            height = c2.number_input("Ръст · cm", 140, 220, int(athlete["height_cm"]), disabled=not can_edit)
            weight = c3.number_input("Маса · kg", 40.0, 130.0, float(athlete["weight_kg"]), 0.5, disabled=not can_edit)
            experience = c3.number_input("Спортен стаж · години", 0, 40, int(athlete["experience_years"]), disabled=not can_edit)
            availability = st.text_input("Наличност", value=str(athlete["availability"]), disabled=not can_edit)
            submitted = st.form_submit_button("Запази профила", disabled=not can_edit, width="stretch")
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
            commit_bundle(bundle, "athlete_profile_update", "Основният профил на спортиста е актуализиран.", athlete_id)

        quality = {
            "История на активности": len(analysis["activities"]),
            "Дни мониторинг": int((bundle["wellness"]["athlete_id"] == athlete_id).sum()),
            "Контролни тестове": int((bundle["tests"]["athlete_id"] == athlete_id).sum()),
            "Надеждност на 40-дневната база": float(analysis["load_stats"]["reliability"].mean() * 100),
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
                "component": "Зона",
                "hr_low": st.column_config.NumberColumn("Пулс · долна", min_value=50, max_value=230),
                "hr_high": st.column_config.NumberColumn("Пулс · горна", min_value=50, max_value=230),
                "weight_low": st.column_config.NumberColumn("Тегло · долно", min_value=1.0, help=help_text("real_equivalent")),
                "weight_high": st.column_config.NumberColumn("Тегло · горно", min_value=1.0, help=help_text("real_equivalent")),
                "power": st.column_config.NumberColumn("Степен p", min_value=0.2, max_value=4.0, step=0.05),
            },
        )
        if can_edit and st.button("Запази зоните и преизчисли", width="stretch"):
            valid = True
            if (edited["hr_low"] >= edited["hr_high"]).any() or (edited["weight_low"] <= 0).any() or (edited["weight_high"] < edited["weight_low"]).any():
                valid = False
                st.error("Проверете границите и теглата: долната граница трябва да е по-малка, а теглата — положителни и ненамаляващи.")
            if valid:
                for col in editable_cols:
                    zones[col] = edited[col].values
                zones["version"] = int(zones["version"].max()) + 1
                bundle["zone_profiles"][athlete_id] = zones
                commit_bundle(bundle, "zone_profile_update", "Индивидуалните зони и вътрешнозонови тегла са променени.", athlete_id)
        for i in range(len(edited) - 1):
            if abs(float(edited.iloc[i]["weight_high"]) - float(edited.iloc[i + 1]["weight_low"])) > 1e-6:
                st.warning("Има прекъсване между горното тегло на една зона и долното тегло на следващата. Това е допустимо за тест, но може да създаде изкуствен скок.")
                break

    with tab_tolerance:
        rows = []
        for component in COMPONENTS:
            rec = bundle["parameters"]["recovery"][component]
            rows.append(
                {
                    "Компонент": component,
                    "E40 / ден": analysis["load_stats"].loc[component, "E40_daily"],
                    "Tref": analysis["load_stats"].loc[component, "Tref"],
                    "Чувствителност s": rec["sensitivity"],
                    "τ · дни": rec["tau_days"],
                    "Товарна readiness": analysis["load_readiness"].loc[component, "readiness"],
                    "Мониторинг": analysis["monitoring_by_component"].loc[component, "monitoring_score"],
                    "Тестова корекция %": analysis["test_adjustments"].get(component, 0.0) * 100,
                    "Интегрирана готовност": analysis["integrated"].loc[component, "integrated_readiness"],
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_models_page() -> None:
    page_header("Модели и обяснения", "Всяка основна метрика в приложението има ❓ tooltip с кратко обяснение и линк към тази страница.")
    titles = explanation_titles()
    requested = str(st.query_params.get("topic", "seven_forty"))
    if requested not in EXPLANATIONS:
        requested = "seven_forty"
    topic = st.selectbox("Модел / индекс", list(EXPLANATIONS), index=list(EXPLANATIONS).index(requested), format_func=lambda key: titles[key], key="model_topic")
    if str(st.query_params.get("topic", "")) != topic:
        st.query_params["topic"] = topic

    item = EXPLANATIONS[topic]
    st.header(item["title"])
    st.markdown(item["body"])

    st.divider()
    st.subheader("Последователност на изчислителния pipeline")
    st.markdown(
        "1. Нормализиране на активността → 2. зониране на всяка секунда → 3. `T`, `k`, `Q` → "
        "4. каскада и разлив до `E` → 5. `E7`, `E40`, `B`, `7/40`, `Tref` → 6. умора и readiness → "
        "7. мониторинг → 8. контролни тестове → 9. интегрирана готовност → 10. периодизационна цел → "
        "11. адаптивен множител → 12. обратно решение `E → Q` → 13. разпределение по дни → "
        "14. избор и дозиране на метод → 15. проверка на ограниченията → 16. DecisionSnapshot."
    )
    st.info("Началните коефициенти са експертни параметри за MVP и валидиране. Те не са универсални норми и са видими в експертните настройки.")

    with st.expander("Всички понятия"):
        for key, value in EXPLANATIONS.items():
            st.markdown(f"### {value['title']}")
            st.write(value["short"])


def render_settings_page(bundle: dict[str, Any], role: str, athlete_id: str) -> None:
    page_header("Експертни настройки", "Начални параметри, възстановяване, каскада, база от методи и журнал на версиите.")
    can_edit = role in EXPERT_ROLES
    if not can_edit:
        st.info("Редакцията на експертните коефициенти е достъпна само за ролята „Главен треньор“. Данните по-долу са в режим преглед.")
    params = bundle["parameters"]
    tab_general, tab_components, tab_cascade, tab_methods, tab_audit = st.tabs(
        ["Общи правила", "Компонентни параметри", "Каскада", "Методи", "Журнал"]
    )

    with tab_general:
        with st.form("general_settings"):
            c1, c2, c3 = st.columns(3)
            spill_threshold = c1.slider("Праг за разлив · дял от Tref", 0.20, 0.90, float(params["spill_threshold_fraction"]), 0.05, disabled=not can_edit)
            spill_fraction = c1.slider("Разлив към по-висока зона", 0.0, 0.50, float(params["spill_fraction"]), 0.05, disabled=not can_edit)
            key_fraction = c2.slider("Праг за ключов стимул", 0.20, 0.80, float(params["key_stimulus_fraction"]), 0.05, disabled=not can_edit)
            key_readiness = c2.slider("Readiness за ключова сесия", 70.0, 100.0, float(params["key_readiness_threshold"]), 1.0, disabled=not can_edit)
            full_recovery = c3.slider("Практическо пълно възстановяване", 90.0, 99.0, float(params["practical_full_recovery"]), 1.0, disabled=not can_edit)
            current_weight = c3.slider("Тежест на текущия показател", 0.30, 0.90, float(params["current_metric_weight"]), 0.05, disabled=not can_edit)
            submit = st.form_submit_button("Запази общите правила", disabled=not can_edit, width="stretch")
        if submit:
            params["spill_threshold_fraction"] = spill_threshold
            params["spill_fraction"] = spill_fraction
            params["key_stimulus_fraction"] = key_fraction
            params["key_readiness_threshold"] = key_readiness
            params["practical_full_recovery"] = full_recovery
            params["current_metric_weight"] = current_weight
            commit_bundle(bundle, "parameter_update", "Общите алгоритмични параметри са актуализирани.", athlete_id)

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
                "component": "Компонент",
                "base_load": st.column_config.NumberColumn("Базов товар B0", min_value=0.1, help=help_text("seven_forty")),
                "sensitivity": st.column_config.NumberColumn("Чувствителност s", min_value=0.1, max_value=3.0, step=0.05),
                "tau_days": st.column_config.NumberColumn("τ · дни", min_value=0.2, max_value=5.0, step=0.05),
                "fmax": st.column_config.NumberColumn("F max", min_value=100.0, max_value=300.0, step=5.0),
            },
        )
        if st.button("Запази компонентните параметри", disabled=not can_edit, width="stretch"):
            for _, row in edited.iterrows():
                component = str(row["component"])
                params["base_loads"][component] = float(row["base_load"])
                params["recovery"][component] = {
                    "sensitivity": float(row["sensitivity"]),
                    "tau_days": float(row["tau_days"]),
                    "fmax": float(row["fmax"]),
                }
            commit_bundle(bundle, "component_parameter_update", "Базовите товари и възстановителните параметри са актуализирани.", athlete_id)

    with tab_cascade:
        cascade_df = pd.DataFrame(params["cascade"]).T.reindex(index=COMPONENTS, columns=COMPONENTS)
        cascade_df.index.name = "приемащ компонент"
        edited = st.data_editor(
            cascade_df,
            width="stretch",
            key=f"cascade_{bundle['version']}",
            disabled=not can_edit,
            column_config={c: st.column_config.NumberColumn(c, min_value=0.0, max_value=1.5, step=0.05) for c in COMPONENTS},
        )
        st.caption("Ред = приемащ компонент; колона = източник на директен товар. Диагоналът трябва да остане 1.0.")
        if st.button("Запази матрицата", disabled=not can_edit, width="stretch"):
            for component in COMPONENTS:
                edited.loc[component, component] = 1.0
            params["cascade"] = {
                receiver: {source: float(edited.loc[receiver, source]) for source in COMPONENTS} for receiver in COMPONENTS
            }
            commit_bundle(bundle, "cascade_update", "Матрицата на физиологичните взаимодействия е актуализирана.", athlete_id)

    with tab_methods:
        component = st.selectbox("Филтър по компонент", COMPONENTS, key="method_component")
        methods = bundle["methods"].loc[bundle["methods"]["component"] == component].copy().reset_index(drop=True)
        edited = st.data_editor(
            methods,
            width="stretch",
            hide_index=True,
            disabled=["method_code", "component"] if can_edit else list(methods.columns),
            key=f"methods_{component}_{bundle['version']}",
            num_rows="dynamic" if can_edit else "fixed",
        )
        if st.button("Запази методите за компонента", disabled=not can_edit, width="stretch"):
            other = bundle["methods"].loc[bundle["methods"]["component"] != component]
            bundle["methods"] = pd.concat([other, edited], ignore_index=True)
            commit_bundle(bundle, "methods_update", f"Базата от методи за {component} е актуализирана.", athlete_id)

    with tab_audit:
        audit = pd.DataFrame(bundle.get("audit_log", []))
        if audit.empty:
            st.info("Все още няма ръчни промени в текущата демо сесия.")
        else:
            st.dataframe(audit.sort_values("timestamp", ascending=False), width="stretch", hide_index=True)
        st.download_button(
            "Изтегли журнала · CSV",
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
