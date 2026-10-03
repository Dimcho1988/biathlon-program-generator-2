"""Fast, display-only Plotly preparation for the standalone HRmod Lab.

This module consumes completed HRmod result frames.  It never calls the core,
changes HR values, classifies zones, or participates in the input hash.  Wave
overlays are batched into a constant number of traces so rendering cost is not
dominated by thousands of individual Plotly layout shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .schemas import AthleteHRProfile


ZONE_COLORS = (
    "rgba(78,121,167,0.08)",
    "rgba(89,161,79,0.08)",
    "rgba(242,207,74,0.09)",
    "rgba(242,142,43,0.08)",
    "rgba(225,87,89,0.08)",
)


@dataclass(frozen=True, slots=True)
class PlotViewData:
    """Frames and axis range required for exactly one rendered plot view."""

    timeseries: pd.DataFrame
    waves: pd.DataFrame
    x_column: str | None
    x_range: tuple[Any, Any] | None


def _first_column(frame: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    return next((name for name in candidates if name in frame.columns), None)


def _wave_axis_value(row: pd.Series, names: Sequence[str], x_column: str) -> Any:
    for name in names:
        if name not in row.index or pd.isna(row[name]):
            continue
        value = row[name]
        if x_column == "timestamp":
            return pd.to_datetime(value, errors="coerce", utc=True)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _selected_wave_range(
    timeseries: pd.DataFrame,
    waves: pd.DataFrame,
    selected_wave_id: int | None,
    x_column: str,
) -> tuple[Any, Any] | None:
    if selected_wave_id is None or waves.empty or "wave_id" not in waves.columns:
        return None
    selected = waves.loc[
        pd.to_numeric(waves["wave_id"], errors="coerce").eq(selected_wave_id)
    ]
    if selected.empty:
        return None
    row = selected.iloc[0]
    start = _wave_axis_value(
        row, ("rise_start_timestamp", "rise_start_elapsed_s"), x_column
    )
    end = _wave_axis_value(
        row, ("tail_end_timestamp", "tail_end_elapsed_s"), x_column
    )
    if start is None or end is None or pd.isna(start) or pd.isna(end):
        if "wave_id" not in timeseries.columns:
            return None
        wave_ids = pd.to_numeric(timeseries["wave_id"], errors="coerce")
        wave_x = timeseries.loc[wave_ids.eq(selected_wave_id), x_column]
        if wave_x.empty:
            return None
        start, end = wave_x.iloc[0], wave_x.iloc[-1]
    if x_column == "timestamp":
        start = pd.to_datetime(start, utc=True)
        end = pd.to_datetime(end, utc=True)
        padding = pd.Timedelta(
            max(5.0, float((end - start).total_seconds()) * 0.08), unit="s"
        )
    else:
        start, end = float(start), float(end)
        padding = max(5.0, (end - start) * 0.08)
    return start - padding, end + padding


def _flag_mask(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        values = frame[column]
        if values.dtype == bool:
            return values.fillna(False)
        return values.astype(str).str.lower().isin(("true", "1", "yes"))
    if "wave_state" in frame.columns:
        state = frame["wave_state"].astype(str).str.lower()
        target = "receiver" if column == "receiver_flag" else "donor"
        return state.eq(target)
    return pd.Series(False, index=frame.index)


def _value_mask(
    frame: pd.DataFrame,
    column: str,
    accepted_values: Sequence[str],
) -> pd.Series:
    """Return a tolerant display-only mask for terrain status columns."""

    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(False)
    accepted = {str(value).strip().casefold() for value in accepted_values}
    return values.astype(str).str.strip().str.casefold().isin(accepted)


def prepare_plot_view(
    timeseries: pd.DataFrame,
    waves: pd.DataFrame,
    *,
    selected_wave_id: int | None = None,
) -> PlotViewData:
    """Return full overview data or a small selected-wave display slice.

    The returned frames are display-only shallow/copy-on-write views.  For a
    wave zoom, only points inside the selected wave plus a small time padding
    are retained, and receiver/donor flags from neighbouring waves are hidden.
    """

    x_column = _first_column(timeseries, ("timestamp", "elapsed_s"))
    if x_column is None:
        return PlotViewData(
            timeseries=timeseries.iloc[0:0].copy(),
            waves=waves.iloc[0:0].copy(),
            x_column=None,
            x_range=None,
        )
    if selected_wave_id is None:
        return PlotViewData(
            timeseries=timeseries,
            waves=waves,
            x_column=x_column,
            x_range=None,
        )

    visible_waves = (
        waves.loc[
            pd.to_numeric(waves["wave_id"], errors="coerce").eq(selected_wave_id)
        ].copy()
        if "wave_id" in waves.columns
        else waves.iloc[0:0].copy()
    )
    selected_range = _selected_wave_range(
        timeseries, waves, selected_wave_id, x_column
    )
    if selected_range is None:
        return PlotViewData(
            timeseries=timeseries.iloc[0:0].copy(),
            waves=visible_waves,
            x_column=x_column,
            x_range=None,
        )

    axis_values = timeseries[x_column]
    if x_column == "timestamp":
        axis_values = pd.to_datetime(axis_values, errors="coerce", utc=True)
    else:
        axis_values = pd.to_numeric(axis_values, errors="coerce")
    start, end = selected_range
    display = timeseries.loc[axis_values.between(start, end, inclusive="both")].copy()
    if "wave_id" in display.columns:
        selected_rows = pd.to_numeric(
            display["wave_id"], errors="coerce"
        ).eq(selected_wave_id)
        for flag_name in ("receiver_flag", "donor_flag"):
            display[flag_name] = _flag_mask(display, flag_name) & selected_rows
    display.reset_index(drop=True, inplace=True)
    return PlotViewData(
        timeseries=display,
        waves=visible_waves,
        x_column=x_column,
        x_range=selected_range,
    )


def _contiguous_flag_ranges(
    frame: pd.DataFrame, x_values: pd.Series, column: str
) -> list[tuple[Any, Any]]:
    mask = _flag_mask(frame, column).reset_index(drop=True).to_numpy(dtype=bool)
    if mask.size == 0 or not mask.any():
        return []
    starts = np.flatnonzero(mask & np.r_[True, ~mask[:-1]])
    ends = np.flatnonzero(mask & np.r_[~mask[1:], True])
    ranges: list[tuple[Any, Any]] = []
    dt_values = pd.to_numeric(
        frame.get("dt_s", pd.Series(1.0, index=frame.index)), errors="coerce"
    ).reset_index(drop=True)
    for start_index, end_index in zip(starts, ends, strict=True):
        x0 = x_values.iloc[int(start_index)]
        x1 = x_values.iloc[int(end_index)]
        if start_index == end_index:
            dt = dt_values.iloc[int(end_index)]
            seconds = 1.0 if pd.isna(dt) or float(dt) <= 0.0 else float(dt)
            x1 = (
                x1 + pd.Timedelta(seconds=seconds)
                if isinstance(x1, pd.Timestamp)
                else float(x1) + seconds
            )
        ranges.append((x0, x1))
    return ranges


def _add_grouped_flag_trace(
    figure: go.Figure,
    frame: pd.DataFrame,
    x_values: pd.Series,
    column: str,
    *,
    y0: float,
    y1: float,
    name: str,
    fillcolor: str,
) -> None:
    xs: list[Any] = []
    ys: list[float | None] = []
    for start, end in _contiguous_flag_ranges(frame, x_values, column):
        xs.extend((start, start, end, end, start, None))
        ys.extend((y0, y1, y1, y0, y0, None))
    figure.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            name=name,
            line={"width": 0},
            fill="toself",
            fillcolor=fillcolor,
            hoverinfo="skip",
            connectgaps=False,
        ),
        row=1,
        col=1,
    )


def _add_grouped_ranges_trace(
    figure: go.Figure,
    ranges: Sequence[tuple[Any, Any]],
    *,
    y0: float,
    y1: float,
    name: str,
    fillcolor: str,
    linecolor: str = "rgba(0,0,0,0)",
) -> None:
    """Draw any number of intervals as one SVG trace with ``None`` gaps."""

    xs: list[Any] = []
    ys: list[float | None] = []
    for start, end in ranges:
        if start is None or end is None or pd.isna(start) or pd.isna(end):
            continue
        xs.extend((start, start, end, end, start, None))
        ys.extend((y0, y1, y1, y0, y0, None))
    figure.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            name=name,
            line={"width": 1.0, "color": linecolor},
            fill="toself",
            fillcolor=fillcolor,
            hoverinfo="skip",
            connectgaps=False,
        ),
        row=1,
        col=1,
    )


def _terrain_rejected_ranges(
    waves: pd.DataFrame,
    x_column: str,
) -> list[tuple[Any, Any]]:
    """Return ranges of terrain-adjusted/confounded waves without shapes."""

    if waves.empty:
        return []
    if "terrain_rejected" in waves.columns:
        rejected = _value_mask(waves, "terrain_rejected", ("true", "1", "yes"))
    elif "terrain_status" in waves.columns:
        rejected = _value_mask(
            waves,
            "terrain_status",
            (
                "terrain_adjusted",
                "terrain_confounded",
                "rejected",
                "terrain_rejected",
            ),
        )
    else:
        return []
    ranges: list[tuple[Any, Any]] = []
    for _, row in waves.loc[rejected].iterrows():
        start = _wave_axis_value(
            row, ("rise_start_timestamp", "rise_start_elapsed_s"), x_column
        )
        end = _wave_axis_value(
            row, ("tail_end_timestamp", "tail_end_elapsed_s"), x_column
        )
        if start is not None and end is not None:
            ranges.append((start, end))
    return ranges


def _add_grouped_wave_guides(
    figure: go.Figure,
    waves: pd.DataFrame,
    x_column: str,
    *,
    y0: float,
    y1: float,
) -> None:
    marker_specs = (
        (
            ("rise_start_timestamp", "rise_start_elapsed_s"),
            "s",
            "rise start",
            "#15803d",
            "dash",
        ),
        (("peak_timestamp", "peak_elapsed_s"), "p", "peak", "#7e22ce", "dot"),
        (
            ("tail_end_timestamp", "tail_end_elapsed_s"),
            "e",
            "wave end",
            "#c2410c",
            "dash",
        ),
    )
    annotate = len(waves) == 1
    for names, marker, label, color, dash in marker_specs:
        xs: list[Any] = []
        ys: list[float | None] = []
        texts: list[str | None] = []
        customdata: list[list[Any]] = []
        for _, row in waves.iterrows():
            x_value = _wave_axis_value(row, names, x_column)
            if x_value is None or pd.isna(x_value):
                continue
            xs.extend((x_value, x_value, None))
            ys.extend((y0, y1, None))
            texts.extend((None, f"<b>{marker}</b>" if annotate else None, None))
            if annotate:
                details = [
                    row.get("wave_id"),
                    row.get("morphology", "mirror_wave"),
                    row.get("morphology_reason") or "not_applicable",
                    row.get("correction_strategy", "v4_mirror_full_rise"),
                ]
                customdata.extend((details, details, [None, None, None, None]))
        if not xs:
            continue
        trace_options: dict[str, Any] = {
            "x": xs,
            "y": ys,
            "text": texts,
            "textposition": "top center",
            "mode": "lines+text" if annotate else "lines",
            "name": f"{marker} · {label}",
            "line": {"color": color, "width": 1.2, "dash": dash},
            "connectgaps": False,
        }
        if annotate:
            trace_options["customdata"] = customdata
            trace_options["hovertemplate"] = (
                f"<b>{marker} · {label}</b><br>"
                "Wave %{customdata[0]}<br>"
                "Morphology: %{customdata[1]}<br>"
                "Reason: %{customdata[2]}<br>"
                "Strategy: %{customdata[3]}<extra></extra>"
            )
        else:
            trace_options["hoverinfo"] = "skip"
        figure.add_trace(
            go.Scatter(**trace_options),
            row=1,
            col=1,
        )

    baseline_x: list[Any] = []
    baseline_y: list[float | None] = []
    for _, row in waves.iterrows():
        start = _wave_axis_value(
            row, ("rise_start_timestamp", "rise_start_elapsed_s"), x_column
        )
        end = _wave_axis_value(
            row, ("tail_end_timestamp", "tail_end_elapsed_s"), x_column
        )
        baseline = row.get("baseline_hr_bpm")
        if start is None or end is None or pd.isna(baseline):
            continue
        baseline_x.extend((start, end, None))
        baseline_y.extend((float(baseline), float(baseline), None))
    figure.add_trace(
        go.Scatter(
            x=baseline_x,
            y=baseline_y,
            mode="lines",
            name="B · local baseline",
            line={"color": "#475569", "width": 1.2, "dash": "dashdot"},
            hoverinfo="skip",
            connectgaps=False,
        ),
        row=1,
        col=1,
    )

def build_hr_only_figure(
    timeseries: pd.DataFrame,
    waves: pd.DataFrame,
    profile: AthleteHRProfile,
    *,
    show_h_detect: bool,
    use_webgl: bool = False,
    selected_wave_id: int | None = None,
) -> go.Figure:
    """Build one fast overview or one sliced wave-zoom figure.

    SVG traces are the compatibility-safe default.  WebGL remains an explicit
    acceleration option for browsers that advertise a working WebGL context.
    """

    view = prepare_plot_view(
        timeseries, waves, selected_wave_id=selected_wave_id
    )
    if view.x_column is None:
        return go.Figure()
    frame = view.timeseries
    x_column = view.x_column
    x_values = frame[x_column].copy()
    if x_column == "timestamp":
        x_values = pd.to_datetime(x_values, errors="coerce", utc=True)

    terrain_mode = any(
        column in frame.columns
        for column in (
            "hrmod_candidate_bpm",
            "hrmod_candidate",
            "hrmod_final_bpm",
            "hrmod_final",
            "smoothed_grade_pct",
            "downhill_mask",
            "terrain_status",
        )
    ) or any(
        column in view.waves.columns
        for column in ("terrain_status", "terrain_rejected")
    )
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=(0.72, 0.28),
        subplot_titles=(
            "raw HR срещу HRmod candidate/final"
            if terrain_mode
            else "raw/clean HR срещу hrmod",
            "Добавена/отнета промяна и HR trend",
        ),
    )
    terrain_rejected_ranges: list[tuple[Any, Any]] = []
    if terrain_mode:
        downhill_column = _first_column(
            frame, ("downhill_mask", "sustained_downhill", "is_downhill")
        )
        downhill_ranges = (
            _contiguous_flag_ranges(frame, x_values, downhill_column)
            if downhill_column is not None
            else []
        )
        _add_grouped_ranges_trace(
            figure,
            downhill_ranges,
            y0=profile.hr_floor_bpm,
            y1=profile.hrmax_bpm,
            name="sustained downhill",
            fillcolor="rgba(14,116,144,0.12)",
            linecolor="rgba(14,116,144,0.35)",
        )
        terrain_rejected_ranges = _terrain_rejected_ranges(
            view.waves, x_column
        )
    _add_grouped_flag_trace(
        figure,
        frame,
        x_values,
        "receiver_flag",
        y0=profile.hr_floor_bpm,
        y1=profile.hrmax_bpm,
        name="receiver · allocation window",
        fillcolor="rgba(34,197,94,0.14)",
    )
    _add_grouped_flag_trace(
        figure,
        frame,
        x_values,
        "donor_flag",
        y0=profile.hr_floor_bpm,
        y1=profile.hrmax_bpm,
        name="donor · allocation window",
        fillcolor="rgba(249,115,22,0.14)",
    )
    if terrain_mode:
        _add_grouped_ranges_trace(
            figure,
            terrain_rejected_ranges,
            y0=profile.hr_floor_bpm,
            y1=profile.hrmax_bpm,
            name="terrain-adjusted wave",
            fillcolor="rgba(220,38,38,0.10)",
            linecolor="rgba(220,38,38,0.55)",
        )

    if terrain_mode:
        hr_traces: tuple[
            tuple[tuple[str, ...], str, str, str, float, float], ...
        ] = (
            (("raw_hr_bpm", "raw_hr"), "raw HR", "#64748b", "solid", 1.0, 0.65),
            (
                ("hrmod_candidate_bpm", "hrmod_candidate", "hrmod_bpm", "hrmod"),
                "HRmod candidate (HR-only)",
                "#7c3aed",
                "dash",
                1.7,
                0.9,
            ),
            (
                ("hrmod_final_bpm", "hrmod_final"),
                "HRmod final",
                "#dc2626",
                "solid",
                2.2,
                1.0,
            ),
        )
    else:
        hr_traces = (
            (("raw_hr_bpm", "raw_hr"), "raw_hr", "#64748b", "solid", 0.9, 0.55),
            (("clean_hr_bpm", "clean_hr"), "clean_hr", "#2563eb", "solid", 1.5, 0.9),
            (("hrmod_bpm", "hrmod"), "hrmod", "#dc2626", "solid", 2.2, 1.0),
        )
    if show_h_detect:
        hr_traces += (
            (("h_detect_bpm", "h_detect"), "h_detect (detection only)", "#7c3aed", "dot", 1.0, 0.8),
        )
    trace_class = go.Scattergl if use_webgl else go.Scatter
    for candidates, label, color, dash, width, opacity in hr_traces:
        column = _first_column(frame, candidates)
        if column is None:
            continue
        figure.add_trace(
            trace_class(
                x=x_values,
                y=pd.to_numeric(frame[column], errors="coerce"),
                mode="lines",
                name=label,
                line={"color": color, "width": width, "dash": dash},
                opacity=opacity,
                connectgaps=False,
            ),
            row=1,
            col=1,
        )

    correction_traces = (
        (
            ("hrmod_final_added_bpm", "added_bpm") if terrain_mode else ("added_bpm",),
            "+ final added_bpm" if terrain_mode else "+ added_bpm",
            "#16a34a",
            "solid",
            1.0,
        ),
        (
            ("hrmod_final_removed_bpm", "removed_bpm") if terrain_mode else ("removed_bpm",),
            "− final removed_bpm" if terrain_mode else "− removed_bpm",
            "#dc2626",
            "solid",
            -1.0,
        ),
        (("trend_bpm_per_s",), "trend (bpm/s)", "#7c3aed", "dash", 1.0),
    )
    for candidates, label, color, dash, sign in correction_traces:
        column = _first_column(frame, candidates)
        if column is None:
            continue
        figure.add_trace(
            trace_class(
                x=x_values,
                y=sign * pd.to_numeric(frame[column], errors="coerce"),
                mode="lines",
                name=label,
                line={"color": color, "width": 1.25, "dash": dash},
                connectgaps=False,
                fill="tozeroy" if label.startswith(("+", "−")) else None,
            ),
            row=2,
            col=1,
        )

    _add_grouped_wave_guides(
        figure,
        view.waves,
        x_column,
        y0=profile.hr_floor_bpm,
        y1=profile.hrmax_bpm,
    )
    for color, zone in zip(ZONE_COLORS, profile.zones, strict=True):
        figure.add_hrect(
            y0=zone.lower_bpm,
            y1=zone.upper_bpm,
            fillcolor=color,
            line_width=0,
            layer="below",
            annotation_text=zone.name,
            annotation_position="right",
            row=1,
            col=1,
        )
    figure.add_hline(y=0.0, line_color="rgba(100,116,139,0.5)", row=2, col=1)
    figure.update_yaxes(title_text="bpm", row=1, col=1)
    figure.update_yaxes(title_text="bpm / bpm·s⁻¹", row=2, col=1)
    figure.update_xaxes(
        title_text="Timestamp" if x_column == "timestamp" else "Elapsed (s)",
        row=2,
        col=1,
    )
    if view.x_range is not None:
        figure.update_xaxes(range=list(view.x_range))
    figure.update_layout(
        height=740 if selected_wave_id is None else 680,
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 45, "r": 35, "t": 80, "b": 45},
        uirevision="hrmod-overview" if selected_wave_id is None else f"hrmod-wave-{selected_wave_id}",
    )
    return figure


__all__ = ["PlotViewData", "build_hr_only_figure", "prepare_plot_view"]

