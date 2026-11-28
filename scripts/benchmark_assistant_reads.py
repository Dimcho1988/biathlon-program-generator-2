"""Measure fixed read conversations using synthetic data and no external calls.

Run this same script with --repo pointing at the baseline and changed checkout.
It measures Python handling + JSON store response transfer/decoding, not network
or model-provider latency. Scripted replies, all permissions, source schedules
and provenance are identical. SQL projections are emulated from the select list.
No athlete data, provider keys, paid calls or persistent writes are used.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter, process_time
from unittest.mock import patch


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def benchmark(repository_path, repeats):
    sys.path.insert(0, str(repository_path.resolve()))
    from apps.api import assistant_service as service
    from apps.api.assistant_provider import ScriptedAssistantProvider
    from apps.api.oauth_store import _PlanningSettingsBundle
    from apps.api.plan_storage import pack
    from tests.api.test_assistant_service import ConversationRepository, ALIAS, ACTOR, OWNER, body, query

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    settings = {"hr_zone_bounds": [90, 120, 140, 160, 180, 200], "timezone": "Europe/Sofia",
        "hrmax_bpm": 200, "hr_zone_source": "MANUAL", "model_preferences": {}}
    sessions = []
    for slot in range(2):
        blocks = [{"kind": "WORK", "label": f"Отсечка {repeat}/12", "repetition": repeat,
            "duration_s": 180, "zone": "Z3", "target_speed_kmh": 14,
            "instructions": "Равномерно изпълнение.", "method_library": {"guidance": "frozen" * 150}}
            for repeat in range(1, 13)]
        sessions.append({"method_id": f"method-{slot}", "title": "Прагова тренировка", "sport": "Run",
            "zone": "Z3", "purpose": "Прагова издръжливост", "total_minutes": 60, "main_work_minutes": 36,
            "blocks": blocks, "dose_evidence": {"explanation": "Frozen scientific evidence", "private": "x" * 3000}})
    days = [{"date": f"2026-11-{day}", "status": "TRAINING", "sessions": deepcopy(sessions),
             "session": deepcopy(sessions[0])} for day in range(13, 20)]
    plan = {"schema_version": "planning-draft-v1", "engine_version": "fixture-v1", "fingerprint": "unchanged-plan",
        "start_date": "2026-11-13", "end_date": "2026-11-19", "generated_at": "2026-11-12T12:00:00Z",
        "input_snapshot": {"generation_id": "fixture-generation", "analysis_revision": 3,
            "analysis_as_of": "2026-11-12", "private_scientific_snapshot": "s" * 700000},
        "source": {"private_scientific_snapshot": "t" * 700000}, "days": days,
        "long_term": {"private_evidence": "u" * 150000}}
    full = pack({"revision": 7, "operation": "REFRESH", "recorded_at": "2026-11-12T12:00:00Z",
        "payload": {"status": "ACTIVE", "plan": plan, "proposal": None,
                    "monitoring": {"private_evidence": "m" * 250000}}})

    class Repository(ConversationRepository):
        def __init__(self):
            super().__init__()
            self.counts = {}
            self.bytes = {}

        def transfer(self, category, value):
            raw = encode(value)
            self.counts[category] = self.counts.get(category, 0) + 1
            self.bytes[category] = self.bytes.get(category, 0) + len(raw)
            return json.loads(raw)

        def athlete_settings(self, alias):
            assert alias == ALIAS
            return _PlanningSettingsBundle([self.transfer("settings", settings)]).athlete_settings()

        def athlete_mesocycle_accent_preferences(self, alias):
            return self.transfer("accents", None)

        def _request(self, method, path, **kwargs):
            if path.startswith("/onflows_management_plan_state?"):
                assert method == "GET"
                fields = path.split("_fkey(", 1)[1].split(")&", 1)[0].split(",")
                record = {}
                for field in fields:
                    alias, source = field.split(":", 1) if ":" in field else (field, field)
                    value = full
                    for part in source.split("->"):
                        value = value.get(part) if isinstance(value, dict) else None
                    record[alias] = value
                return self.transfer("plan_pointer", [{"revision": full["revision"], "record": record}])
            result = super()._request(method, path, **kwargs)
            return self.transfer("conversation", result)

    def provider():
        return ScriptedAssistantProvider([
            {"text": "", "query": query("management", view="week")},
            {"text": "", "query": query("settings", view="profile")},
            {"text": "", "query": query("settings", view="accents")},
            {"text": "Седмичната програма и настройките са прочетени."},
        ])

    samples, signatures = [], set()
    for index in range(repeats + 1):
        repo, model = Repository(), provider()
        def access(current, alias, actor):
            assert current is repo and alias == ALIAS and actor == ACTOR
            return repo.transfer("authorization", OWNER)
        with patch.object(service.assistant_commands, "access", access), patch.object(service, "datetime", FrozenClock):
            started, cpu_started = perf_counter(), process_time()
            result = service.turn(repo, ALIAS, ACTOR, body("Покажи седмичната програма и настройките"), model)
            elapsed = (perf_counter() - started) * 1000
            cpu_elapsed = (process_time() - cpu_started) * 1000
        if result["message"] != "Седмичната програма и настройките са прочетени.":
            raise AssertionError(result)
        signature = sha256(encode(model.calls[-1]["context"]["results"])).hexdigest()
        signatures.add(signature)
        if index:
            samples.append({"elapsed_ms": elapsed, "cpu_ms": cpu_elapsed, "store_calls": sum(repo.counts.values()),
                "store_bytes": sum(repo.bytes.values()), "calls_by_kind": repo.counts, "bytes_by_kind": repo.bytes})
    if len(signatures) != 1:
        raise AssertionError("Read projections changed between identical repetitions")
    return {"scope": "SYNTHETIC_SCRIPTED_CONVERSATION_NO_NETWORK_OR_LLM", "repeats": repeats,
        "model_result_sha256": signatures.pop(), "median_elapsed_ms": round(statistics.median(s["elapsed_ms"] for s in samples), 3),
        "median_cpu_ms": round(statistics.median(s["cpu_ms"] for s in samples), 3),
        "store_calls": samples[0]["store_calls"], "store_bytes": samples[0]["store_bytes"],
        "calls_by_kind": samples[0]["calls_by_kind"], "bytes_by_kind": samples[0]["bytes_by_kind"],
        "samples_ms": [round(s["elapsed_ms"], 3) for s in samples]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error("At least two repetitions are required")
    result = benchmark(args.repo, args.repeats)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered)
