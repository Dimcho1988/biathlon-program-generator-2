from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import perf_counter

import numpy as np
import pandas as pd

from hrmod_lab.hrmod_core import compute_hrmod_hr_only
from hrmod_lab.schemas import AthleteHRProfile, HRmodConfig, HRSample, HRZone
from vflat_b65 import apply_vflat_b65, detect_sprint_str


def test_hrmod_v4_and_vflat_b65_process_at_least_10000_samples() -> None:
    count = 10_000
    start = datetime(2026, 1, 1, tzinfo=UTC)
    hr = 145.0 + 25.0 * np.sin(np.arange(count) * 2.0 * np.pi / 90.0)
    samples = tuple(
        HRSample(start + timedelta(seconds=index), float(value))
        for index, value in enumerate(hr)
    )
    bounds = (50.0, 100.0, 120.0, 140.0, 160.0, 200.0)
    profile = AthleteHRProfile(
        hrmax_bpm=200.0,
        hr_floor_bpm=50.0,
        zones=tuple(
            HRZone(f"Z{index + 1}", bounds[index], bounds[index + 1])
            for index in range(5)
        ),
    )
    frame = pd.DataFrame(
        {
            "grade_pct": 6.0 * np.sin(np.arange(count) * 2.0 * np.pi / 300.0),
            "speed_mps": np.full(count, 5.0),
            "accel_mps2": np.zeros(count),
            "block": np.ones(count, dtype=int),
            "turn_flag": np.zeros(count, dtype=bool),
        }
    )
    started = perf_counter()
    hrmod = compute_hrmod_hr_only(
        hr_samples=samples,
        athlete_profile=profile,
        config=HRmodConfig(),
    )
    vflat = apply_vflat_b65(frame)
    sprint_str = detect_sprint_str(
        vflat.assign(
            timestamp=[start + timedelta(seconds=index) for index in range(count)]
        )
    )
    elapsed = perf_counter() - started
    assert len(hrmod.timeseries) == count
    assert len(vflat) == count
    assert len(sprint_str.sample_mask) == count
    assert elapsed < 15.0
