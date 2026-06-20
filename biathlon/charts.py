"""Plotly графики за демонстрационния интерфейс."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .constants import COMPONENT_LABELS, COMPONENTS, METRIC_DEFINITIONS, TEST_DEFINITIONS
from .preferences import EVENT_TYPE_LABELS


def index_7_40_figure(rolling_load: pd.DataFrame, component: str) -> go.Figure:
    data = rolling_load.loc[rolling_load["component"] == component].copy()
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["index_7_40"],
            mode="lines",
            name="7/40",
            hovertemplate="%{x|%d.%m.%Y}<br>7/40=%{y:.2f}<extra></extra>",
        )
    )
    for value, label in [(0.80, "разтоварване"), (1.00, "поддържане"), (1.15, "силно изграждане"), (1.30, "проверка")]:
        fig.add_hline(y=value, line_dash="dot", annotation_text=label, annotation_position="top left")
    fig.update_layout(
        title=f"Индекс 7/40 · {COMPONENT_LABELS[component]}",
        xaxis_title="Дата",
        yaxis_title="Индекс",
        yaxis_range=[0.55, max(1.45, float(data["index_7_40"].max()) + 0.1 if not data.empty else 1.45)],
        hovermode="x unified",
        height=390,
        margin=dict(l=20, r=20, t=55, b=25),
    )
    return fig


def effective_load_figure(rolling_load: pd.DataFrame, component: str, days: int = 60) -> go.Figure:
    data = rolling_load.loc[rolling_load["component"] == component].tail(days).copy()
    fig = go.Figure()
    fig.add_trace(go.Bar(x=data["date"], y=data["effective"], name="Дневен E"))
    fig.add_trace(go.Scatter(x=data["date"], y=data["E7_daily"], mode="lines", name="E7 средно/ден"))
    fig.add_trace(go.Scatter(x=data["date"], y=data["E40_daily"], mode="lines", name="E40 средно/ден"))
    fig.update_layout(
        title=f"Ефективен товар · {COMPONENT_LABELS[component]}",
        xaxis_title="Дата",
        yaxis_title="Еквивалентни минути",
        barmode="overlay",
        hovermode="x unified",
        height=390,
        margin=dict(l=20, r=20, t=55, b=25),
    )
    return fig


def readiness_figure(readiness_history: pd.DataFrame, components: list[str] | None = None, days: int = 45) -> go.Figure:
    components = components or COMPONENTS
    data = readiness_history.loc[readiness_history["component"].isin(components)].copy()
    if not data.empty:
        cutoff = pd.to_datetime(data["date"]).max() - pd.Timedelta(days=days - 1)
        data = data.loc[pd.to_datetime(data["date"]) >= cutoff]
    fig = px.line(
        data,
        x="date",
        y="readiness_after",
        color="component",
        labels={"date": "Дата", "readiness_after": "Readiness %", "component": "Компонент"},
        title="Компонентна readiness и възстановяване",
    )
    fig.add_hline(y=90, line_dash="dot", annotation_text="ключова сесия")
    fig.add_hline(y=65, line_dash="dot", annotation_text="възстановяване")
    fig.update_layout(height=420, hovermode="x unified", margin=dict(l=20, r=20, t=55, b=25), yaxis_range=[0, 105])
    return fig


def weekly_targets_figure(weekly_targets: pd.DataFrame, metric: str = "target_effective_week") -> go.Figure:
    labels = {
        "target_effective_week": "Целеви ефективен седмичен товар",
        "target_index": "Целеви 7/40",
    }
    fig = px.line(
        weekly_targets,
        x="week_start",
        y=metric,
        color="component",
        markers=True,
        hover_data=["phase", "status", "events", "weeks_to_main_race"],
        labels={"week_start": "Начало на седмицата", metric: labels[metric], "component": "Компонент"},
        title=f"Вълнообразна динамика · {labels[metric]}",
    )
    fig.update_layout(height=470, hovermode="x unified", margin=dict(l=20, r=20, t=55, b=25))
    return fig


def real_vs_equivalent_figure(activity_summary: pd.Series) -> go.Figure:
    rows = []
    for component in COMPONENTS:
        rows.append({"component": component, "Тип": "Реално време", "Минути": float(activity_summary.get(f"real_{component}", 0.0))})
        rows.append({"component": component, "Тип": "Директно Q", "Минути": float(activity_summary.get(f"q_{component}", 0.0))})
    data = pd.DataFrame(rows)
    fig = px.bar(
        data,
        x="component",
        y="Минути",
        color="Тип",
        barmode="group",
        title="Реално срещу физиологично еквивалентно време",
        labels={"component": "Компонент"},
    )
    fig.update_layout(height=390, margin=dict(l=20, r=20, t=55, b=25))
    return fig


def activity_stream_figure(stream: pd.DataFrame, zone_profile: pd.DataFrame) -> go.Figure:
    display = stream.iloc[:: max(1, len(stream) // 1800)].copy() if len(stream) > 1800 else stream.copy()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=display["offset_sec"] / 60.0, y=display["hr"], mode="lines", name="Пулс"))
    for _, zone in zone_profile.sort_values("hr_low").iterrows():
        fig.add_hrect(
            y0=float(zone["hr_low"]),
            y1=float(zone["hr_high"]),
            opacity=0.08,
            line_width=0,
            annotation_text=str(zone["component"]),
            annotation_position="top left",
        )
    fig.update_layout(
        title="1-секунден тестов пулсов профил",
        xaxis_title="Минута",
        yaxis_title="Пулс (уд./мин)",
        height=390,
        margin=dict(l=20, r=20, t=55, b=25),
    )
    return fig


def monitoring_history_figure(wellness: pd.DataFrame, athlete_id: str, metric: str, days: int = 60) -> go.Figure:
    definition = METRIC_DEFINITIONS[metric]
    data = wellness.loc[wellness["athlete_id"] == athlete_id, ["date", metric]].copy().sort_values("date").tail(days)
    data["mean7"] = data[metric].rolling(7, min_periods=1).mean()
    data["mean40"] = data[metric].rolling(40, min_periods=1).mean()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=data["date"], y=data[metric], mode="lines+markers", name="Реална стойност"))
    fig.add_trace(go.Scatter(x=data["date"], y=data["mean7"], mode="lines", name="7 дни"))
    fig.add_trace(go.Scatter(x=data["date"], y=data["mean40"], mode="lines", name="40 дни"))
    fig.update_layout(
        title=f"{definition['label']} · реална стойност и тенденции",
        xaxis_title="Дата",
        yaxis_title=definition["unit"],
        height=390,
        hovermode="x unified",
        margin=dict(l=20, r=20, t=55, b=25),
    )
    return fig


def test_history_figure(tests: pd.DataFrame, athlete_id: str, test_code: str) -> go.Figure:
    definition = TEST_DEFINITIONS[test_code]
    data = tests.loc[(tests["athlete_id"] == athlete_id) & (tests["test_code"] == test_code)].copy().sort_values("date")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["primary_value"],
            mode="lines+markers",
            name=f"{definition['primary_label']} ({definition['primary_unit']})",
        )
    )
    fig.update_layout(
        title=definition["label"],
        xaxis_title="Дата",
        yaxis_title=f"{definition['primary_label']} · {definition['primary_unit']}",
        height=390,
        margin=dict(l=20, r=20, t=55, b=25),
    )
    return fig


def plan_comparison_figure(comparison: pd.DataFrame) -> go.Figure:
    data = comparison.reset_index().melt(
        id_vars="component",
        value_vars=["target_effective", "planned_effective"],
        var_name="Тип",
        value_name="Еквивалентни минути",
    )
    data["Тип"] = data["Тип"].map({"target_effective": "Цел", "planned_effective": "План"})
    fig = px.bar(
        data,
        x="component",
        y="Еквивалентни минути",
        color="Тип",
        barmode="group",
        title="Целеви срещу планиран ефективен товар",
    )
    fig.update_layout(height=390, margin=dict(l=20, r=20, t=55, b=25))
    return fig


def calendar_timeline_figure(calendar: pd.DataFrame) -> go.Figure:
    """Компактна хоризонтална времева линия за стартове, лагери и тестове."""

    data = calendar.copy()
    fig = go.Figure()
    if data.empty:
        fig.update_layout(
            title="Календар на подготовката",
            height=300,
            annotations=[dict(text="Няма въведени събития", x=0.5, y=0.5, showarrow=False)],
        )
        return fig
    data["start_date"] = pd.to_datetime(data["start_date"]).dt.normalize()
    data["end_date"] = pd.to_datetime(data["end_date"]).dt.normalize()
    data["duration_days"] = (data["end_date"] - data["start_date"]).dt.days.clip(lower=0) + 1
    for event_type, subset in data.groupby("type", sort=False):
        fig.add_trace(
            go.Bar(
                y=subset["name"],
                x=subset["duration_days"] * 24 * 60 * 60 * 1000,
                base=subset["start_date"],
                orientation="h",
                name=EVENT_TYPE_LABELS.get(str(event_type), str(event_type)),
                customdata=subset[["start_date", "end_date", "priority", "goal"]],
                hovertemplate=(
                    "%{y}<br>%{customdata[0]|%d.%m.%Y} – %{customdata[1]|%d.%m.%Y}"
                    "<br>Приоритет: %{customdata[2]}<br>Цел: %{customdata[3]}<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        title="Календар на подготовката",
        xaxis_title="Дата",
        yaxis_title="",
        barmode="overlay",
        height=max(330, 55 * len(data) + 120),
        margin=dict(l=20, r=20, t=55, b=25),
        legend_title="Тип",
    )
    return fig


def weekly_plan_vs_actual_figure(trajectory: pd.DataFrame) -> go.Figure:
    """Показва отделно реалния исторически и бъдещия планиран седмичен обем."""

    data = trajectory.copy()
    fig = go.Figure()
    if data.empty:
        fig.update_layout(
            title="Реален срещу планиран седмичен обем",
            height=390,
            annotations=[dict(text="Няма налични данни", x=0.5, y=0.5, showarrow=False)],
        )
        return fig

    data["date"] = pd.to_datetime(data["date"])
    actual = data.loc[data["series_type"] == "actual"].sort_values("date")
    plan = data.loc[data["series_type"] == "plan"].sort_values("date")
    anchor = data.loc[data["series_type"] == "anchor"].sort_values("date")

    if not actual.empty:
        fig.add_trace(
            go.Scatter(
                x=actual["date"],
                y=actual["actual_weekly_hours"],
                mode="lines+markers",
                name="Реално изпълнено",
                hovertemplate="%{x|%d.%m.%Y}<br>Реално: %{y:.1f} h<extra></extra>",
            )
        )
    if not plan.empty:
        fig.add_trace(
            go.Scatter(
                x=plan["date"],
                y=plan["planned_weekly_hours"],
                mode="lines+markers",
                name="Адаптивен план",
                hovertemplate="%{x|%d.%m.%Y}<br>План: %{y:.1f} h<extra></extra>",
            )
        )

    required = float(data["required_weekly_hours"].dropna().iloc[-1]) if data["required_weekly_hours"].notna().any() else 0.0
    target_average = float(data["target_average_weekly_hours"].dropna().iloc[-1]) if data["target_average_weekly_hours"].notna().any() else 0.0
    if required > 0:
        fig.add_hline(y=required, line_dash="dash", annotation_text=f"Нужно до края: {required:.1f} h/седм.")
    if target_average > 0 and abs(target_average - required) > 0.2:
        fig.add_hline(y=target_average, line_dash="dot", annotation_text=f"Средна сезонна цел: {target_average:.1f} h/седм.")
    if not anchor.empty:
        today_marker = pd.Timestamp(anchor["date"].iloc[-1]).to_pydatetime()
        fig.add_vline(x=today_marker, line_dash="dot")
        fig.add_annotation(x=today_marker, y=1.0, yref="paper", text="Днес", showarrow=False, yshift=10)

    fig.update_layout(
        title="Реален срещу планиран седмичен обем",
        xaxis_title="Край на 7-дневния прозорец",
        yaxis_title="Реални часове",
        height=420,
        hovermode="x unified",
        margin=dict(l=20, r=20, t=55, b=25),
    )
    return fig


def annual_goal_figure(context: dict, trajectory: pd.DataFrame | None = None) -> go.Figure:
    """Показва натрупан реален обем, адаптивен план и линейна сезонна цел."""

    data = trajectory.copy() if trajectory is not None else pd.DataFrame()
    if data.empty:
        completed = float(context.get("completed_hours", 0.0))
        expected = float(context.get("expected_hours_to_date", 0.0))
        target = float(context.get("target_hours", 0.0))
        fig = go.Figure()
        fig.add_trace(go.Bar(x=["Към днешна дата"], y=[completed], name="Изпълнено"))
        fig.add_trace(go.Bar(x=["Към днешна дата"], y=[max(0.0, expected - completed)], name="Разлика до линейната цел"))
        fig.add_hline(y=expected, line_dash="dot", annotation_text=f"Очаквано: {expected:.0f} h")
        fig.add_hline(y=target, line_dash="dash", annotation_text=f"Сезонна цел: {target:.0f} h")
        fig.update_layout(
            title="Прогрес към сезонната обемна цел",
            yaxis_title="Часове",
            barmode="stack",
            height=390,
            margin=dict(l=20, r=20, t=55, b=25),
        )
        return fig

    data["date"] = pd.to_datetime(data["date"])
    actual = data.loc[data["series_type"].isin(["actual", "anchor"])].sort_values("date")
    plan = data.loc[data["series_type"].isin(["anchor", "plan"])].sort_values("date")
    season_start = pd.Timestamp(context["season_start"])
    season_end = pd.Timestamp(context["season_end"])
    target_hours = float(context.get("target_hours", 0.0))

    fig = go.Figure()
    if not actual.empty:
        fig.add_trace(
            go.Scatter(
                x=actual["date"],
                y=actual["actual_cumulative_hours"],
                mode="lines+markers",
                name="Реално натрупано",
                connectgaps=False,
                hovertemplate="%{x|%d.%m.%Y}<br>Реално: %{y:.1f} h<extra></extra>",
            )
        )
    if not plan.empty:
        fig.add_trace(
            go.Scatter(
                x=plan["date"],
                y=plan["planned_cumulative_hours"],
                mode="lines+markers",
                name="Натрупано при текущия план",
                connectgaps=False,
                hovertemplate="%{x|%d.%m.%Y}<br>Реално + план: %{y:.1f} h<extra></extra>",
            )
        )
    fig.add_trace(
        go.Scatter(
            x=[season_start, season_end],
            y=[0.0, target_hours],
            mode="lines",
            line=dict(dash="dash"),
            name="Линейна целева траектория",
            hovertemplate="%{x|%d.%m.%Y}<br>Целева траектория: %{y:.1f} h<extra></extra>",
        )
    )
    anchor = data.loc[data["series_type"] == "anchor"]
    if not anchor.empty:
        today_marker = pd.Timestamp(anchor["date"].iloc[-1]).to_pydatetime()
        fig.add_vline(x=today_marker, line_dash="dot")
        fig.add_annotation(x=today_marker, y=1.0, yref="paper", text="Днес", showarrow=False, yshift=10)
    fig.update_layout(
        title="Натрупан обем: реално, план и сезонна цел",
        xaxis_title="Дата",
        yaxis_title="Часове",
        height=430,
        hovermode="x unified",
        margin=dict(l=20, r=20, t=55, b=25),
    )
    return fig

