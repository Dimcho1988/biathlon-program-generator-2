from datetime import date, datetime, timezone
from types import SimpleNamespace
import math
import pytest
from fastapi.testclient import TestClient
from apps.api import energy_model as m, energy_service as s, dependencies, main
from apps.api.cloud import AthleteModelSettings
from biathlon import speed_equivalence

BOUNDS = (100, 120, 140, 160, 180, 200)


def report(rows, **kw):
    return m.activity(
        {"timeseries": rows},
        sex="MALE",
        weight=75,
        hrmax=200,
        bounds=BOUNDS,
        sport="Run",
        parameters=None,
        duration_s=len(rows),
        **kw
    )


def test_bounds_monotonicity_and_no_extrapolation():
    for sex in ("MALE", "FEMALE"):
        values = [m.cho_fraction(0.5 + i / 2000, sex) for i in range(1001)]
        assert all(0 <= v <= 1 for v in values)
        assert all(a <= b + 1e-10 for a, b in zip(values, values[1:]))
        assert m.cho_fraction(0.49, sex) is None
        assert m.cho_fraction(1.01, sex) is None
    assert m.cho_fraction(0.8, "FEMALE") == pytest.approx(0.3418807794)


def test_segments_not_average_and_conservation():
    rows = [
        {"elapsed_s": i, "hr_raw_bpm": h} for i, h in enumerate([120] * 60 + [180] * 60)
    ]
    r = report(rows)
    avg = report([{"elapsed_s": i, "hr_raw_bpm": 150} for i in range(120)])
    assert r["coverage_percent"] == 100
    assert r["carbohydrate_kcal"] != pytest.approx(avg["carbohydrate_kcal"])
    assert sum(r[k + "_kcal"] for k in m.MACROS) == pytest.approx(r["kcal"])
    assert sum(r[k + "_g"] * m.FACTORS[k] for k in m.MACROS) == pytest.approx(r["kcal"])
    assert sum(t["kcal"] for t in r["timeline"]) == pytest.approx(r["kcal"])


def test_missing_hr_never_filled_by_hrmod_or_zero():
    r = report([{"elapsed_s": i, "hrmod_final_bpm": 150} for i in range(20)])
    assert r["coverage_percent"] == 0 and r["unknown_seconds"] == 20
    assert r["source_seconds"]["measured_hr"] == 0


def test_speed_fallback_reuses_zone_position_and_marks_estimation():
    params = {
        "sport": "Run",
        "classification_bounds_kmh": [10, 12, 14, 16],
        "zones": [],
    }
    for i, z in enumerate(speed_equivalence.ZONES):
        low = 8 + i * 2
        high = low + 2
        params["zones"].append(
            {
                "zone": z,
                "status": "AVAILABLE",
                "low_kmh": low,
                "high_kmh": high,
                "slope_per_kmh": 0.1,
                "upper_weight": 1.2,
                "reference_speed_kmh": high,
            }
        )
    rows = [
        {"elapsed_s": i, "load_speed_kmh": 11, "load_status": "ACTIVE"}
        for i in range(60)
    ]
    r = m.activity(
        {"timeseries": rows},
        sex="FEMALE",
        weight=60,
        hrmax=200,
        bounds=BOUNDS,
        sport="Run",
        parameters=params,
        duration_s=60,
    )
    assert r["source_seconds"]["relative_intensity"] == 60
    assert r["source_seconds"]["measured_hr"] == 0
    assert r["coverage_percent"] == 100
    expected_rate = 60 * (
        -0.09354289849186385 + 0.3984908361869844 * 0.65 - 0.04590881586125497
    )
    assert r["kcal"] == pytest.approx(expected_rate)


def test_measured_hr_wins_over_speed_and_hrmod():
    r = report(
        [
            {
                "elapsed_s": i,
                "hr_raw_bpm": 140,
                "hrmod_final_bpm": 180,
                "load_speed_kmh": 18,
                "load_status": "ACTIVE",
            }
            for i in range(60)
        ]
    )
    assert r["kcal"] == pytest.approx(
        75 * (-0.09354289849186385 + 0.3984908361869844 * 0.7)
    )


def test_dietary_protein_not_oxidation_and_no_incomplete_or_minor_target():
    t = m.dietary_targets(3000, 68, 2, True)
    assert t["protein_g"] == 122.4
    assert 4 * (t["carbohydrate_g"] + t["protein_g"]) + 9 * t["fat_g"] == pytest.approx(
        3000
    )
    assert m.dietary_targets(None, 68, 2, True) is None
    assert m.dietary_targets(3000, 68, 2, False) is None


def test_dated_weight_ignores_future_and_before_profile():
    p = {"effective_from": "2026-11-10", "weight_kg": 68}
    entries = [
        {"kind": "WEIGHT", "observed_date": d, "payload": {"morning_kg": w}}
        for d, w in [("2026-11-09", 90), ("2026-11-14", 67), ("2026-11-19", 65)]
    ]
    assert s.weight_on(entries, [], p, date(2026, 11, 12)) == 68
    assert s.weight_on(entries, [], p, date(2026, 11, 15)) == 67


