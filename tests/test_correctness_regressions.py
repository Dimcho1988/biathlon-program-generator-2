from __future__ import annotations

import builtins
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from biathlon.charts import readiness_figure
from biathlon.constants import COMPONENTS, fresh_parameters
from biathlon.demo_data import generate_activity_stream, generate_demo_bundle
from biathlon.monitoring import analyze_wellness
from biathlon.physiology import (
    compute_daily_load_history,
    compute_load_statistics,
    compute_readiness_history,
    rolling_load_statistics,
)
from biathlon.service import _hash_inputs, analyze_athlete
from biathlon.testing import aggregate_test_effects, analyze_tests


def _z3_test_history(
    latest_date: date,
    comparability: float = 1.0,
) -> pd.DataFrame:
    rows = []
    for offset in (30, 20, 10):
        rows.append(
            {
                "test_id": f"prior-{offset}",
                "athlete_id": "A",
                "date": pd.Timestamp(latest_date - timedelta(days=offset)),
                "test_code": "Z3_20MIN",
                "protocol_version": "1.0",
                "primary_value": 20.0,
                "secondary_value": 5.0,
                "valid": True,
                "comparability": 1.0,
                "conditions": "",
                "note": "",
            }
        )
    rows.append(
        {
            "test_id": "latest",
            "athlete_id": "A",
            "date": pd.Timestamp(latest_date),
            "test_code": "Z3_20MIN",
            "protocol_version": "1.0",
            "primary_value": 18.0,
            "secondary_value": 5.0,
            "valid": True,
            "comparability": comparability,
            "conditions": "",
            "note": "",
        }
    )
    return pd.DataFrame(rows)


def test_key_readiness_threshold_controls_key_stimulus_eligibility() -> None:
    bundle = generate_demo_bundle(history_days=120)
    bundle["parameters"]["key_readiness_threshold"] = 100.0

    analysis = analyze_athlete(bundle, "A", as_of=date.today(), generate_plan=True)

    assert not analysis["plan"]["key_stimulus"].any()


def test_readiness_chart_uses_configured_key_threshold() -> None:
    history = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-06-20"),
                "component": "Z3",
                "readiness_after": 80.0,
            }
        ]
    )

    figure = readiness_figure(history, key_readiness_threshold=87.0)
    horizontal_levels = {
        float(shape.y0)
        for shape in figure.layout.shapes
        if shape.y0 == shape.y1
    }

    assert 87.0 in horizontal_levels
    assert 90.0 not in horizontal_levels


def test_stale_illness_and_critical_pain_hard_flags_expire() -> None:
    bundle = generate_demo_bundle(history_days=60)
    wellness = bundle["wellness"].loc[bundle["wellness"]["athlete_id"] == "A"].copy()
    wellness["date"] = pd.to_datetime(wellness["date"]) - pd.Timedelta(days=10)
    latest_index = wellness["date"].idxmax()
    wellness.loc[latest_index, "illness"] = True
    wellness.loc[latest_index, "pain"] = 9.0
    parameters = deepcopy(bundle["parameters"])
    parameters["hard_flag_max_age_days"] = 3

    _, by_component, reasons = analyze_wellness(
        wellness,
        "A",
        parameters,
        as_of=date.today(),
    )

    assert not by_component["hard_flag"].any()
    assert not reasons


def test_recent_illness_still_creates_hard_flag_with_expiry_rule() -> None:
    bundle = generate_demo_bundle(history_days=60)
    wellness = bundle["wellness"].loc[bundle["wellness"]["athlete_id"] == "A"].copy()
    latest_index = wellness["date"].idxmax()
    wellness.loc[latest_index, "illness"] = True
    parameters = deepcopy(bundle["parameters"])
    parameters["hard_flag_max_age_days"] = 3

    _, by_component, reasons = analyze_wellness(
        wellness,
        "A",
        parameters,
        as_of=date.today(),
    )

    assert by_component["hard_flag"].all()
    assert any("заболяване" in reason.lower() for reason in reasons)


