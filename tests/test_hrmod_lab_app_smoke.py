from __future__ import annotations

import ast
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

import hrmod_lab
from hrmod_lab.schemas import HRmodConfig, MODEL_VERSION


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPOSITORY_ROOT / "hrmod_lab_app.py"
PLOTTING_PATH = REPOSITORY_ROOT / "hrmod_lab" / "plotting.py"


def _synthetic_wave_tcx() -> bytes:
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
        "</Trackpoint>"
        for index, hr in enumerate(hr_values)
    )
    return (
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/'
        'TrainingCenterDatabase/v2"><Activities><Activity Sport="Other">'
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
    real_compute = hrmod_lab.compute_hrmod_hr_only

    def counted_compute(**kwargs):
        nonlocal compute_calls
        compute_calls += 1
        return real_compute(**kwargs)

    monkeypatch.setattr(hrmod_lab, "compute_hrmod_hr_only", counted_compute)
    app = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    app.file_uploader[0].upload(
        "synthetic-wave.tcx", _synthetic_wave_tcx(), "application/xml"
    ).run()

    compute = next(
        button for button in app.button if button.label == "Изчисли HRmod (само HR)"
    )
    compute.click().run()

    assert not app.exception
    assert not app.error
    assert any("изчислен само" in message.value for message in app.success)
    assert compute_calls == 1
    assert app.radio[0].label == "Резултатен изглед"
    assert app.radio[0].value == "HR-only сигнали"
    assert not any(widget.label == "Wave selector" for widget in app.selectbox)
    assert len(app.get("plotly_chart")) == 1

    before = app.session_state["hrmod_lab_core_run"]["result"]
    before_plain = asdict(before)
    before_hash = before.hr_input_hash
    assert "hrmod_lab_annotations" in app.session_state
    app.radio[0].set_value("HR вълни").run()

    assert not app.exception
    assert not app.error
    assert compute_calls == 1
    assert any(widget.label == "Wave selector" for widget in app.selectbox)
    assert len(app.get("plotly_chart")) == 1
    after = app.session_state["hrmod_lab_core_run"]["result"]
    assert after is before
    assert asdict(after) == before_plain
    assert after.hr_input_hash == before_hash
