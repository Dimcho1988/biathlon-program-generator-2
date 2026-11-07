#!/usr/bin/env python3
"""Compare trial budget copies with the preceding deepcopy implementation.

Offline only: --replay contains the audit replay's input object; --bundle
contains generation.snapshot_payload.load_history. Neither fixture is emitted.
The replay omits TI/response/management observations and calls generate_plan
once per sample, without the outer improvement search. Its timings therefore
cannot establish live REFRESH latency. No repository/database write is made.

Run from the repository root with PYTHONPATH=. and API dependencies installed.
"""
import argparse
from copy import deepcopy
from datetime import date, datetime
import gc
from hashlib import sha256
import json
from pathlib import Path
import statistics
import time
from types import SimpleNamespace

from apps.api import model_service, training_plan_engine as engine
from apps.api.management_schemas import ManagementProfile
from apps.api.read_session import ReadSession


def benchmark(replay_path, bundle_path, now, pairs):
    inputs = json.loads(replay_path.read_text())["input"]
    generation = json.loads(bundle_path.read_text())["generation"]

    class Repository:
        def __init__(self):
            self.settings = SimpleNamespace(**inputs["physiology"])
            source = deepcopy(generation["snapshot_payload"]["load_history"])
            self.envelope = {**deepcopy(generation), "activities": [
                {**row, "local_date": row["date"], "duration_s": row["duration_min"] * 60}
                for row in source["activities"]]}

        def athlete_settings(self, alias):
            return self.settings

        def active_planning_calendar(self, alias, start, end):
            return deepcopy(self.envelope)

        active_activity_calendar = active_planning_calendar

        def athlete_planning_calendar(self, alias):
            return deepcopy(inputs["calendar"])

        def athlete_planning_profile(self, alias):
            return deepcopy(inputs["planning_preferences"])

        def athlete_mesocycle_accent_preferences(self, alias):
            return deepcopy(inputs["accent_preferences"])

        def _request(self, method, path, **kwargs):
            if method != "GET":
                raise AssertionError("Offline benchmark forbids writes")
            if path.startswith("/onflows_model_entries?"):
                return deepcopy(inputs["model_entries"])
            if path.startswith(("/onflows_response_entries?", "/onflows_management_")):
                return []
            raise AssertionError("Unexpected offline repository read")

        def _json(self, value):
            return value

    profile = ManagementProfile.model_validate(inputs["management_profile"]).model_dump(mode="json")
    speed_cache = {}
    original_speed = model_service.speed_view
    method_copy, capacity_copy = engine._copy_method_budget, engine._copy_capacity_budget

    def speed_view(repository, alias, sport):
        if sport not in speed_cache:
            speed_cache[sport] = original_speed(repository, alias, sport, _inputs={
                "alias": alias, "settings": repository.athlete_settings(alias), "today": now.date(),
                "calendar": repository.active_planning_calendar(alias, date.min, now.date()),
                "entries": [row for row in inputs["model_entries"] if row["kind"] == "SPEED_TEST"],
                "history": []})
        return deepcopy(speed_cache[sport])

    samples, expected = [], None
    try:
        model_service.speed_view = speed_view
        # Equalize import/catalogue warmup; predictor caches remain per plan.
        engine.generate_plan(ReadSession(Repository()), "athlete", deepcopy(profile),
                             start_date=now.date(), now=now)
        for pair in range(pairs):
            for label in (("previous", "reused") if pair % 2 == 0 else ("reused", "previous")):
                engine._copy_method_budget = deepcopy if label == "previous" else method_copy
                engine._copy_capacity_budget = deepcopy if label == "previous" else capacity_copy
                speed_cache.clear()
                repository, body = Repository(), deepcopy(profile)
                original_envelope = deepcopy(repository.envelope)
                gc.collect()
                wall_start, cpu_start = time.perf_counter(), time.process_time()
                result = engine.generate_plan(ReadSession(repository), "athlete", body,
                                              start_date=now.date(), now=now)
                cpu_seconds = time.process_time() - cpu_start
                wall_seconds = time.perf_counter() - wall_start
                encoded = json.dumps(result, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()
                if expected is None:
                    expected = encoded
                if encoded != expected:
                    raise AssertionError("Complete generated JSON changed")
                if repository.envelope != original_envelope or body != profile:
                    raise AssertionError("Generator changed its source inputs")
                samples.append({"variant": label, "wall_seconds": wall_seconds,
                                "cpu_seconds": cpu_seconds, "json_bytes": len(encoded),
                                "json_sha256": sha256(encoded).hexdigest()})
    finally:
        model_service.speed_view = original_speed
        engine._copy_method_budget, engine._copy_capacity_budget = method_copy, capacity_copy
    medians = {label: {metric: statistics.median(row[metric] for row in samples if row["variant"] == label)
                       for metric in ("wall_seconds", "cpu_seconds")} for label in ("previous", "reused")}
    return {"scope": "OFFLINE_PARTIAL_STANDALONE_GENERATOR", "complete_json_equal": True,
            "source_inputs_unchanged": True, "samples": samples, "medians": medians,
            "reduction_percent": {metric: 100 * (1 - medians["reused"][metric] / medians["previous"][metric])
                                  for metric in ("wall_seconds", "cpu_seconds")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--now", type=datetime.fromisoformat, required=True)
    parser.add_argument("--pairs", type=int, default=4)
    args = parser.parse_args()
    if args.pairs < 2 or args.now.tzinfo is None:
        parser.error("Use at least two pairs and an explicit timezone")
    print(json.dumps(benchmark(args.replay, args.bundle, args.now, args.pairs), indent=2))


if __name__ == "__main__":
    main()
