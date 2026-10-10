from datetime import date, timedelta

import pandas as pd
import pytest

from biathlon.component_load import (
    EXPERT_CAPACITY_SOURCE,
    calculate_component_load,
    expert_zone_tmax_minutes,
)
from biathlon.constants import COMPONENTS, fresh_parameters
from biathlon.physiology import compute_daily_load_history, effective_from_direct_vector
from intervals_inspector.shadow_model import (
    calculate_shadow_result, configuration_from_safe_dict,
    configuration_to_safe_dict, configuration_with_overrides,
    default_shadow_configuration,
)


@pytest.mark.parametrize("dose,up,down", [
    (49.999, 0., 0.), (50., 10., 5.), (60., 12., 6.),
    (80., 16., 8.), (80.001, 24.0003, 16.0002),
])
def test_whole_direct_dose_at_both_exact_tier_boundaries(dose, up, down):
    result = calculate_component_load({"Z3": dose}, {"Z3": 100.})
    assert result["effective"] == pytest.approx({
        "Z1": 0., "Z2": down, "Z3": dose, "Z4": up, "Z5": 0., "STR": 0.,
    })


def test_received_load_cannot_spill_again_and_strength_stays_independent():
    result = calculate_component_load({"Z2": 1000., "STR": 10000.}, {"Z2": 100., "Z3": 1.})
    assert result["effective"] == pytest.approx({
        "Z1": 200., "Z2": 1000., "Z3": 300., "Z4": 0., "Z5": 0., "STR": 10000.,
    })
    assert result["direct_ratio"]["Z3"] == 0.


def test_only_existing_neighbors_receive_influence():
    result = calculate_component_load({"Z1": 60., "Z5": 90.}, {"Z1": 100., "Z5": 100.})
    assert result["effective"] == pytest.approx({
        "Z1": 60., "Z2": 12., "Z3": 0., "Z4": 18., "Z5": 90., "STR": 0.,
    })


def test_explicit_curve_tmax_is_authoritative_without_expert_clamp():
    result = calculate_component_load({"Z3": 60.}, {"Z3": 100.}, capacity_sources={"Z3": "TEST_CURVE"})
    assert result["capacity_minutes"]["Z3"] == 100.
    assert result["capacity_sources"]["Z3"] == "TEST_CURVE"
    assert result["effective"]["Z4"] == 12.
    assert result["capacity_sources"]["Z2"] == EXPERT_CAPACITY_SOURCE


@pytest.mark.parametrize("capacities", [None, {}, {"Z2": 100.}])
def test_absent_curve_uses_expert_continuous_capacity(capacities):
    result = calculate_component_load({"Z3": 60.}, capacities)
    assert result["capacity_minutes"]["Z3"] == expert_zone_tmax_minutes()["Z3"] == 52.5
    assert result["capacity_sources"]["Z3"] == EXPERT_CAPACITY_SOURCE
    assert result["effective"]["Z4"] == 18.


@pytest.mark.parametrize("invalid", [None, 0., -1., float("nan"), float("inf"), float("-inf"), True, False, "invalid"])
def test_invalid_explicit_curve_capacity_cannot_silently_become_expert(invalid):
    with pytest.raises(ValueError, match="Z3 Tmax must be finite and positive"):
        calculate_component_load({"Z3": 60.}, {"Z3": invalid})


def test_plan_and_recorded_load_match_and_tref_cannot_change_spill():
    direct = {"Z1": 130., "Z2": 60., "Z3": 90., "Z4": 5., "Z5": 20., "STR": 0.}
    capacities = {zone: 100. for zone in COMPONENTS[:5]}
    analysis = {"hr_coverage_percent": 100., "zones": [
        {"zone": zone, "equivalent_seconds": direct[zone] * 60.}
        for zone in COMPONENTS[:5]
    ]}
    today = date(2026, 10, 10)
    outputs = []
    for historical_load in (0., 10000.):
        plan = effective_from_direct_vector(direct, {z: historical_load for z in COMPONENTS},
                                            fresh_parameters(), zone_tmax_minutes=capacities)
        recorded = calculate_shadow_result(analysis, default_shadow_configuration(),
            zone_tmax_minutes=capacities,
            prior_daily_effective_load=[{"date": (today-timedelta(days=1)).isoformat(),
                                         **{z: historical_load for z in COMPONENTS[:5]}}],
            activity_date=today)
        values = [row["E_z"] for row in recorded["rows"]] + [0.]
        assert plan == pytest.approx(values)
        assert all(row["cascade"] == 0. for row in recorded["rows"])
        outputs.append(values)
    assert outputs[0] == outputs[1]


def test_two_sessions_preserve_separate_thresholds_in_daily_history():
    summaries = pd.DataFrame([{"date": pd.Timestamp("2026-10-10"),
                               **{f"q_{z}": (30. if z == "Z3" else 0.) for z in COMPONENTS}}
                              for _ in range(2)])
    params = fresh_parameters()
    params["zone_tmax_minutes"] = {"Z3": 100.}
    row = compute_daily_load_history(summaries, params).iloc[0]
    assert row["q_Z3"] == row["e_Z3"] == 60.
    assert row["e_Z2"] == row["e_Z4"] == 0.
    assert calculate_component_load({"Z3": 60.}, {"Z3": 100.})["effective"]["Z4"] == 12.


def test_supplied_per_sport_canonical_effect_is_preserved_in_daily_sum():
    summaries = pd.DataFrame([{"date": pd.Timestamp("2026-10-10"),
                               **{f"q_{z}": (60. if z == "Z3" else 0.) for z in COMPONENTS},
                               **{f"e_{z}": v for z,v in calculate_component_load(
                                   {"Z3": 60.}, {"Z3": cap})["effective"].items()}}
                              for cap in (100., 200.)])
    row = compute_daily_load_history(summaries, fresh_parameters()).iloc[0]
    assert row["e_Z3"] == 120.
    assert row["e_Z2"] == 6.
    assert row["e_Z4"] == 12.


def test_configuration_roundtrip_and_fixed_tier_coefficients():
    configuration = default_shadow_configuration()
    assert configuration_from_safe_dict(configuration_to_safe_dict(configuration)) == configuration
    with pytest.raises(ValueError, match="read-only"):
        configuration_with_overrides({"parameter.Z3.spill_down_fraction": .9})
