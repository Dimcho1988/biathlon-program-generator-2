from __future__ import annotations

import copy

import pandas as pd
import pytest

from hrmod_lab.plotting import build_hr_only_figure, prepare_plot_view
from hrmod_lab.schemas import AthleteHRProfile, HRZone


def _profile() -> AthleteHRProfile:
    return AthleteHRProfile(
        hrmax_bpm=200.0,
        hr_floor_bpm=50.0,
        zones=tuple(
            HRZone(
                name=f"Z{index + 1}",
                lower_bpm=50.0 + index * 30.0,
                upper_bpm=80.0 + index * 30.0,
            )
            for index in range(5)
        ),
    )


def _plot_frames(wave_count: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    samples_per_wave = 80
    rows: list[dict[str, object]] = []
    waves: list[dict[str, object]] = []
    for wave_index in range(wave_count):
        wave_id = wave_index + 1
        offset = wave_index * samples_per_wave
        waves.append(
            {
                "wave_id": wave_id,
                "rise_start_elapsed_s": float(offset + 10),
                "peak_elapsed_s": float(offset + 30),
                "tail_end_elapsed_s": float(offset + 60),
                "baseline_hr_bpm": 100.0,
            }
        )

    sample_count = max(100, wave_count * samples_per_wave)
    for index in range(sample_count):
        wave_index = index // samples_per_wave
        wave_id = wave_index + 1 if wave_index < wave_count else None
        local_index = index % samples_per_wave
        raw_hr = 100.0 + min(local_index, 30) * 0.5
        rows.append(
            {
                "elapsed_s": float(index),
                "dt_s": 1.0,
                "raw_hr_bpm": raw_hr,
                "clean_hr_bpm": raw_hr,
                "h_detect_bpm": raw_hr,
                "hrmod_bpm": raw_hr + 0.25,
                "added_bpm": 0.25 if 10 <= local_index <= 30 else 0.0,
                "removed_bpm": 0.25 if 30 < local_index <= 60 else 0.0,
                "trend_bpm_per_s": 0.5,
                "wave_id": wave_id,
                "receiver_flag": wave_id is not None and 10 <= local_index <= 30,
                "donor_flag": wave_id is not None and 30 < local_index <= 60,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(waves)


@pytest.mark.parametrize("wave_count", (1, 10, 108))
def test_overview_trace_and_shape_counts_are_small_and_constant(
    wave_count: int,
) -> None:
    timeseries, waves = _plot_frames(wave_count)

    figure = build_hr_only_figure(
        timeseries, waves, _profile(), show_h_detect=False
    )

    assert len(figure.data) == 12
    assert len(figure.layout.shapes or ()) == 6
    names = [trace.name for trace in figure.data]
    for expected in (
        "receiver · allocation window",
        "donor · allocation window",
        "s · rise start",
        "p · peak",
        "e · wave end",
        "B · local baseline",
    ):
        assert names.count(expected) == 1
    traces = {trace.name: trace for trace in figure.data}
    assert traces["raw_hr"].type == "scatter"
    assert traces["hrmod"].type == "scatter"
    assert len(figure.layout.shapes or ()) <= 6


@pytest.mark.parametrize(
    ("use_webgl", "expected_type"), ((False, "scatter"), (True, "scattergl"))
)
def test_full_series_backend_is_svg_by_default_and_webgl_only_when_requested(
    use_webgl: bool, expected_type: str
) -> None:
    timeseries, waves = _plot_frames(10)

    figure = build_hr_only_figure(
        timeseries,
        waves,
        _profile(),
        show_h_detect=True,
        use_webgl=use_webgl,
    )

    traces = {trace.name: trace for trace in figure.data}
    for name in (
        "raw_hr",
        "clean_hr",
        "hrmod",
        "h_detect (detection only)",
        "+ added_bpm",
        "− removed_bpm",
        "trend (bpm/s)",
    ):
        assert traces[name].type == expected_type
    for name in (
        "receiver · allocation window",
        "donor · allocation window",
        "s · rise start",
        "p · peak",
        "e · wave end",
        "B · local baseline",
    ):
        assert traces[name].type == "scatter"
    assert len(figure.data) == 13
    assert len(figure.layout.shapes or ()) == 6


def test_svg_and_webgl_backends_prepare_identical_plot_data() -> None:
    timeseries, waves = _plot_frames(10)
    svg_figure = build_hr_only_figure(
        timeseries, waves, _profile(), show_h_detect=True, use_webgl=False
    )
    webgl_figure = build_hr_only_figure(
        timeseries, waves, _profile(), show_h_detect=True, use_webgl=True
    )

    assert len(svg_figure.data) == len(webgl_figure.data)
    for svg_trace, webgl_trace in zip(
        svg_figure.data, webgl_figure.data, strict=True
    ):
        assert svg_trace.name == webgl_trace.name
        assert tuple(svg_trace.x) == tuple(webgl_trace.x)
        assert tuple(svg_trace.y) == tuple(webgl_trace.y)
    assert svg_figure.layout.shapes == webgl_figure.layout.shapes


def test_wave_zoom_slices_points_and_overlays_without_mutating_inputs() -> None:
    timeseries, waves = _plot_frames(108)
    timeseries_before = timeseries.copy(deep=True)
    waves_before = waves.copy(deep=True)
    selected_wave_id = 54

    view = prepare_plot_view(
        timeseries, waves, selected_wave_id=selected_wave_id
    )
    figure = build_hr_only_figure(
        timeseries,
        waves,
        _profile(),
        show_h_detect=True,
        selected_wave_id=selected_wave_id,
    )

    assert len(view.timeseries) == 61
    assert view.waves["wave_id"].tolist() == [selected_wave_id]
    flagged = view.timeseries.loc[
        view.timeseries["receiver_flag"] | view.timeseries["donor_flag"]
    ]
    assert flagged["wave_id"].dropna().eq(selected_wave_id).all()
    raw_trace = next(trace for trace in figure.data if trace.name == "raw_hr")
    hrmod_trace = next(trace for trace in figure.data if trace.name == "hrmod")
    assert len(raw_trace.x) == len(view.timeseries)
    assert len(hrmod_trace.x) == len(view.timeseries)
    assert len(view.timeseries) < len(timeseries) // 100
    pd.testing.assert_frame_equal(timeseries, timeseries_before)
    pd.testing.assert_frame_equal(waves, waves_before)


def test_plot_build_does_not_modify_core_result_frames() -> None:
    timeseries, waves = _plot_frames(10)
    expected_timeseries = copy.deepcopy(timeseries)
    expected_waves = copy.deepcopy(waves)

    build_hr_only_figure(timeseries, waves, _profile(), show_h_detect=False)

    pd.testing.assert_frame_equal(timeseries, expected_timeseries)
    pd.testing.assert_frame_equal(waves, expected_waves)


@pytest.mark.parametrize("wave_count", (1, 10, 108))
def test_terrain_overview_uses_constant_grouped_traces(wave_count: int) -> None:
    timeseries, waves = _plot_frames(wave_count)
    timeseries["hrmod_candidate_bpm"] = timeseries["hrmod_bpm"]
    timeseries["hrmod_final_bpm"] = timeseries["hrmod_bpm"]
    timeseries["smoothed_grade_pct"] = 0.0
    timeseries["downhill_mask"] = False
    waves["terrain_status"] = "accepted"
    if wave_count:
        rejected_wave = int(waves.iloc[-1]["wave_id"])
        waves.loc[waves["wave_id"].eq(rejected_wave), "terrain_status"] = (
            "terrain_confounded"
        )
        timeseries.loc[
            pd.to_numeric(timeseries["wave_id"], errors="coerce").eq(rejected_wave),
            "downhill_mask",
        ] = True

    figure = build_hr_only_figure(
        timeseries, waves, _profile(), show_h_detect=False
    )

    names = [trace.name for trace in figure.data]
    assert names.count("sustained downhill") == 1
    assert names.count("terrain-confounded wave") == 1
    assert names.count("raw HR") == 1
    assert names.count("HRmod candidate (HR-only)") == 1
    assert names.count("HRmod final") == 1
    assert len(figure.data) == 14
    assert len(figure.layout.shapes or ()) == 6
    assert all(trace.type == "scatter" for trace in figure.data)


def test_terrain_plot_preserves_svg_default_and_webgl_opt_in() -> None:
    timeseries, waves = _plot_frames(10)
    timeseries["hrmod_candidate_bpm"] = timeseries["hrmod_bpm"]
    timeseries["hrmod_final_bpm"] = timeseries["raw_hr_bpm"]
    timeseries["downhill_mask"] = timeseries["donor_flag"]
    waves["terrain_status"] = "terrain_confounded"

    figure = build_hr_only_figure(
        timeseries,
        waves,
        _profile(),
        show_h_detect=False,
        use_webgl=True,
    )
    traces = {trace.name: trace for trace in figure.data}

    for name in ("raw HR", "HRmod candidate (HR-only)", "HRmod final"):
        assert traces[name].type == "scattergl"
    for name in ("sustained downhill", "terrain-confounded wave"):
        assert traces[name].type == "scatter"
        assert None in tuple(traces[name].x)


@pytest.mark.parametrize("wave_count", (1, 10, 108))
def test_v3_morphology_guides_are_grouped_and_constant(
    wave_count: int,
) -> None:
    timeseries, waves = _plot_frames(wave_count)
    waves["morphology"] = "sustained"
    waves["correction_strategy"] = "v3_terminal_fall"
    waves["hold_start_elapsed_s"] = waves["rise_start_elapsed_s"] + 10.0
    waves["hold_end_elapsed_s"] = waves["rise_start_elapsed_s"] + 34.0
    waves["terminal_fall_start_elapsed_s"] = waves["hold_end_elapsed_s"]
    waves["terminal_fall_end_elapsed_s"] = waves["hold_end_elapsed_s"] + 10.0
    waves["hold_target_hr_bpm"] = 114.0

    figure = build_hr_only_figure(
        timeseries, waves, _profile(), show_h_detect=False
    )

    names = [trace.name for trace in figure.data]
    for expected in (
        "u · hold start",
        "h · hold end",
        "d · terminal fall start",
        "f · terminal fall end",
        "H · hold target",
    ):
        assert names.count(expected) == 1
    assert len(figure.data) == 17
    assert len(figure.layout.shapes or ()) == 6