def test_analysis_before_first_activity_is_safe() -> None:
    bundle = generate_demo_bundle(history_days=30)
    first_activity = pd.to_datetime(bundle["activities"]["date"]).min().date()
    analysis_date = first_activity - timedelta(days=10)

    analysis = analyze_athlete(
        bundle,
        "A",
        as_of=analysis_date,
        generate_plan=False,
    )

    assert analysis["daily_loads"].empty
    assert analysis["load_stats"]["index_7_40"].notna().all()
    assert pd.to_datetime(analysis["readiness_history"]["date"]).max().date() == analysis_date


def test_empty_activity_history_has_current_readiness_snapshot() -> None:
    bundle = generate_demo_bundle(history_days=30)
    bundle["activities"] = bundle["activities"].loc[
        bundle["activities"]["athlete_id"] != "A"
    ].copy()

    analysis = analyze_athlete(
        bundle,
        "A",
        as_of=date.today(),
        generate_plan=False,
    )

    assert analysis["daily_loads"].empty
    assert len(analysis["readiness_history"]) == len(COMPONENTS)
    assert analysis["load_readiness"]["readiness"].eq(100.0).all()


def test_demo_stream_seed_does_not_depend_on_python_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = generate_demo_bundle(history_days=30)
    activity = bundle["activities"].iloc[0]
    zone_profile = bundle["zone_profiles"][str(activity["athlete_id"])]

    monkeypatch.setattr(builtins, "hash", lambda _: 111)
    first = generate_activity_stream(activity, zone_profile)
    monkeypatch.setattr(builtins, "hash", lambda _: 999_999)
    second = generate_activity_stream(activity, zone_profile)

    pd.testing.assert_frame_equal(first, second)


def test_short_history_uses_available_days_consistently() -> None:

    parameters = fresh_parameters()
    rows = []
    for current_date in pd.date_range("2026-06-01", periods=3, freq="D"):
        row = {"date": current_date}
        for component in COMPONENTS:
            row[f"q_{component}"] = 10.0 if component == "STR" else 0.0
        rows.append(row)

    daily = compute_daily_load_history(pd.DataFrame(rows), parameters)
    current = compute_load_statistics(
        daily,
        parameters,
        as_of=pd.Timestamp("2026-06-03"),
    )
    rolling = rolling_load_statistics(daily, parameters)
    readiness = compute_readiness_history(daily, parameters)

    rolling_last = rolling.loc[rolling["component"] == "STR"].iloc[-1]
    readiness_last = readiness.loc[readiness["component"] == "STR"].iloc[-1]

    assert current.loc["STR", "E7_daily"] == pytest.approx(10.0)
    assert rolling_last["E7_daily"] == pytest.approx(10.0)
    assert current.loc["STR", "E40_daily"] == pytest.approx(10.0)
    assert current.loc["STR", "Tref"] == pytest.approx(56.0)
    assert readiness_last["Tref"] == pytest.approx(56.0)


@pytest.mark.parametrize(
    ("table_name", "page"),
    [("wellness", "monitoring"), ("tests", "tests")],
)
def test_pages_render_with_empty_optional_tables(
    table_name: str,
    page: str,
) -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=180)
    app.query_params["page"] = page
    app.query_params["athlete"] = "A"
    app.run()
    assert not app.exception

    bundle = app.session_state["bundle"]
    bundle[table_name] = bundle[table_name].iloc[0:0].copy()
    app.session_state["bundle"] = bundle
    app.run()

    assert not app.exception


@pytest.mark.parametrize(
    ("table_name", "scenario"),
    [
        ("wellness", "Мониторинг · три неблагоприятни дни"),
        ("tests", "Контролен тест"),
    ],
)
def test_simulator_optional_data_scenarios_are_safe_with_empty_tables(
    table_name: str,
    scenario: str,
) -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=180)
    app.query_params["page"] = "simulator"
    app.query_params["athlete"] = "A"
    app.run()
    assert not app.exception

    bundle = app.session_state["bundle"]
    bundle[table_name] = bundle[table_name].iloc[0:0].copy()
    app.session_state["bundle"] = bundle
    app.selectbox(key="scenario_type").set_value(scenario)
    app.run()

    assert not app.exception


