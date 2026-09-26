"""Regression checks for stale or incompatible inputs to both HR directions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from apps.api import model_service as service
from apps.api.activity_shadow_pipeline import activity_shadow_configuration_fingerprint
from apps.api.model_schemas import SpeedTestInput
from apps.api.trainability import MODEL_VERSION, SCHEMA_VERSION
from apps.api.trainability_history import admit_activities
from biathlon.speed_duration import calibrated
from vflat_b65 import MODEL_VERSION as VF_VERSION, CONFIG_VERSION as VF_CONFIG


NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
BOUNDS = (100, 125, 145, 160, 175, 190)
KEY = activity_shadow_configuration_fingerprint(BOUNDS, 190)
REF = "act_" + "1" * 32
ACTOR = UUID("11111111-1111-4111-8111-111111111111")


def index(value=5., seconds=600):
    def band(name):
        return {"name": name, "valid": True, "index": value, "hr_seconds": seconds}
    return {"schema_version": SCHEMA_VERSION, "model_version": MODEL_VERSION,
            "comparison_key": KEY, "hrmax_bpm": 190, "zone_bounds_bpm": list(BOUNDS),
            "source_versions": {"vflat": VF_VERSION},
            "signal_quality": {"status": "PASSED_SCREEN", "reason": None},
            "zones": [band(f"Z{i}") for i in range(1, 6)], "general": band("GENERAL")}


class Repository:
    def __init__(self):
        self.activities = []
        self.summaries = {}
        self.rows = []
        self.saved = []
        self.activity_day = NOW.date().isoformat()

    def athlete_settings(self, alias):
        assert alias == "ath-test"
        return SimpleNamespace(timezone="Europe/Sofia", hrmax_bpm=190, zone_bounds_bpm=BOUNDS)

    def add(self, name, days, *, sport="Run", value=5., seconds=600, changes=None):
        day = (NOW.date() + timedelta(days=days)).isoformat()
        self.activities.append({"activity_ref": name, "sport": sport, "local_date": day,
                                "start_at_utc": day + "T09:00:00Z", "latest_shadow_run_key": name})
        self.summaries[name] = {"activity_ref": name, "trainability_index": {
            **index(value, seconds), **(changes or {})}}

    def test(self, days=0, **extra):
        self.rows.append({"kind": "SPEED_TEST", "entry_key": str(len(self.rows)), "revision": 1,
                          "payload": {"sport": "Run", "day": (NOW.date() + timedelta(days=days)).isoformat(),
                                      "enabled": True, "duration_s": 600., "speed_kmh": 22.,
                                      "vflat_version": VF_VERSION, "vflat_config_version": VF_CONFIG, **extra}})

    def active_activity_calendar(self, alias, start, end):
        # Deliberately do not date-filter: the consumer must bound its inputs too.
        assert alias == "ath-test"
        return {"activities": deepcopy(self.activities), "snapshot_payload": {}}

    def trainability_summaries(self, alias, keys):
        assert alias == "ath-test"
        return {k: deepcopy(self.summaries[k]) for k in keys}

    def _request(self, method, path, **kwargs):
        if method == "GET":
            assert "athlete_alias=eq.ath-test" in path
            return self.rows
        self.saved.append(kwargs["json"])
        return {"saved": True, "revision": 1}

    def _json(self, value):
        return value

    def active_activity_view(self, alias, ref):
        assert alias == "ath-test" and ref == REF
        return {"catalog_payload": {"sport": "Run", "local_date": self.activity_day},
                "shadow_run_key": "a" * 64, "shadow_payload": {
                    "vflat_model_version": VF_VERSION, "vflat_config_version": VF_CONFIG,
                    "timeseries": [{"elapsed_s": t, "vflat_b65_kmh": 20., "grade_smoothed_pct": 0.}
                                   for t in range(721)]}}


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(service, "datetime", FixedDatetime)


def test_only_current_compatible_accepted_sport_indices_enter_exact_40_day_window():
    repo = Repository()
    repo.test()
    repo.add("boundary", -39, value=5.)
    repo.add("today", 0, value=5.4, seconds=1800)
    repo.add("too-old", -40, value=8.)
    repo.add("future", 1, value=9.)
    repo.add("other-sport", -1, sport="NordicSki", value=50.)
    repo.add("old-profile", -1, value=30., changes={"hrmax_bpm": 180})
    repo.add("old-config", -1, value=30., changes={"comparison_key": "old-config"})
    repo.add("bad-signal", -1, value=30., changes={"signal_quality": {"status": "EXCLUDED", "reason": "HR_SIGNAL_SUSPECT"}})
    repo.add("legacy", -1, changes={"model_version": "old-index"})
    original = deepcopy(repo.summaries)
    result = service.speed_view(repo, "ath-test", "Run")
    assert result["index_window"] == {"start": "2026-08-11", "end": "2026-09-19", "days": 40,
                                      "last_activity_date": "2026-09-19"}
    assert result["index_summary"]["Z3"] == pytest.approx({"index": 5.3, "count": 2, "seconds": 2400})
    assert result["index_admission"] == {"activities": 6, "used": 2, "excluded": 1, "refresh_required": 1, "incompatible": 2}
    assert all(a["day"] <= "2026-09-19" for a in result["activities"])
    assert repo.summaries == original and repo.saved == []


@pytest.mark.parametrize("change", [{"vflat_version": "old-model"}, {"vflat_config_version": "old-config"}])
def test_frozen_test_with_different_vflat_settings_cannot_use_current_index(change):
    repo = Repository()
    repo.test(**change)
    repo.add("current", -1)
    result = service.speed_view(repo, "ath-test", "Run", hr_bpm=150)
    assert result["status"] == "CALIBRATED" and result["active_test_count"] == 1
    assert result["index_summary"]["Z3"]["index"] is None
    assert result["index_admission"]["incompatible"] == 1
    assert result["prediction"]["hr_prediction_source"] == "EXPERT_MIDPOINT"
    assert "INCOMPARABLE_INDEX_CONFIGURATION" in result["warnings"]


def test_manual_flat_test_uses_current_index_and_both_directions_share_the_same_map():
    repo = Repository()
    repo.test(source="MANUAL", vflat_version=None, vflat_config_version=None)
    curve = calibrated([repo.rows[0]["payload"]])
    value = 100 * BOUNDS[3] / 190 / (curve.speed(3300) * 3.6)
    repo.add("current", -1, value=value)
    result = service.speed_view(repo, "ath-test", "Run", hr_bpm=160)
    prediction = result["prediction"]
    assert prediction["hr_prediction_source"] == "INDEX"
    assert prediction["duration_s"] == pytest.approx(3300)
    inverse = service.speed_view(repo, "ath-test", "Run", speed_kmh=prediction["speed_kmh"])
    assert inverse["prediction"]["estimated_hr_bpm"] == pytest.approx(160)
    assert "INCOMPARABLE_INDEX_CONFIGURATION" not in result["warnings"]


def test_no_recent_index_uses_explicit_expert_fallback_without_reaching_further_back():
    repo = Repository()
    repo.test()
    repo.add("old", -40)
    result = service.speed_view(repo, "ath-test", "Run", hr_bpm=155)
    assert result["index_window"]["last_activity_date"] is None
    assert result["index_admission"]["used"] == 0
    assert all(x["index"] is None and x["count"] == 0 for x in result["index_summary"].values())
    assert result["prediction"]["hr_prediction_source"] == "EXPERT_MIDPOINT"


def test_window_uses_athlete_local_day_in_both_prediction_directions(monkeypatch):
    class MidnightUTC(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 18, 22, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(service, "datetime", MidnightUTC)
    repo = Repository()
    repo.test()
    repo.add("current", 0)
    result = service.speed_view(repo, "ath-test", "Run")
    assert result["index_window"]["end"] == "2026-09-19"
    assert result["index_admission"]["used"] == 1


def test_test_calibration_window_is_90_dates_and_cannot_include_future_or_91st_day():
    repo = Repository()
    repo.test(-89)
    repo.test(-90, duration_s=1200, speed_kmh=40.)
    repo.test(1, duration_s=1800, speed_kmh=45.)
    result = service.speed_view(repo, "ath-test", "Run")
    assert result["test_window"] == {"start": "2026-06-22", "end": "2026-09-19"}
    assert result["active_test_keys"] == ["0"]
    assert "CONFLICTING_TESTS" not in result["warnings"]


@pytest.mark.parametrize("days,accepted", [(-90, False), (-89, True), (0, True), (1, False)])
def test_imported_test_save_uses_same_calendar_window(days, accepted):
    repo = Repository()
    repo.activity_day = (NOW.date() + timedelta(days=days)).isoformat()
    body = SpeedTestInput(activity_ref=REF, start_s=0, duration_s=600, maximal=True,
                          comparable=True, conditions="Flat course")
    if accepted:
        service.save_test(repo, "ath-test", body, ACTOR)
        assert len(repo.saved) == 1
    else:
        with pytest.raises(HTTPException) as error:
            service.save_test(repo, "ath-test", body, ACTOR)
        assert error.value.status_code == 422 and repo.saved == []


def test_history_outlier_screen_also_uses_exact_40_dates():
    repo = Repository()
    for i, offset in enumerate([-40, -6, -5, -4, -3, -2, -1]):
        repo.add(str(i), offset, value=5.)
    repo.add("target", 0, value=10.)
    rows = [{**a, "index": repo.summaries[a["activity_ref"]]["trainability_index"]} for a in repo.activities]
    last = admit_activities(rows)[-1]["index"]["admission"]
    assert last["status"] == "ACCEPTED"
    assert last["reference_status"] == "INSUFFICIENT_HISTORY"
    # Moving the seventh reference inside the window makes the outlier screen eligible.
    rows[0]["local_date"] = "2026-08-11"
    rows[0]["start_at_utc"] = "2026-08-11T09:00:00Z"
    assert admit_activities(rows)[-1]["index"]["admission"]["reason"] == "INDEX_OUTLIER"
