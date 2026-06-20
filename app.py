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
    effective_load_figure,
    index_7_40_figure,
    monitoring_history_figure,
    plan_comparison_figure,
    readiness_figure,
    real_vs_equivalent_figure,
    test_history_figure,
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
    page_title="Biathlon LoadLab · MVP",
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
    main_events = bundle["calendar"].loc[bundle["calendar"]["type"] == "MAIN_RACE"]
    days_to_main = int((pd.to_datetime(main_events["start_date"]).min().date() - date.today()).days)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Спортисти", len(summary))
    c2.metric("Средна готовност", f"{mean_readiness:.0f}/100", help=help_text("integrated_readiness"))
    c3.metric("Твърди флагове", flags, help=help_text("monitoring"))
    c4.metric("До основния старт", f"{days_to_main} дни", help=help_text("periodization"))

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
                st.session_state.athlete_id = athlete_id
                st.session_state.nav_page = "dashboard"
                st.query_params.from_dict({"page": "dashboard", "athlete": athlete_id})
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
    page_header("Адаптивна тренировъчна програма", "Седмична компонентна цел → директен товар → реално време → конкретен метод.")
    tab_wave, tab_week = st.tabs(["Дългосрочна динамика", "Следващи 7 дни"])

    with tab_wave:
        metric = st.radio(
            "Графика",
            ["target_effective_week", "target_index"],
            horizontal=True,
            format_func=lambda x: "Ефективен товар" if x == "target_effective_week" else "Целеви 7/40",
            key="weekly_chart_metric",
        )
        st.plotly_chart(weekly_targets_figure(analysis["weekly_targets"], metric), width="stretch")
        events = bundle["calendar"].loc[bundle["calendar"]["athlete_id"] == str(analysis["athlete"]["athlete_id"])].copy()
        st.dataframe(events[["type", "name", "start_date", "end_date", "priority", "goal"]], width="stretch", hide_index=True)

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

        editable = analysis["plan"][["date", "day", "focus", "method", "total_real_min", "status", "locked", "coach_note"]].copy()
        edited = st.data_editor(
            editable,
            width="stretch",
            hide_index=True,
            key=f"plan_editor_{analysis['athlete']['athlete_id']}_{bundle['version']}",
            disabled=["date", "day", "focus", "method"] if can_edit else list(editable.columns),
            column_config={
                "date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"),
                "day": "Ден",
                "focus": "Фокус",
                "method": "Метод",
                "total_real_min": st.column_config.NumberColumn("Общо реални минути", min_value=0.0, max_value=300.0, step=5.0),
                "status": st.column_config.SelectboxColumn("Статус", options=["Предложена", "Одобрена", "Отхвърлена"]),
                "locked": st.column_config.CheckboxColumn("Заключена"),
                "coach_note": st.column_config.TextColumn("Бележка на треньора"),
            },
        )
        b1, b2, b3 = st.columns([1, 1, 2])
        if b1.button("Запази редакциите", disabled=not can_edit, width="stretch"):
            for _, row in edited.iterrows():
                key = f"{analysis['athlete']['athlete_id']}|{pd.Timestamp(row['date']).date().isoformat()}"
                bundle["plan_overrides"][key] = {
                    "status": row["status"],
                    "locked": bool(row["locked"]),
                    "coach_note": str(row["coach_note"]),
                    "total_real_min": float(row["total_real_min"]),
                }
            commit_bundle(bundle, "plan_edit", "Редакциите на седмичната програма са записани като нова версия.", str(analysis["athlete"]["athlete_id"]))
        if b2.button("Одобри всички дни", disabled=not can_edit, width="stretch"):
            for _, row in analysis["plan"].iterrows():
                key = f"{analysis['athlete']['athlete_id']}|{pd.Timestamp(row['date']).date().isoformat()}"
                current = bundle["plan_overrides"].get(key, {})
                current.update({"status": "Одобрена", "locked": bool(current.get("locked", False)), "coach_note": current.get("coach_note", "")})
                bundle["plan_overrides"][key] = current
            commit_bundle(bundle, "plan_approve", "Всички дни от текущата програма са одобрени.", str(analysis["athlete"]["athlete_id"]))
        b3.caption("Заключените/одобрени записи се пазят като треньорски override и се прилагат върху следващото преизчисляване.")

        d1, d2 = st.columns(2)
        d1.download_button(
            "Изтегли програмата · CSV",
            dataframe_csv_bytes(analysis["plan"]),
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

        st.subheader("Описание по дни")
        for _, row in analysis["plan"].iterrows():
            with st.expander(f"{pd.Timestamp(row['date']).strftime('%d.%m')} · {row['day']} · {row['focus']} · {row['method']}"):
                st.write(row["description"])
                st.markdown(f"**Обяснение на решението:** {row['explanation']}")
                load_cols = [f"real_{c}" for c in COMPONENTS] + [f"q_{c}" for c in COMPONENTS]
                details = pd.DataFrame(
                    {
                        "Компонент": COMPONENTS,
                        "Реални минути": [row[f"real_{c}"] for c in COMPONENTS],
                        "Директно Q": [row[f"q_{c}"] for c in COMPONENTS],
                        "Ефективно E": [row[f"e_{c}"] for c in COMPONENTS],
                    }
                )
                st.dataframe(details, width="stretch", hide_index=True)


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
    left = before["plan"][["date", "focus", "method", "total_real_min"]].rename(
        columns={"focus": "Фокус · преди", "method": "Метод · преди", "total_real_min": "Минути · преди"}
    )
    right = after["plan"][["date", "focus", "method", "total_real_min"]].rename(
        columns={"focus": "Фокус · след", "method": "Метод · след", "total_real_min": "Минути · след"}
    )
    merged = left.merge(right, on="date", how="outer")
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
        main_mask = (scenario_bundle["calendar"]["athlete_id"] == athlete_id) & (scenario_bundle["calendar"]["type"] == "MAIN_RACE")
        current_date = pd.Timestamp(scenario_bundle["calendar"].loc[main_mask, "start_date"].iloc[0]).date()
        weeks = st.slider("Седмици до основния старт", 6, 24, max(6, int(round((current_date - date.today()).days / 7))), 1)
        new_date = date.today() + timedelta(weeks=weeks)
        scenario_bundle["calendar"].loc[main_mask, "start_date"] = pd.Timestamp(new_date)
        scenario_bundle["calendar"].loc[main_mask, "end_date"] = pd.Timestamp(new_date)
        scenario_description = f"Основният старт е преместен на {new_date.strftime('%d.%m.%Y')} ({weeks} седмици)."
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
    elif page == "monitoring":
        render_monitoring_page(bundle, analysis, can_edit)
    elif page == "tests":
        render_tests_page(bundle, analysis, can_edit)
    elif page == "simulator":
        render_simulator_page(bundle, analysis, can_edit)
    elif page == "profile":
        render_profile_page(bundle, analysis, can_edit)
