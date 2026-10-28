"""Initial threshold through real service inputs, profile store and admission."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.api import model_service as service
from apps.api.activity_shadow_pipeline import activity_shadow_configuration_fingerprint
from apps.api.read_session import ReadSession
from apps.api.trainability import MODEL_VERSION, SCHEMA_VERSION
from vflat_b65.sports import speed_model_versions


NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
BOUNDS = (100, 125, 145, 160, 175, 190)


class Repository:
    def __init__(self):
        self.reads = []
        self.entries = []
        self.profile = {"training_experience_years": 4}
        self.summaries = {}
        self.profile_forbidden = False
        dates = [(NOW.date()-timedelta(days=i)).isoformat() for i in range(1, 29)]
        rows = []
        for i in (1, 8, 15, 22):
            day = (NOW.date()-timedelta(days=i)).isoformat()
            # Four weeks of 4 h running + 4 h strength, with one passive hour
            # per run: H=8; raw moving speed=36 km / 3 h=12 km/h.
            rows.extend([
                {"activity_ref": f"run-{i}", "sport": "Run", "local_date": day,
                 "recording_time_s": 14400, "moving_time_s": 10800, "distance_m": 36000,
                 "quality_status": "limited", "hr_coverage_percent": 0},
                {"activity_ref": f"strength-{i}", "sport": "WeightTraining", "local_date": day,
                 "recording_time_s": 14400},
            ])
        self.calendar = {"generation_id": "pinned-generation", "revision": 5, "activities": rows,
            "snapshot_payload": {"load_history": {
                "period_start": dates[-1], "period_end": dates[0],
                "quality": {"processed_activities": 8, "limited_activities": 4,
                            "excluded_activities": 0, "no_activity_days": 24, "warnings": []},
                "daily": [{"date": day, "zone": f"Z{i}", "effective_load": 0}
                          for day in dates for i in range(1, 6)],
                "activities": []}}}

    def athlete_settings(self, alias):
        assert alias == "ath-test"
        self.reads.append("settings")
        # The independent athlete-settings default must not replace PROFILE.
        return SimpleNamespace(timezone="Europe/Sofia", hrmax_bpm=190,
                               zone_bounds_bpm=BOUNDS, training_experience_years=0)

    def active_activity_calendar(self, alias, start, end):
        assert alias == "ath-test"
        self.reads.append("calendar")
        return deepcopy(self.calendar)

    def trainability_summaries(self, alias, keys):
        assert alias == "ath-test"
        self.reads.append("summaries")
        return {key: deepcopy(self.summaries[key]) for key in keys}

    def _request(self, method, path, **kwargs):
        assert method == "GET" and not kwargs
        assert "athlete_alias=eq.ath-test" in path
        if path.startswith("/onflows_management_entries?"):
            assert not self.profile_forbidden, "A reliable anchor must not read the fallback profile"
            self.reads.append("profile")
            return [] if self.profile is None else [{"kind": "PROFILE", "revision": 3,
                "entry_key": "management-profile-v1", "payload": deepcopy(self.profile)}]
        assert path.startswith("/onflows_model_entries?")
        self.reads.append("models")
        return deepcopy(self.entries)

    def _json(self, value):
        return value

    def add_test(self):
        self.entries.append({"kind": "SPEED_TEST", "entry_key": "measured-test", "revision": 1,
            "payload": {"sport": "Run", "day": (NOW.date()-timedelta(days=1)).isoformat(),
                "source": "MANUAL", "enabled": True, "duration_s": 600., "speed_kmh": 20.,
                "maximal": True, "comparable": True}})

    def add_index(self):
        row = self.calendar["activities"][0]
        row["latest_shadow_run_key"] = "run-shadow"
        def band(name):
            return {"name": name, "valid": True, "index": 5., "hr_seconds": 600}
        self.summaries["run-shadow"] = {"activity_ref": row["activity_ref"], "trainability_index": {
            "schema_version": SCHEMA_VERSION, "model_version": MODEL_VERSION,
            "comparison_key": activity_shadow_configuration_fingerprint(BOUNDS, 190, sport="Run"),
            "hrmax_bpm": 190, "zone_bounds_bpm": list(BOUNDS),
            "source_versions": {"vflat": speed_model_versions("Run")[0]},
            "signal_quality": {"status": "PASSED_SCREEN", "reason": None},
            "zones": [band(f"Z{i}") for i in range(1, 6)], "general": band("GENERAL")}}


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(service, "datetime", FixedDatetime)


def test_full_service_uses_management_years_raw_matching_sport_and_complete_dates():
    repo = Repository()
    for offset in (-29, 0, 1):
        repo.calendar["activities"].append({"activity_ref": f"outside-{offset}", "sport": "Run",
            "local_date": (NOW.date()+timedelta(days=offset)).isoformat(),
            "recording_time_s": 360000, "moving_time_s": 360000, "distance_m": 9999999})
    before = deepcopy(repo.calendar)
    model = service.speed_view(repo, "ath-test", "Run", duration_s=3600)
    estimate = model["initial_threshold"]
    assert estimate["status"] == "AVAILABLE"
    assert estimate["inputs"] == {"H": 8., "v": 12., "S": 4.}
    assert estimate["p_percent"] == pytest.approx(80.5476, abs=.0001)
    assert estimate["v_threshold_estimated_kmh"] == pytest.approx(14.8980, abs=.0001)
    assert estimate["window"] == {"start": "2026-09-30", "end_exclusive": "2026-10-28", "days": 28}
    assert estimate["history"]["activity_count"] == 8
    assert repo.reads.count("profile") == 1
    assert "summaries" not in repo.reads  # no extra raw per-activity reads
    assert repo.calendar == before
    assert model["source_generation_id"] == "pinned-generation"
    # The estimate cannot turn itself into an individual curve or evidence.
    assert model["tests"] == model["points"] == []
    assert model["active_test_count"] == 0
    assert model["prediction"] is None
    assert model["speed_zones"]["status"] == "UNAVAILABLE"
    assert model["speed_zones"]["zones"] == []
    assert not model["curve_metadata"]["absolute_speed_available"]
    assert estimate["used_for_zones"] is False and estimate["calibration_eligible"] is False


def test_explicit_21_day_window_reaches_adapter_without_changing_index_window():
    result = service.speed_view(Repository(), "ath-test", "Run", initial_history_days=21)
    estimate = result["initial_threshold"]
    assert estimate["window"]["days"] == 21
    assert estimate["window"]["start"] == "2026-10-07"
    assert estimate["history"]["activity_count"] == 6
    assert estimate["inputs"] == {"H": 8., "v": 12., "S": 4.}
    assert result["index_window"]["days"] == 40


@pytest.mark.parametrize("profile", [None, {}])
def test_absent_profile_years_remain_unknown_despite_athlete_settings_default(profile):
    repo = Repository()
    repo.profile = profile
    estimate = service.speed_view(repo, "ath-test", "Run")["initial_threshold"]
    assert estimate["status"] == "UNAVAILABLE"
    assert estimate["reason"] == "TRAINING_YEARS_REQUIRED"
    assert estimate["v_threshold_estimated_kmh"] is None


@pytest.mark.parametrize("anchor", ["TESTS", "INDEX"])
def test_reliable_anchor_supersedes_heuristic_and_avoids_profile_read(anchor):
    repo = Repository()
    (repo.add_test if anchor == "TESTS" else repo.add_index)()
    repo.profile_forbidden = True
    model = service.speed_view(repo, "ath-test", "Run")
    estimate = model["initial_threshold"]
    assert estimate["use_status"] == "SUPERSEDED"
    assert estimate["superseded_by"] == anchor
    assert estimate["v_threshold_estimated_kmh"] is None
    assert "profile" not in repo.reads
    assert model["speed_zones"]["status"] == "AVAILABLE"


def test_historical_capacity_path_does_not_load_or_produce_fallback_estimate():
    repo = Repository()
    repo.profile_forbidden = True
    model = service.speed_view(repo, "ath-test", "Run", _capacity_only=True)
    assert "initial_threshold" not in model
    assert "profile" not in repo.reads
    assert model["speed_zones"]["status"] == "UNAVAILABLE"


def test_one_read_session_reuses_profile_and_pinned_calendar_across_sports():
    repo = Repository()
    session = ReadSession(repo)
    first = service.speed_view(session, "ath-test", "Run")
    second = service.speed_view(session, "ath-test", "Ride")
    assert first["initial_threshold"]["status"] == "AVAILABLE"
    assert second["initial_threshold"]["reason"] == "SPORT_HISTORY_REQUIRED"
    assert repo.reads == ["settings", "calendar", "models", "profile"]
