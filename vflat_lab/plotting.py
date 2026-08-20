"""Plotly figures for the Vflat calibration laboratory."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def activity_figure(timeseries: pd.DataFrame, segments: pd.DataFrame) -> go.Figure:
    display_segments = segments.sort_values(["lap", "elapsed_min"]).copy()
    figure = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.25, 0.52, 0.23],
        specs=[[{"secondary_y": False}], [{"secondary_y": False}], [{"secondary_y": True}]],
    )
    figure.add_trace(
        go.Scattergl(x=timeseries.elapsed_s / 60.0, y=timeseries.grade_pct, name="Наклон", line={"color": "#7C8799", "width": 1.2}),
        row=1,
        col=1,
    )
    figure.add_hline(y=0, line_width=1, line_color="#CBD5E1", row=1, col=1)

    for column, label, color, width in (
        ("speed_kmh", "Реална скорост", "#64748B", 1.2),
        ("vflat_stationary_kmh", "Стационарен Vflat", "#2563EB", 1.4),
        ("vflat_final_kmh", "Финален емпиричен Vflat", "#F97316", 2.0),
    ):
        for lap_index, (_, lap) in enumerate(display_segments.groupby("lap", sort=True)):
            figure.add_trace(
                go.Scattergl(
                    x=lap.elapsed_min,
                    y=lap[column].where(lap.segment_valid),
                    name=label,
                    legendgroup=column,
                    showlegend=lap_index == 0,
                    mode="lines+markers",
                    marker={"size": 3},
                    line={"color": color, "width": width},
                    connectgaps=False,
                ),
                row=2,
                col=1,
            )

    figure.add_trace(
        go.Scattergl(
            x=timeseries.elapsed_s / 60.0,
            y=timeseries.hr_bpm,
            name="Пулс",
            line={"color": "#DC2626", "width": 1.1},
        ),
        row=3,
        col=1,
        secondary_y=False,
    )
    figure.add_trace(
        go.Scattergl(
            x=timeseries.elapsed_s / 60.0,
            y=timeseries.post_descent_memory,
            name="Памет след спускане",
            line={"color": "#0EA5E9", "width": 1.0},
        ),
        row=3,
        col=1,
        secondary_y=True,
    )
    figure.add_trace(
        go.Scattergl(
            x=timeseries.elapsed_s / 60.0,
            y=timeseries.post_climb_memory,
            name="Памет след изкачване",
            line={"color": "#16A34A", "width": 1.0},
        ),
        row=3,
        col=1,
        secondary_y=True,
    )
    figure.update_yaxes(title_text="Наклон (%)", row=1, col=1)
    figure.update_yaxes(title_text="Скорост (km/h)", row=2, col=1)
    figure.update_yaxes(title_text="Пулс (bpm)", row=3, col=1, secondary_y=False)
    figure.update_yaxes(title_text="Памет (0–1)", range=[0, 1], row=3, col=1, secondary_y=True)
    figure.update_xaxes(title_text="Изминало време (min)", row=3, col=1)
    figure.update_layout(
        height=830,
        margin={"l": 40, "r": 35, "t": 35, "b": 40},
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.01, "xanchor": "left", "x": 0},
    )
    return figure


def grade_scatter_figure(segments: pd.DataFrame) -> go.Figure:
    valid = segments[segments.segment_valid].copy()
    figure = go.Figure()
    figure.add_trace(
        go.Scattergl(
            x=valid.grade_pct,
            y=valid.vflat_stationary_kmh,
            mode="markers",
            name="Стационарен",
            marker={"size": 7, "opacity": 0.55},
            customdata=valid[["elapsed_min", "speed_kmh"]],
            hovertemplate="Наклон %{x:.1f}%<br>Vflat %{y:.1f} km/h<br>Време %{customdata[0]:.1f} min<br>Реална %{customdata[1]:.1f} km/h<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scattergl(
            x=valid.grade_pct,
            y=valid.vflat_final_kmh,
            mode="markers",
            name="Финален емпиричен",
            marker={"size": 7, "opacity": 0.65},
        )
    )
    figure.update_layout(
        height=450,
        xaxis_title="Среден наклон на сегмента (%)",
        yaxis_title="Vflat (km/h)",
        hovermode="closest",
        margin={"l": 35, "r": 20, "t": 25, "b": 35},
    )
    return figure
