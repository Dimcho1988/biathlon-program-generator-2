from datetime import date, timedelta

import numpy as np
import pandas as pd

from biathlon.constants import COMPONENTS, fresh_parameters
from biathlon.demo_data import generate_demo_bundle, generate_activity_stream
from biathlon.physiology import (
    analyze_activity_stream,
    compute_daily_load_history,
    compute_load_statistics,
    effective_from_direct_vector,
    intrazone_coefficient,
    solve_direct_load,
)


def test_intrazone_coefficient_is_monotonic():
    values = [intrazone_coefficient(x, 150, 220, 1.15) for x in np.linspace(0, 1, 20)]
    assert values[0] == 1.0
    assert values[-1] > values[0]
    assert all(b >= a for a, b in zip(values, values[1:]))


def test_stream_preserves_real_time_and_increases_q():
    bundle = generate_demo_bundle(history_days=60)
    activity = bundle["activities"].loc[bundle["activities"]["athlete_id"] == "A"].iloc[-1]
    stream = generate_activity_stream(activity, bundle["zone_profiles"]["A"])
    summary = analyze_activity_stream(stream, bundle["zone_profiles"]["A"]).set_index("component")
    expected_real = sum(float(activity[f"real_{c}"]) for c in COMPONENTS[:5])
    assert abs(summary["real_min"].sum() - expected_real) < 0.2
    assert summary["q_min"].sum() >= summary["real_min"].sum()


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
