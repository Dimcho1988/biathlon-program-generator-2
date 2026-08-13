from __future__ import annotations

import ast
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

import hrmod_lab
import hrmod_lab.terrain_gate as terrain_gate
from hrmod_lab.schemas import HRmodConfig, MODEL_VERSION


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPOSITORY_ROOT / "hrmod_lab_app.py"
PLOTTING_PATH = REPOSITORY_ROOT / "hrmod_lab" / "plotting.py"


def _synthetic_wave_tcx(
    *, include_grade: bool = True, grade_pct: float = 0.0
) -> bytes:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    hr_values = (
        [100] * 25
        + [100 + 2 * index for index in range(1, 21)]
        + [140 - 2 * index for index in range(1, 21)]
        + [100] * 11
    )
    trackpoints = "".join(
        "<Trackpoint>"
        f"<Time>{(start + timedelta(seconds=index)).isoformat().replace('+00:00', 'Z')}</Time>"
        f"<HeartRateBpm><Value>{hr}</Value></HeartRateBpm>"
        + (
            f"<Extensions><ns3:TPX><ns3:Grade>{grade_pct}</ns3:Grade>"
            "</ns3:TPX></Extensions>"
            if include_grade
            else ""
        )
        + "</Trackpoint>"
        for index, hr in enumerate(hr_values)
    )
    return (
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/'
        'TrainingCenterDatabase/v2" xmlns:ns3="http://www.garmin.com/xmlschemas/'
        'ActivityExtension/v2"><Activities><Activity Sport="Other">'
        "<Id>2026-01-01T00:00:00Z</Id>"
        '<Lap StartTime="2026-01-01T00:00:00Z">'
        "<TotalTimeSeconds>75</TotalTimeSeconds><DistanceMeters>0</DistanceMeters>"
        f"<Track>{trackpoints}</Track></Lap></Activity></Activities>"
        "</TrainingCenterDatabase>"
    ).encode("utf-8")


def test_standalone_hrmod_lab_renders_without_touching_production_navigation() -> None:
    app = AppTest.from_file(
        str(APP_PATH), default_timeout=60
    )
    app.run()
    assert not app.exception
    assert app.title[0].value == "HRmod Lab v2 · HR-only wave area shift"
    assert "HRmod v2 преразпределя" in app.warning[0].value
    assert (REPOSITORY_ROOT / "app.py").read_text(encoding="utf-8").find("hrmod_lab") == -1


def test_v2_config_contract_and_defaults_have_no_retired_model_fields() -> None:
    config = HRmodConfig()
    assert MODEL_VERSION == "hrmod_wave_area_shift_v2"
    assert config.config_version == "hrmod_config_v2"
    assert config.alpha == 1.0
    assert config.rise_threshold_bpm_s == 0.15
    assert config.min_rise_bpm == 5.0
    assert config.smoothing_window_s == 5.0

    config_fields = {item.name for item in fields(HRmodConfig)}
    retired_fields = {
        "delay_s",
        "tau_on_s",
        "tau_off_s",
        "correction_deadband_bpm",
        "min_lobe_duration_s",
        "min_lobe_area_bpm_s",
        "episode_balance_tolerance_bpm_s",
    }
    assert config_fields.isdisjoint(retired_fields)


def test_main_v2_ui_has_exactly_four_core_settings_before_advanced_panel() -> None:
    source = APP_PATH.read_text(encoding="utf-8")
    widget_source = source.split("def _model_config_widgets", 1)[1].split(
        'with st.expander("Advanced detection safeguards"', 1
    )[0]
    expected_labels = (
        '"alpha"',
        '"rise_threshold_bpm_s"',
        '"min_rise_bpm"',
        '"smoothing_window_s"',
    )
    assert all(widget_source.count(label) == 1 for label in expected_labels)
    assert widget_source.count(".slider(") + widget_source.count(".number_input(") == 4

    retired_ui_tokens = (
        "delay_s",
        "tau_on_s",
        "tau_off_s",
        "min_lobe_duration_s",
        "episode_balance_tolerance_bpm_s",
        "provisional_latent_demand",
    )
    assert all(token not in source for token in retired_ui_tokens)


