"""Read-only speed-load regression/performance replay.

Run from the repository root:
  python scripts/benchmark_speed_load.py --baseline-ref 0f411c0 --repeats 3

This measures local Python/read/decode costs, not network/service wake-up or
100-user capacity. The baseline module is loaded from a git revision; both
implementations receive identical full-resolution inputs. Synthetic data is
the default. An authorized private pinned snapshot can be supplied with
--replay-file; identifiers and payloads are never printed. No database
credentials, migrations or writes are involved. Pinned replay also simulates
arrival of the snapshot's actual last-date activities and a controlled 1%
prior-index correction in memory; both scenarios are explicitly labelled.
Provider sync, live network latency and service wake-up time are excluded.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, timedelta
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
from types import ModuleType, SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api import speed_load as optimized
from apps.api.speed_load_cache import SpeedLoadCache
from apps.api.shadow_storage import decode_shadow_payload
from tests.api.test_speed_load import Repository, TODAY


class SyntheticRepository(Repository):
    def __init__(self):
        super().__init__()
        self.speed_load_cache_namespace = str(uuid4())
        self.samples_reads = self.samples_activities = 0

    def active_trainability_calendar(self, alias, *dates):
        return super().active_trainability_calendar("authorized-athlete", *dates)

    def activity_speed_exposure_samples(self, alias, keys):
        self.samples_reads += 1
        self.samples_activities += len(keys)
        return super().activity_speed_exposure_samples("authorized-athlete", keys)


class PinnedReplayRepository:
    """Replay an authorized private snapshot without printing its identities."""
    def __init__(self, data):
        self.data = data
        self.speed_load_cache_namespace = str(uuid4())
        self.samples_reads = self.samples_activities = 0
        self.decode_seconds = 0.

    def check_alias(self, alias):
        if alias != self.data["alias"]:
            raise ValueError("Replay athlete is unavailable")

    def athlete_settings(self, alias):
        self.check_alias(alias)
        return SimpleNamespace(**self.data["settings"])

    def active_trainability_calendar(self, alias, start, end):
        self.check_alias(alias)
        return deepcopy(self.data["calendar"])

    def trainability_summaries(self, alias, keys):
        self.check_alias(alias)
        return deepcopy({key: self.data["summaries"][key] for key in keys})

    def activity_speed_exposure_samples(self, alias, keys):
        self.check_alias(alias)
        self.samples_reads += 1
        self.samples_activities += len(keys)
        started = perf_counter()
        result = {key: {**self.data["shadows"][key], "shadow_payload":
                       decode_shadow_payload(self.data["shadows"][key]["shadow_payload"])} for key in keys}
        self.decode_seconds += perf_counter()-started
        return result


def fixture(days, samples):
    repo = SyntheticRepository()
    for offset in range(days, 0, -1):
        key = f"synthetic-{offset}"
        repo.add(key, "Run", TODAY-timedelta(days=offset), 150, 15)
        repo.activities[-1]["duration_min"] = samples/60
        repo.shadows[key]["shadow_payload"]["speed_test_series"] = [
            {"elapsed_s": t, "dt_s": 1., "vflat_b65_kmh": 11 + (t % 9),
             "grade_smoothed_pct": -4. if t % 41 == 0 else 0.,
             "exclusion_reason": "COASTING" if t % 47 == 0 else None}
            for t in range(1, samples+1)]
    return repo


def compare(a, b, differences):
    if isinstance(a, bool) or a is None or isinstance(a, str):
        assert a == b
    elif isinstance(a, (int, float)):
        assert isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
        differences.append(abs(a-b))
    elif isinstance(a, dict):
        assert a.keys() == b.keys()
        for key in a:
            compare(a[key], b[key], differences)
    elif isinstance(a, list):
        assert len(a) == len(b)
        for left, right in zip(a, b):
            compare(left, right, differences)
    else:
        assert a == b


def replay(model, source):
    repo = deepcopy(source)
    repo.speed_load_cache_namespace = str(uuid4())
    model.speed_load_cache = SpeedLoadCache()
    if hasattr(model, "speed_activity_cache"):
        model.speed_activity_cache = SpeedLoadCache(max_entries=2048, ttl_seconds=1800)
    results = {}
    def run(name, alias="synthetic-athlete", **kwargs):
        before_reads, before_activities = repo.samples_reads, repo.samples_activities
        started = perf_counter()
        response = model.history_view(repo, alias, today=TODAY, **kwargs)
        results[name] = {"milliseconds": (perf_counter()-started)*1000,
            "samples_reads": repo.samples_reads-before_reads,
            "samples_activities": repo.samples_activities-before_activities,
            "response": response}
    run("first_open")
    run("repeat_same_report")
    run("period_change", period_start=TODAY-timedelta(days=13), period_end=TODAY)
    run("athlete_change", alias="another-synthetic-athlete")
    repo.add("synthetic-new", "Run", TODAY, 150, 15)
    run("after_new_training")
    repo.summaries[f"synthetic-{len(source.activities)}"]["trainability_index"]["general"]["index"] *= 1.05
    run("after_old_calibration_correction")
    return results


def replay_pinned(model, source):
    model.speed_load_cache = SpeedLoadCache()
    if hasattr(model, "speed_activity_cache"):
        model.speed_activity_cache = SpeedLoadCache(max_entries=2048, ttl_seconds=1800)
    repo = PinnedReplayRepository(source)
    end = max(date.fromisoformat(a["local_date"]) for a in source["calendar"]["activities"])
    results = {}
    def run(name, start, last):
        reads, activities, decode = repo.samples_reads, repo.samples_activities, repo.decode_seconds
        started = perf_counter()
        response = model.history_view(repo, source["alias"], today=end+timedelta(days=1),
                                      period_start=start, period_end=last)
        results[name] = {"milliseconds": (perf_counter()-started)*1000,
            "samples_reads": repo.samples_reads-reads, "samples_activities": repo.samples_activities-activities,
            "decode_ms": (repo.decode_seconds-decode)*1000, "response": response}
    for name, start, last in [("first_90_day_report", end-timedelta(days=89), end),
                             ("repeat_same_report", end-timedelta(days=89), end),
                             ("period_change", end-timedelta(days=31), end),
                             ("end_date_change", end-timedelta(days=31), end-timedelta(days=1))]:
        run(name, start, last)
    # Replay actual last-date streams as a simulated sync arrival. These
    # scenarios alter only a private in-memory copy and its generation marker.
    # Reset both caches so future streams were never already loaded.
    model.speed_load_cache = SpeedLoadCache()
    if hasattr(model, "speed_activity_cache"):
        model.speed_activity_cache = SpeedLoadCache(max_entries=2048, ttl_seconds=1800)
    repo.data = deepcopy(source)
    calendar = repo.data["calendar"]
    arrived = [a for a in calendar["activities"] if a["local_date"] == end.isoformat()]
    if arrived:
        calendar["activities"] = [a for a in calendar["activities"] if a not in arrived]
        calendar["generation_id"] = "simulated-before-arrival"
        calendar["revision"] = 1
        run("simulated_arrival_cold_report", end-timedelta(days=89), end)
        calendar["activities"].extend(arrived)
        calendar["generation_id"] = "simulated-after-arrival"
        calendar["revision"] = 2
        run("after_simulated_arrival", end-timedelta(days=89), end)
        # Small controlled prior-index correction exercises causal invalidation.
        candidates = [a for a in calendar["activities"] if
                      (end-timedelta(days=39)).isoformat() <= a["local_date"] < end.isoformat()]
        for activity in sorted(candidates, key=lambda a: (a["local_date"], a["activity_ref"])):
            index = repo.data["summaries"].get(activity.get("latest_shadow_run_key"), {}).get("trainability_index")
            bands = [*index.get("zones", []), index.get("general", {})] if index else []
            changed = False
            for band in bands:
                if band.get("valid") and optimized.finite_positive(band.get("index")):
                    band["index"] *= 1.01
                    changed = True
            if changed:
                calendar["revision"] = 3
                run("after_simulated_prior_correction", end-timedelta(days=89), end)
                break
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--samples", type=int, default=3600)
    parser.add_argument("--replay-file", type=Path,
        help="Authorized private JSON snapshot: alias/settings/calendar/summaries/shadows; contents are never printed")
    args = parser.parse_args()
    assert 1 <= args.days <= 366 and 1 <= args.samples <= 36000 and 1 <= args.repeats <= 10
    source_text = subprocess.run(["git", "show", f"{args.baseline_ref}:apps/api/speed_load.py"],
                                 check=True, text=True, capture_output=True).stdout
    baseline = ModuleType("apps.api._benchmark_speed_load_baseline")
    baseline.__package__ = "apps.api"
    exec(compile(source_text, f"{args.baseline_ref}:apps/api/speed_load.py", "exec"), baseline.__dict__)
    source = json.loads(args.replay_file.read_text()) if args.replay_file else fixture(args.days, args.samples)
    replay_model = replay_pinned if args.replay_file else replay
    measurements, differences = {}, []
    for repeat in range(args.repeats):
        before, after = replay_model(baseline, source), replay_model(optimized, source)
        for name in before:
            compare(before[name]["response"], after[name]["response"], differences)
            measurements.setdefault(name, []).append((before[name], after[name]))
    output = {"dataset": "REAL_PINNED_REPLAY_WITH_SIMULATED_ARRIVAL_AND_CORRECTION" if args.replay_file else "SYNTHETIC",
              "baseline_ref": args.baseline_ref,
              "activities": len(source["calendar"]["activities"]) if args.replay_file else args.days,
              "repeats": args.repeats,
              "conditions": "local Python, no network or service wake-up; no 100-user capacity claim",
              "maximum_absolute_numeric_difference": max(differences, default=0.), "scenarios": {}}
    if not args.replay_file:
        output["samples_per_activity"] = args.samples
    for name, samples in measurements.items():
        before_ms = statistics.median(pair[0]["milliseconds"] for pair in samples)
        after_ms = statistics.median(pair[1]["milliseconds"] for pair in samples)
        output["scenarios"][name] = {"before_ms": round(before_ms, 3), "after_ms": round(after_ms, 3),
            "speedup": round(before_ms/after_ms, 3),
            "before_sample_batches": samples[0][0]["samples_reads"],
            "after_sample_batches": samples[0][1]["samples_reads"],
            "before_sample_activities": samples[0][0]["samples_activities"],
            "after_sample_activities": samples[0][1]["samples_activities"]}
        if args.replay_file:
            output["scenarios"][name].update({
                "before_decode_ms": round(statistics.median(pair[0]["decode_ms"] for pair in samples), 3),
                "after_decode_ms": round(statistics.median(pair[1]["decode_ms"] for pair in samples), 3)})
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Private snapshots may contain identities in dictionary keys. Avoid
        # leaking those through assertion/KeyError representations or traces.
        print(f"Speed replay failed ({type(error).__name__}); inspect the local snapshot/inputs.", file=sys.stderr)
        sys.exit(1)
