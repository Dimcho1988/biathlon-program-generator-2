from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from biathlon.constants import COMPONENTS, STRENGTH_COEFFICIENTS, fresh_parameters
from biathlon.demo_data import generate_demo_bundle, generate_activity_stream
from biathlon.physiology import (
    activities_to_activity_summaries,
    analyze_activity_stream,
    compute_daily_load_history,
    compute_load_statistics,
    effective_from_direct_vector,
    linear_equivalence_coefficient,
    solve_direct_load,
)


def test_linear_equivalence_coefficient_is_monotonic():
    values = [
        linear_equivalence_coefficient(hr, 140, 150, 3.0)
        for hr in np.linspace(140, 150, 20)
    ]
    assert values[0] == pytest.approx(0.70)
    assert values[-1] == pytest.approx(1.0)
    assert all(b >= a for a, b in zip(values, values[1:]))


def test_stream_preserves_real_time_and_calculates_equivalent_time():
    bundle = generate_demo_bundle(history_days=60)
    activity = bundle["activities"].loc[bundle["activities"]["athlete_id"] == "A"].iloc[-1]
    stream = generate_activity_stream(activity, bundle["zone_profiles"]["A"])
    summary = analyze_activity_stream(stream, bundle["zone_profiles"]["A"]).set_index("component")
    expected_real = sum(float(activity[f"real_{c}"]) for c in COMPONENTS[:5])
    assert abs(summary["real_min"].sum() - expected_real) < 0.2
    assert summary["q_min"].sum() >= 0.0
    active = summary.loc[summary["real_min"] > 0]
    assert active["average_minute_value_percent"].notna().all()


def test_demo_stream_uses_canonical_effective_hr_and_caps_z5_dose_at_hrmax():
    stream = pd.DataFrame({"hr": [200.0] * 60, "moving": [True] * 60})
    profile = pd.DataFrame(
        [
            {
                "component": "Z5",
                "hr_low": 178.0,
                "hr_high": 195.0,
                "equivalence_slope_pp_per_bpm": 3.0,
            }
        ]
    )

    z5 = analyze_activity_stream(stream, profile).set_index("component").loc["Z5"]

    assert z5["real_min"] == pytest.approx(1.0)
    assert z5["q_min"] == pytest.approx(1.51)
    assert z5["mean_effective_hr_bpm"] == pytest.approx(200.0)


def test_effective_cascade_and_nonnegative_inverse():
    params = fresh_parameters()
    tref = {c: 100.0 for c in COMPONENTS}
    q = {c: 0.0 for c in COMPONENTS}
    q["Z4"] = 20.0
    effective = effective_from_direct_vector(q, tref, params)
    assert effective[0] >= 20.0
    assert effective[1] >= 20.0
    assert effective[2] >= 20.0
    assert effective[3] >= 20.0
    target = pd.Series(effective, index=COMPONENTS)
    solved, error = solve_direct_load(target, pd.Series(tref), params)
    assert (solved >= 0).all()
    assert error < 1e-5


def test_uniform_load_gives_index_near_one():
    params = fresh_parameters()
    dates = pd.date_range(date.today() - timedelta(days=59), periods=60, freq="D")
    rows = []
    for d in dates:
        row = {"date": d}
        for c in COMPONENTS:
            row[f"q_{c}"] = 10.0 if c == "Z1" else 0.0
        rows.append(row)
    summaries = pd.DataFrame(rows)
    history = compute_daily_load_history(summaries, params)
    stats = compute_load_statistics(history, params)
    assert 0.98 <= stats.loc["Z1", "index_7_40"] <= 1.02


def test_four_strength_types_convert_to_aggregate_str_q():
    bundle = generate_demo_bundle(history_days=30)
    row = bundle["activities"].iloc[[0]].copy()
    row["real_STR_STAB"] = 10.0
    row["real_STR_END"] = 20.0
    row["real_STR_MAX"] = 15.0
    row["real_STR_PLY"] = 5.0
    row["real_STR"] = 50.0
    summaries = activities_to_activity_summaries(row, bundle["zone_profiles"][str(row.iloc[0]["athlete_id"])])
    result = summaries.iloc[0]

    expected = (
        10.0 * STRENGTH_COEFFICIENTS["STR_STAB"]
        + 20.0 * STRENGTH_COEFFICIENTS["STR_END"]
        + 15.0 * STRENGTH_COEFFICIENTS["STR_MAX"]
        + 5.0 * STRENGTH_COEFFICIENTS["STR_PLY"]
    )
    assert result["real_STR"] == pytest.approx(50.0)
    assert result["q_STR"] == pytest.approx(expected)
    assert result["k_STR"] == pytest.approx(expected / 50.0)