def test_v2_ui_exposes_wave_visualization_and_exact_limitation() -> None:
    source = APP_PATH.read_text(encoding="utf-8")
    plotting_source = PLOTTING_PATH.read_text(encoding="utf-8")
    required_visual_fields = (
        "h_detect_bpm",
        "receiver_flag",
        "donor_flag",
        "rise_start_timestamp",
        "peak_timestamp",
        "tail_end_timestamp",
        "baseline_hr_bpm",
        "wave_summary",
        "Wave selector",
        "raw_zone_seconds",
        "hrmod_zone_seconds",
        "hrmod_minus_raw_zone_seconds",
    )
    assert all(field in source or field in plotting_source for field in required_visual_fields)
    exact_limitation = (
        "HRmod v2 преразпределя във времето част от наблюдавания HR отговор. "
        "Ако кратко усилие не остави различим HR отговор, HR-only моделът не може да го възстанови. "
        "Ако HR спада по време на продължаващо реално усилие, моделът не може да го знае без независим "
        "reference канал. Затова резултатът е експериментална HR-еквивалентна оценка, а не измерена мощност."
    )
    tree = ast.parse(source)
    limitation_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SCIENTIFIC_LIMITATION"
            for target in node.targets
        )
    )
    assert ast.literal_eval(limitation_assignment.value) == exact_limitation


