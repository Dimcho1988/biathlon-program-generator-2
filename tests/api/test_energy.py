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
    assert r["coverage_percent"] == 0 and r["unknown_seconds"] == 0
    assert r["source_seconds"]["measured_hr"] == 0
    assert r["source_seconds"]["sport_met"] == 20
    assert r["estimated_seconds"] == r["accounted_seconds"] == 20
    assert r["kcal"] == pytest.approx(7.5 * 3.5 * 75 / 200 * 20 / 60)


def test_missing_signal_and_outside_regression_range_are_explained():
    r = report([{"elapsed_s": 0, "hr_raw_bpm": 80}, {"elapsed_s": 1},
                {"elapsed_s": 2, "hr_raw_bpm": 150}])
    assert r["fallback_reasons_seconds"] == {"missing_signal": 1, "outside_hr_range": 1}
    assert r["accounted_seconds"] == 3
    assert r["estimated_seconds"] == 2
    assert r["unknown_seconds"] == 0


def test_strength_estimate_conserves_energy_without_claiming_sensor_coverage():
    r = m.strength_activity(weight=70, duration_s=3670, resting_kcal=1680)
    expected = 3.5 * 3.5 * 70 / 200 * 3670 / 60
    assert r["kcal"] == pytest.approx(expected)
    assert r["above_rest_kcal"] == pytest.approx(expected - 1680 * 3670 / 86400)
    assert r["coverage_percent"] == 0
    assert r["accounted_seconds"] == r["estimated_seconds"] == 3670
    assert sum(r["source_seconds"].values()) == 3670
    assert r["unknown_seconds"] == 0
    assert sum(t["seconds"] for t in r["timeline"]) == 3670
    for key in m.empty():
        assert sum(t[key] for t in r["timeline"]) == pytest.approx(r[key])
    assert sum(r[k + "_g"] * m.FACTORS[k] for k in m.MACROS) == pytest.approx(expected)
    changed = m.strength_activity(weight=70, duration_s=3670, met=6, cho=0.7)
    assert changed["kcal"] / r["kcal"] == pytest.approx(6 / 3.5)
    assert changed["carbohydrate_kcal"] / (changed["kcal"] - changed["protein_kcal"]) == pytest.approx(0.7)


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
    slow = m.activity({"timeseries": [{"elapsed_s": i, "load_speed_kmh": 4,
                         "load_status": "ACTIVE"} for i in range(60)]},
                     sex="FEMALE", weight=60, hrmax=200, bounds=BOUNDS,
                     sport="Run", parameters=params, duration_s=60, resting_kcal=1440)
    assert slow["source_seconds"]["low_intensity"] == 60
    assert slow["source_seconds"]["rest"] == 0
    assert 1 < slow["kcal"] < r["kcal"]


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
    assert t["protein_g"] == pytest.approx(110.9)
    assert 4 * (t["carbohydrate_g"] + t["protein_g"]) + 9 * t["fat_g"] == pytest.approx(
        3000
    )
    assert m.dietary_targets(None, 68, 2, True)["protein_g"] == pytest.approx(122.4)
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
    assert rest["targets"]["protein_g"] == pytest.approx(108.8)
    assert current["total"] is None and current["targets"]["kcal"] == 2160
    assert current["targets"]["protein_g"] == pytest.approx(108.8)
    assert current["nutrition_basis"]["window_end"] == "2026-11-18"
    assert current["outside"]["kcal"] == pytest.approx(1800 * 1.2 * 13 / 24)


def strength_day(sport="WeightTraining", duration=3600, entries=None, second_activity=False):
    repo = RestRepository()
    if entries is not None:
        repo.entries = entries
    calendar = repo.active_activity_calendar("a", None, None)
    calendar["activities"] = [{"activity_ref": "strength", "sport": sport,
        "local_date": "2026-11-18", "elapsed_time_s": duration,
        "start_at_utc": "2026-11-18T09:00:00Z"}]
    if second_activity:
        calendar["activities"].append({"activity_ref": "missing", "sport": "Swim",
            "local_date": "2026-11-18", "elapsed_time_s": 1800,
            "start_at_utc": "2026-11-18T15:00:00Z"})
    repo.active_activity_calendar = lambda *args: calendar
    return s.history(repo, "a", date(2026, 11, 18), date(2026, 11, 18),
                     now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc))["days"][0]


