"""Compare exact planning outputs and local CPU costs on synthetic fixtures.

Run from a repository containing the baseline commit and installed API/test deps:
    python scripts/benchmark_recovery.py --baseline-ref 0f411c0 --repetitions 5

This does not contact services or read athlete data. The baseline is the actual
recovery module at the supplied Git revision, not a rewritten reference formula.
Wall times describe this local process, not staging latency or user capacity.
"""
from __future__ import annotations

import argparse
import cProfile
from datetime import timedelta
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api import training_plan_engine as engine
from biathlon import recovery_v2
from tests.api.test_recovery_computation import _planning_inputs
from tests.api.test_training_plan_engine import NOW, TODAY, reference_speed

MODES = ("legacy", "controls", "progression", "estimated", "double", "race", "strength", "locked")


def baseline_module(revision):
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        raise ValueError("Supply a Git commit SHA for the baseline")
    source = subprocess.run(
        ["git", "show", f"{revision}:biathlon/recovery_v2.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    module = types.ModuleType("_recovery_benchmark_baseline")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{revision}:biathlon/recovery_v2.py", "exec"), module.__dict__)
    return module


def benchmark(revision, repetitions):
    baseline = baseline_module(revision)
    optimized = recovery_v2.simulate
    saved_speed_view = engine.model_service.speed_view

    def original(*args, **kwargs):
        kwargs.pop("include_details", None)
        return baseline.simulate(*args, **kwargs)

    result = {
        "base_commit": revision,
        "data": "Synthetic 50-calendar-day, six-component planning fixtures; fixed 2026-09-21",
        "repetitions": repetitions,
        "timing_scope": "Local CPU; no network, database, service wake-up or rendering",
        "modes": {},
    }
    engine.model_service.speed_view = reference_speed
    try:
        for mode in MODES:
            recovery_v2.simulate = optimized
            repo, body, locked = _planning_inputs(mode)
            arguments = dict(start_date=TODAY, now=NOW, locked_day=locked)
            recovery_v2.simulate = original
            expected = engine.generate_plan(repo, "athlete", body, **arguments)
            recovery_v2.simulate = optimized
            assert engine.generate_plan(repo, "athlete", body, **arguments) == expected, mode
            timings = {"before_ms": [], "after_ms": []}
            for repeat in range(repetitions):
                operations = [("before_ms", original), ("after_ms", optimized)]
                for label, operation in operations if repeat % 2 == 0 else reversed(operations):
                    recovery_v2.simulate = operation
                    started = time.perf_counter()
                    actual = engine.generate_plan(repo, "athlete", body, **arguments)
                    timings[label].append((time.perf_counter() - started) * 1000)
                    assert actual == expected, mode
            before, after = (statistics.median(timings[key]) for key in ("before_ms", "after_ms"))
            entry = {**timings, "before_median_ms": before, "after_median_ms": after,
                     "speedup": before / after, "whole_payload_equal": True}
            result["modes"][mode] = entry
            print(json.dumps({"mode": mode, **entry}, allow_nan=False), flush=True)

        daily = [{"date": (TODAY - timedelta(days=age)).isoformat(), "zone": zone,
                  "effective_load": (age * 17 + index * 11) % 73 / 7}
                 for index, zone in enumerate(recovery_v2.ZONES) for age in range(55, -1, -1)]
        assert optimized(daily, target=TODAY) == baseline.simulate(daily, target=TODAY)
        result["default_full_projection_equal_to_base"] = True
        counts = {}
        for label, operation in (("before", original), ("after", optimized)):
            recovery_v2.simulate = operation
            profiler = cProfile.Profile()
            repo, body, _ = _planning_inputs("legacy")
            profiler.runcall(engine.generate_plan, repo, "athlete", body, start_date=TODAY, now=NOW)
            stats = profiler.getstats()
            counts[label] = {"total_function_calls": sum(s.callcount for s in stats),
                             "total_recursive_calls": sum(s.reccallcount for s in stats),
                             "profile_total_s": sum(s.inlinetime for s in stats)}
        result["profile_counts"] = counts
        print(json.dumps({"profile_counts": counts}), flush=True)
        return result
    finally:
        recovery_v2.simulate = optimized
        engine.model_service.speed_view = saved_speed_view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="0f411c0", help="Commit SHA with the original recovery module")
    parser.add_argument("--repetitions", type=int, default=5, choices=range(1, 21))
    parser.add_argument("--output", type=Path, help="Optional JSON result path")
    args = parser.parse_args()
    result = benchmark(args.baseline_ref, args.repetitions)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