def test_synthetic_tcx_uses_lazy_plot_views_and_cached_core(monkeypatch) -> None:
    compute_calls = 0
    terrain_calls = 0
    prepare_calls = 0
    real_compute = hrmod_lab.compute_hrmod_hr_only
    real_prepare = terrain_gate.prepare_terrain
    real_apply = terrain_gate.apply_terrain_gate

    def counted_compute(**kwargs):
        nonlocal compute_calls
        compute_calls += 1
        return real_compute(**kwargs)

    def counted_prepare(*args, **kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        return real_prepare(*args, **kwargs)

    def counted_apply(*args, **kwargs):
        nonlocal terrain_calls
        terrain_calls += 1
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(hrmod_lab, "compute_hrmod_hr_only", counted_compute)
    monkeypatch.setattr(terrain_gate, "prepare_terrain", counted_prepare)
    monkeypatch.setattr(terrain_gate, "apply_terrain_gate", counted_apply)
    app = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    app.file_uploader[0].upload(
        "synthetic-wave.tcx",
        _synthetic_wave_tcx(grade_pct=-4.0),
        "application/xml",
    ).run()

    compute = next(
        button for button in app.button if button.label == "Изчисли HRmod (само HR)"
    )
    compute.click().run()

    assert not app.exception
    assert not app.error
    assert any("изчислен само" in message.value for message in app.success)
    assert compute_calls == 1
    assert prepare_calls == terrain_calls == 1
    assert app.radio[0].label == "Резултатен изглед"
    assert app.radio[0].value == "HR-only сигнали"
    assert not any(widget.label == "Wave selector" for widget in app.selectbox)
    assert len(app.get("plotly_chart")) == 1
    default_plot = json.loads(app.get("plotly_chart")[0].proto.spec)
    default_types = {trace["name"]: trace["type"] for trace in default_plot["data"]}
    assert default_types["raw HR"] == "scatter"
    assert default_types["HRmod candidate"] == "scatter"
    assert default_types["HRmod final"] == "scatter"
    assert default_types["sustained downhill"] == "scatter"
    assert default_types["terrain-confounded wave"] == "scatter"
    assert "scattergl" not in default_types.values()

    before = app.session_state["hrmod_lab_core_run"]["result"]
    before_plain = asdict(before)
    before_hash = before.hr_input_hash
    terrain_before = app.session_state["hrmod_lab_terrain_result"]["result"]
    assert terrain_before.hr_input_hash == before_hash
    assert "hrmod_lab_annotations" in app.session_state

    app.radio[0].set_value(app.radio[0].options[2]).run()
    assert compute_calls == 1
    assert prepare_calls == terrain_calls == 1
    zone_table_before = app.dataframe[0].value
    required_zone_columns = {
        "raw_seconds",
        "clean_seconds",
        "hrmod_candidate_seconds",
        "hrmod_final_seconds",
        "final_minus_candidate_seconds",
    }
    assert required_zone_columns.issubset(zone_table_before.columns)
    candidate_zone_seconds = zone_table_before["hrmod_candidate_seconds"].tolist()
    final_zone_seconds_before = zone_table_before["hrmod_final_seconds"].tolist()
    assert final_zone_seconds_before != candidate_zone_seconds
    zone_plot_before = json.loads(app.get("plotly_chart")[0].proto.spec)
    assert [trace["name"] for trace in zone_plot_before["data"]] == [
        "Raw HR",
        "Clean HR",
        "HRmod candidate (HR-only core)",
        "HRmod final (terrain gate)",
    ]

    threshold = next(
        widget
        for widget in app.number_input
        if widget.label == "Downhill threshold (%)"
    )
    threshold.set_value(-5.0).run()

    assert not app.exception
    assert not app.error
    assert compute_calls == 1
    assert prepare_calls == terrain_calls == 2
    assert app.session_state["hrmod_lab_core_run"]["result"] is before
    terrain_after_threshold = app.session_state["hrmod_lab_terrain_result"]["result"]
    assert terrain_after_threshold is not terrain_before
    assert terrain_after_threshold.hr_input_hash == before_hash
    zone_table_after_threshold = app.dataframe[0].value
    assert zone_table_after_threshold["hrmod_candidate_seconds"].tolist() == (
        candidate_zone_seconds
    )
    assert zone_table_after_threshold["hrmod_final_seconds"].tolist() != (
        final_zone_seconds_before
    )
    assert zone_table_after_threshold["hrmod_final_seconds"].tolist() == (
        candidate_zone_seconds
    )

    terrain_toggle = next(
        widget for widget in app.checkbox if widget.label == "Enable terrain gate"
    )
    terrain_toggle.set_value(False).run()

    assert not app.exception
    assert not app.error
    assert compute_calls == 1
    assert prepare_calls == terrain_calls == 3
    disabled = app.session_state["hrmod_lab_terrain_result"]["result"]
    assert disabled.diagnostics.terrain_gate_enabled is False
    assert [point.hrmod_final_bpm for point in disabled.timeseries] == [
        point.hrmod_candidate_bpm for point in disabled.timeseries
    ]
    disabled_zone_table = app.dataframe[0].value
    assert disabled_zone_table["hrmod_candidate_seconds"].tolist() == (
        candidate_zone_seconds
    )
    assert disabled_zone_table["hrmod_final_seconds"].tolist() == (
        candidate_zone_seconds
    )
    assert app.session_state["hrmod_lab_core_run"]["result"] is before
    app.radio[0].set_value(app.radio[0].options[0]).run()
    webgl_toggle = next(
        widget
        for widget in app.checkbox
        if widget.label == "WebGL ускорение (само за съвместим браузър)"
    )
    assert webgl_toggle.value is False
    webgl_toggle.set_value(True).run()

    assert not app.exception
    assert not app.error
    assert compute_calls == 1
    assert prepare_calls == terrain_calls == 3
    webgl_plot = json.loads(app.get("plotly_chart")[0].proto.spec)
    webgl_types = {trace["name"]: trace["type"] for trace in webgl_plot["data"]}
    assert webgl_types["raw HR"] == "scattergl"
    assert webgl_types["HRmod candidate"] == "scattergl"
    assert webgl_types["HRmod final"] == "scattergl"
    assert sum(trace_type == "scattergl" for trace_type in webgl_types.values()) == 6
    assert app.session_state["hrmod_lab_core_run"]["result"] is before
    app.radio[0].set_value("HR вълни").run()

    assert not app.exception
    assert not app.error
    assert compute_calls == 1
    assert prepare_calls == terrain_calls == 3
    assert any(widget.label == "Wave selector" for widget in app.selectbox)
    assert len(app.get("plotly_chart")) == 1
    after = app.session_state["hrmod_lab_core_run"]["result"]
    assert after is before
    assert asdict(after) == before_plain
    assert after.hr_input_hash == before_hash


def test_missing_terrain_channel_warns_and_keeps_candidate_fallback() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    app.file_uploader[0].upload(
        "synthetic-no-terrain.tcx",
        _synthetic_wave_tcx(include_grade=False),
        "application/xml",
    ).run()
    next(
        button for button in app.button if button.label == "Изчисли HRmod (само HR)"
    ).click().run()

    assert not app.exception
    assert not app.error
    assert any(
        "Terrain gate не е приложен" in message.value for message in app.warning
    )
    terrain_result = app.session_state["hrmod_lab_terrain_result"]["result"]
    assert terrain_result.diagnostics.terrain_gate_applied is False
    assert [point.hrmod_final_bpm for point in terrain_result.timeseries] == [
        point.hrmod_candidate_bpm for point in terrain_result.timeseries
    ]


def test_pre_zone_cached_terrain_result_is_recomputed_without_core_rerun() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    app.file_uploader[0].upload(
        "synthetic-cache-migration.tcx",
        _synthetic_wave_tcx(),
        "application/xml",
    ).run()
    next(button for button in app.button if "HRmod" in button.label).click().run()

    core_before = app.session_state["hrmod_lab_core_run"]["result"]
    terrain_state = app.session_state["hrmod_lab_terrain_result"]
    legacy_result = {
        "timeseries": terrain_state["result"].timeseries,
        "wave_summary": terrain_state["result"].wave_summary,
    }
    app.session_state["hrmod_lab_terrain_result"] = {
        "signature": terrain_state["signature"],
        "prepared": terrain_state["prepared"],
        "result": legacy_result,
    }
    app.run()

    assert not app.exception
    assert not app.error
    assert app.session_state["hrmod_lab_core_run"]["result"] is core_before
    migrated = app.session_state["hrmod_lab_terrain_result"]["result"]
    assert migrated is not legacy_result
    assert len(migrated.zone_summary) == 5