def test_strength_daily_gross_energy_does_not_double_count_resting_time():
    d = strength_day()
    assert d["total"] is not None
    assert d["coverage_percent"] == 0  # data coverage stays distinct from estimated duration
    assert d["estimated_activity_seconds"] == 3600
    assert d["unknown_activity_seconds"] == 0
    assert d["outside"]["kcal"] == pytest.approx(1800 * 1.2 * 23 / 24)
    assert d["total"]["kcal"] == pytest.approx(1800 * 1.2 * 23 / 24 + 3.5 * 3.5 * 68 / 200 * 60)
    for key in (*m.empty(), *(k + "_g" for k in m.MACROS)):
        assert d["total"][key] == pytest.approx(d["activity"][key] + d["outside"][key])


def test_strength_assumptions_are_dated_and_do_not_read_sensor_streams():
    entries = RestRepository().entries
    entries[0]["payload"].update(strength_met=6, strength_cho_fraction=0.7)
    entries.append({"kind": "NUTRITION", "observed_date": "2026-11-19", "revision": 2,
                    "payload": {**entries[0]["payload"], "strength_met": 12}})
    d = strength_day(entries=entries)
    assert d["activities"][0]["met"] == 6
    assert d["activities"][0]["carbohydrate_fraction_assumption"] == 0.7


def test_catalog_only_activity_completes_day_and_dietary_targets():
    d = strength_day(second_activity=True)
    assert d["total"]["kcal"] > d["targets"]["kcal"] == 2160
    assert d["unknown_activity_seconds"] == 0
    assert d["estimated_activity_seconds"] == 5400
    assert d["outside"]["kcal"] == pytest.approx(1800 * 1.2 * 22.5 / 24)
    assert d["activities"][1]["source_seconds"]["sport_met"] == 1800
    assert all(d["accounted"][k + "_g"] > 0 for k in m.MACROS)
    assert all(d["targets"][k + "_g"] > 0 for k in m.MACROS)
    assert sum(d["targets"][k + "_g"] * m.FACTORS[k] for k in m.MACROS) == pytest.approx(d["targets"]["kcal"])
    assert "90.0 min" in d["reason"]


@pytest.mark.parametrize("sport,duration,entries", [
    ("WeightTraining", 3600, []), ("WeightTraining", 0, None)])
def test_no_estimate_without_weight_or_duration(sport, duration, entries):
    d = strength_day(sport=sport, duration=duration, entries=entries)
    assert d["activities"][0]["kcal"] is None
    assert d["total"] is None
    assert d["targets"] is None if entries == [] else d["targets"]["kcal"] == 2160


@pytest.mark.parametrize("sport", ["Run", "Ride", "NordicSki", "Swim", "Yoga", "Other"])
def test_every_catalog_activity_with_weight_and_duration_has_energy(sport):
    d = strength_day(sport=sport)
    assert d["activities"][0]["kcal"] > 0
    assert d["activities"][0]["unknown_seconds"] == 0
    assert d["total"] is not None


def test_pauses_and_sensor_gaps_are_different_estimates_and_do_not_change_load():
    from biathlon.unified_load import integrate
    shadow = {"timeseries": [
        {"elapsed_s": 0, "hr_raw_bpm": 140},
        {"elapsed_s": 1, "hr_interval_classification": "recording_stop", "hr_interval_end_s": 61},
        {"elapsed_s": 61, "hr_raw_bpm": 160},
        {"elapsed_s": 70, "hr_raw_bpm": 160},
    ]}
    before = integrate(shadow, bounds=BOUNDS, sport="Run", duration_s=71, hr_signal="hr_raw_bpm")
    r = m.activity(shadow, sex="MALE", weight=75, hrmax=200, bounds=BOUNDS,
                   sport="Run", parameters=None, duration_s=71, resting_kcal=1440)
    after = integrate(shadow, bounds=BOUNDS, sport="Run", duration_s=71, hr_signal="hr_raw_bpm")
    assert before == after
    assert r["source_seconds"]["pause_rest"] == 60
    assert r["source_seconds"]["sport_met"] == 8
    assert r["covered_seconds"] == 3
    assert r["accounted_seconds"] == sum(r["source_seconds"].values()) == 71
    assert r["unknown_seconds"] == 0
    assert sum(b["seconds"] for b in r["timeline"]) == 71
    for key in m.empty():
        assert sum(b[key] for b in r["timeline"]) == pytest.approx(r[key])