def test_audit_hash_changes_when_input_values_change_without_row_count_change() -> None:
    bundle = generate_demo_bundle(history_days=30)
    changed = deepcopy(bundle)
    mask = changed["activities"]["athlete_id"] == "A"
    row_index = changed["activities"].loc[mask].index[0]
    changed["activities"].loc[row_index, "real_Z2"] += 1.0

    assert _hash_inputs(bundle, "A") != _hash_inputs(changed, "A")


def test_final_key_stimulus_count_respects_configured_maximum() -> None:
    bundle = generate_demo_bundle(history_days=120)
    bundle["planning_preferences"]["A"]["max_key_sessions_per_week"] = 1

    analysis = analyze_athlete(bundle, "A", as_of=date.today(), generate_plan=True)

    assert int(analysis["plan"]["key_stimulus"].sum()) <= 1


def test_test_component_multipliers_control_planning_effect_magnitude() -> None:
    parameters = fresh_parameters()
    _, effects = analyze_tests(
        _z3_test_history(date.today()),
        "A",
        parameters,
        as_of=date.today(),
    )

    planning = aggregate_test_effects(effects, parameters, channel="planning")
    readiness = aggregate_test_effects(effects, parameters, channel="readiness")

    assert planning["Z3"] == pytest.approx(-0.035)
    assert planning["Z2"] == pytest.approx(-0.035 * (0.35 / 0.65))
    assert planning.drop(["Z2", "Z3"]).eq(0.0).all()
    assert readiness.eq(0.0).all()


def test_test_comparability_is_applied_once_not_squared() -> None:
    parameters = fresh_parameters()
    details, effects = analyze_tests(
        _z3_test_history(date.today(), comparability=0.5),
        "A",
        parameters,
        as_of=date.today(),
    )

    planning = aggregate_test_effects(effects, parameters, channel="planning")

    assert details.loc["Z3_20MIN", "raw_composite_change_pct"] == pytest.approx(-7.0)
    assert details.loc["Z3_20MIN", "effective_change_pct"] == pytest.approx(-3.5)
    assert planning["Z3"] == pytest.approx(-0.0175)


def test_test_effect_expires_and_decays_from_the_test_date() -> None:
    parameters = fresh_parameters()
    settings = parameters["test_settings"]["Z3_20MIN"]
    settings["max_age_days"] = 100
    settings["half_life_days"] = 10.0
    test_date = date.today() - timedelta(days=10)
    _, effects = analyze_tests(
        _z3_test_history(test_date),
        "A",
        parameters,
        as_of=date.today(),
    )

    current = aggregate_test_effects(effects, parameters, channel="planning")
    future = aggregate_test_effects(
        effects,
        parameters,
        channel="planning",
        future_days=91,
    )

    assert current["Z3"] == pytest.approx(-0.0175)
    assert future.eq(0.0).all()


def test_planning_only_test_channel_does_not_change_integrated_readiness() -> None:
    bundle = generate_demo_bundle(history_days=120)
    no_planning = deepcopy(bundle)
    for settings in no_planning["parameters"]["test_settings"].values():
        settings["planning_strength"] = 0.0

    without_test_planning = analyze_athlete(
        no_planning,
        "A",
        as_of=date.today(),
        generate_plan=False,
    )
    with_test_planning = analyze_athlete(
        bundle,
        "A",
        as_of=date.today(),
        generate_plan=False,
    )

    pd.testing.assert_series_equal(
        without_test_planning["integrated"]["integrated_readiness"],
        with_test_planning["integrated"]["integrated_readiness"],
    )
    assert not without_test_planning["weekly_targets"]["target_index"].equals(
        with_test_planning["weekly_targets"]["target_index"]
    )


def test_expert_test_settings_render_safely() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=180)
    app.query_params["page"] = "settings"
    app.query_params["athlete"] = "A"
    app.run()

    assert not app.exception
    assert any(tab.label == "Тестове" for tab in app.tabs)