def test_prepared_route_does_not_compute(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "energy-test")
    repo = SimpleNamespace(
        prepared_view=lambda op, **kw: {
            "ready": True,
            "athlete_alias": kw["alias"],
            "kind": kw["kind"],
            "payload": {"ok": True},
        }
    )
    monkeypatch.setattr(dependencies, "repository", lambda: repo)
    monkeypatch.setattr(s, "history", lambda *a: pytest.fail("HTTP computed model"))
    client = TestClient(main.app)
    assert client.get("/api/v2/athlete/energy").status_code == 401
    r = client.get(
        "/api/v2/athlete/energy",
        headers={
            "Authorization": "Bearer energy-test",
            "X-OnFlows-Athlete-Alias": "ath-energy",
        },
    )
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_assistant_energy_denies_plan_only_access():
    from apps.api import assistant_context
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        assistant_context.read(
            SimpleNamespace(),
            "ath-energy",
            {"can_view_plan": True, "can_view_recovery": False},
            {"kind": "energy"},
        )
    assert exc.value.status_code == 403


class RestRepository:
    def __init__(self):
        self.settings = AthleteModelSettings(BOUNDS, "Europe/Vienna", hrmax_bpm=200)
        self.entries = [
            {
                "kind": "NUTRITION",
                "observed_date": "2026-10-01",
                "revision": 1,
                "payload": {
                    "effective_from": "2026-10-01",
                    "sex": "FEMALE",
                    "weight_kg": 68,
                    "height_cm": 170,
                    "birth_year": 1990,
                    "resting_kcal": 1800,
                    "outside_factor": 1.2,
                    "outside_cho_fraction": 0.5,
                    "preferences": "",
                    "allergies": "",
                },
            }
        ]

    def athlete_settings(self, alias):
        return self.settings

    def latest_response_entries(self, alias):
        return self.entries

    def active_activity_calendar(self, alias, start, end):
        return {
            "generation_id": "same",
            "revision": 1,
            "activities": [],
            "snapshot_payload": {
                "load_history": {
                    "period_start": "2026-10-01",
                    "period_end": "2026-11-19",
                }
            },
        }


def test_rest_day_total_and_today_partial():
    repo = RestRepository()
    data = s.history(
        repo,
        "a",
        date(2026, 11, 18),
        date(2026, 11, 19),
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
    )
    rest, current = data["days"]
    assert rest["total"]["kcal"] == 2160
    assert rest["activity"]["kcal"] == 0
    assert rest["targets"]["protein_g"] == 136
    assert current["total"] is None and current["targets"] is None
    assert current["outside"]["kcal"] == pytest.approx(1800 * 1.2 * 13 / 24)


def test_dst_day_uses_actual_elapsed_seconds():
    data = s.history(
        RestRepository(),
        "a",
        date(2026, 10, 25),
        date(2026, 10, 25),
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
    )
    assert data["days"][0]["total"]["kcal"] == pytest.approx(1800 * 1.2 * 25 / 24)


def test_missing_profile_preserves_missing_total():
    repo = RestRepository()
    repo.entries = []
    data = s.history(
        repo,
        "a",
        date(2026, 11, 18),
        date(2026, 11, 18),
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
    )
    assert data["days"][0]["total"] is None
    assert data["days"][0]["outside"] is None
    assert data["profile"] is None


def test_birth_year_does_not_promote_possible_seventeen_year_old_to_adult():
    repo = RestRepository()
    repo.entries[0]["payload"]["birth_year"] = 2008
    data = s.history(
        repo,
        "a",
        date(2026, 11, 18),
        date(2026, 11, 18),
        now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
    )
    assert data["days"][0]["targets"] is None


def test_activity_cache_reuses_recording_but_recalculates_dated_body_mass(monkeypatch):
    from apps.api import model_service
    from apps.api import energy_service

    repo = RestRepository()
    meta = {
        "latest_shadow_run_key": "pinned-run",
        "activity_ref": "recording",
        "sport": "Run",
        "local_date": "2026-11-18",
        "elapsed_time_s": 60,
        "start_at_utc": "2026-11-18T09:00:00Z",
    }
    calendar = repo.active_activity_calendar("a", None, None)
    calendar["activities"] = [meta]
    repo.active_activity_calendar = lambda *args: calendar
    cache = {}
    reads = []
    repo.read_activity_derivations = lambda a, k, keys: {
        key: cache[key] for key in keys if key in cache
    }
    repo.write_activity_derivations = lambda a, k, values: cache.update(values)

    def recordings(a, keys):
        reads.append(keys)
        yield "pinned-run", {
            "activity_ref": "recording",
            "shadow_payload": {
                "timeseries": [{"elapsed_s": i, "hr_raw_bpm": 150} for i in range(60)]
            },
        }

    repo.iter_activity_speed_history_samples = recordings
    monkeypatch.setattr(
        model_service,
        "speed_view_inputs",
        lambda *args: {"calendar": calendar, "today": date(2026, 11, 19)},
    )
    monkeypatch.setattr(model_service, "speed_view", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "apps.api.component_load_context.context_from_speed_view", lambda value: {}
    )

    def read_day():
        return energy_service.history(
            repo,
            "a",
            date(2026, 11, 18),
            date(2026, 11, 18),
            now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        )["days"][0]

    first = read_day()
    assert first["total"] is not None
    assert first["total"]["kcal"] == pytest.approx(
        first["activity"]["kcal"] + first["outside"]["kcal"]
    )
    repo.entries[0]["payload"]["outside_factor"] = 1.3
    second = read_day()
    assert len(reads) == 1
    assert first["activity"] == second["activity"]
    assert first["outside"]["kcal"] != second["outside"]["kcal"]
    repo.entries.append(
        {"kind": "WEIGHT", "observed_date": "2026-11-18", "payload": {"morning_kg": 70}}
    )
    third = read_day()
    assert len(reads) == 2
    assert third["activity"]["kcal"] / first["activity"]["kcal"] == pytest.approx(
        70 / 68
    )