def test_raw_speed_only_is_estimated_and_stationary_outdoor_time_is_rest():
    slow = report([{"elapsed_s": i, "speed_raw_kmh": 6.5} for i in range(60)], resting_kcal=1440)
    fast = report([{"elapsed_s": i, "speed_raw_kmh": 12} for i in range(60)], resting_kcal=1440)
    stopped = report([{"elapsed_s": i, "speed_raw_kmh": 0} for i in range(60)], resting_kcal=1440)
    assert 0 < slow["kcal"] < fast["kcal"]
    assert fast["source_seconds"]["pace_met"] == 60
    assert stopped["source_seconds"]["rest"] == 60
    assert stopped["kcal"] == pytest.approx(1)
    for r in [slow, fast, stopped]:
        assert r["unknown_seconds"] == 0
        assert sum(r[k + "_kcal"] for k in m.MACROS) == pytest.approx(r["kcal"])


@pytest.mark.parametrize("sport", ["VirtualRide", "VirtualRun", "Swim"])
def test_zero_gps_does_not_imply_rest_for_indoor_or_water_activity(sport):
    r = m.activity({"timeseries": [{"elapsed_s": i, "speed_raw_kmh": 0} for i in range(60)]},
                   sex="MALE", weight=75, hrmax=200, bounds=BOUNDS,
                   sport=sport, parameters=None, duration_s=60)
    assert r["source_seconds"]["rest"] == 0
    assert r["source_seconds"]["sport_met"] == 60


def test_low_and_above_max_hr_are_accounted_and_missing_sex_uses_prior():
    low = report([{"elapsed_s": i, "hr_raw_bpm": 60} for i in range(60)], resting_kcal=1440)
    middle = report([{"elapsed_s": i, "hr_raw_bpm": 80} for i in range(60)], resting_kcal=1440)
    edge = report([{"elapsed_s": i, "hr_raw_bpm": 100} for i in range(60)], resting_kcal=1440)
    high = report([{"elapsed_s": i, "hr_raw_bpm": 205} for i in range(60)])
    max_hr = report([{"elapsed_s": i, "hr_raw_bpm": 200} for i in range(60)])
    assert low["kcal"] == pytest.approx(1)
    assert low["kcal"] < middle["kcal"] < edge["kcal"]
    assert high["kcal"] == max_hr["kcal"]
    assert high["source_seconds"]["bounded_hr"] == 60
    r = m.activity({}, sex=None, weight=75, hrmax=None, bounds=BOUNDS,
                   sport="Run", parameters=None, duration_s=60)
    assert r["source_seconds"]["sport_met"] == 60
    assert r["unknown_seconds"] == 0


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


@pytest.mark.parametrize("energy,coefficient", [(20, 1.6), (40, 1.6), (50, 1.675), (60, 1.75), (80, 1.9), (100, 1.9)])
def test_protein_energy_rule_boundaries_conservation_and_no_training_switch(energy, coefficient):
    for hours in (0, 0.2, 1, 5):
        t = m.dietary_targets(energy * 67, 67, hours, True, 0.4)
        assert t["protein_g"] == pytest.approx(67 * coefficient)
        assert t["protein_g_per_kg"] == pytest.approx(coefficient)
        assert t["energy_kcal_per_kg"] == energy
        assert t["carbohydrate_g"] * 4 + t["fat_g"] * 9 + t["protein_g"] * 4 == pytest.approx(energy * 67)
        assert t["carbohydrate_g"] * 4 / (t["fat_g"] * 9) == pytest.approx(2/3)


def test_partial_targets_only_protein_and_missing_weight_or_minor_stay_absent():
    for total in (None, 0, float("nan")):
        t = m.dietary_targets(total, 67, 0, True)
        assert t["protein_g"] == pytest.approx(120.6)
        assert t["protein_basis"] == "INCOMPLETE_DAY_FALLBACK"
        assert all(t[k] is None for k in ("kcal", "carbohydrate_g", "fat_g", "energy_kcal_per_kg"))
    assert m.dietary_targets(None, None, 0, True) is None
    assert m.dietary_targets(None, 67, 0, False) is None


def test_reuses_imported_body_and_dated_weight_without_nutrition_entry():
    repo = RestRepository()
    repo.entries = [{"kind": "WEIGHT", "observed_date": "2026-11-17", "payload": {"morning_kg": 67}}]
    calendar = repo.active_activity_calendar("a", None, None)
    calendar["snapshot_payload"]["nutrition_body_inputs"] = {
        "observed_on": "2026-11-19", "sex": "FEMALE", "height_cm": 170, "birth_year": 1990, "weight_kg": 70}
    calendar["snapshot_payload"]["wellness_calendar"] = [
        {"date": "2026-11-18", "metrics": {"weight": {"value": 66}}},
        {"date": "2026-11-20", "metrics": {"weight": {"value": 65}}}]
    repo.active_activity_calendar = lambda *args: calendar
    data = s.history(repo, "a", date(2026,11,17), date(2026,11,18), now=datetime(2026,11,19,12,tzinfo=timezone.utc))
    assert [d["weight_kg"] for d in data["days"]] == [67, 66]
    assert data["profile_missing"] == []
    assert data["profile"]["height_cm"] == 170
    assert data["profile_revision"] == 0
    for day in data["days"]:
        assert day["total"] is not None
        assert sum(day["energy_sources"].values()) == pytest.approx(day["total"]["kcal"])
    repo.entries = []
    calendar["snapshot_payload"]["wellness_calendar"] = []
    data = s.history(repo, "a", date(2026,11,18), date(2026,11,19), now=datetime(2026,11,19,12,tzinfo=timezone.utc))
    assert data["days"][0]["weight_kg"] is None  # today's provider mass is not backdated
    assert data["days"][1]["weight_kg"] == 70


def test_outside_factor_change_does_not_require_duplicate_demographics():
    body = s.NutritionProfile(effective_from="2026-11-18", outside_factor=1.4)
    assert body.sex is None and body.weight_kg is None
    imported = {"observed_on": "2026-11-17", "sex": "FEMALE", "birth_year": 1990, "weight_kg": 67, "height_cm": 170}
    entries = [{"kind": "NUTRITION", "observed_date": "2026-11-18", "payload": body.model_dump(mode="json")}]
    p = s.resolved_profile(entries, [], imported, date(2026,11,18))
    assert (p["weight_kg"], p["height_cm"], p["outside_factor"]) == (67, 170, 1.4)
    assert s.resolved_profile(entries, [], imported, date(2026,11,17))["outside_factor"] == 1.2


def test_provider_profile_is_allowlisted_and_invalid_values_stay_missing():
    from apps.api.nutrition_inputs import provider_body_inputs
    raw = {"sex": "F", "icu_weight": 67, "weight": 70, "height": 170,
           "icu_date_of_birth": "1990-04-05", "email": "private", "icu_api_key": "secret", "id": "private"}
    assert provider_body_inputs(raw, date(2026,11,19)) == {
        "observed_on": "2026-11-19", "sex": "FEMALE", "weight_kg": 67.0, "height_cm": 170.0, "birth_year": 1990,
        "weight_observations": [{"day": "2026-11-19", "weight_kg": 67.0}]}
    assert provider_body_inputs({"sex": "?", "icu_weight": float("nan"), "height": 1.7,
                                 "icu_date_of_birth": "bad"}, date(2026,11,19)) is None
    assert provider_body_inputs(None, date(2026,11,19)) is None


def test_provider_weight_history_survives_refresh_and_never_backdates_changes():
    from apps.api.nutrition_inputs import provider_body_inputs
    first = provider_body_inputs({"icu_weight": 67}, date(2026,11,17))
    same = provider_body_inputs({"icu_weight": 67}, date(2026,11,18), first)
    changed = provider_body_inputs({"icu_weight": 66}, date(2026,11,19), same)
    assert len(same["weight_observations"]) == 1
    assert s.resolved_profile([], [], changed, date(2026,11,16))["weight_kg"] is None
    assert s.resolved_profile([], [], changed, date(2026,11,18))["weight_kg"] == 67
    assert s.resolved_profile([], [], changed, date(2026,11,19))["weight_kg"] == 66
