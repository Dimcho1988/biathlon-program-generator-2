from copy import deepcopy
from types import SimpleNamespace

import pytest

from apps.api.activity_shadow_pipeline import compute_activity_shadow
from apps.api.hr_recalculation import recalibrate_shadow
from biathlon.hr_zones import bounds_from_hrmax
from intervals_inspector.stream_normalizer import NormalizerInput, normalize_stream_intervals


@pytest.mark.parametrize("sport", ["Run", "VirtualRun", "NordicSki", "RollerSki", "Ride"])
@pytest.mark.parametrize("recording", ["regular", "smart", "gap", "missing_hr"])
def test_local_hr_correction_matches_full_model_without_speed_recomputation(monkeypatch, sport, recording):
    from apps.api import activity_shadow_pipeline
    offsets = list(range(0, 960, 4 if recording == "smart" else 1))
    if recording == "gap":
        offsets = [t for t in offsets if not 320 <= t < 360]
    normalized = normalize_stream_intervals(NormalizerInput(offsets=offsets, metrics={
        "heartrate": [None if recording == "missing_hr" and 300 < t < 450 else 120+60*(t%120)/120 for t in offsets],
        "velocity_smooth": [4.+(t%120)/300 for t in offsets],
        "gradient": [-5. if 400 < t < 600 else 3. for t in offsets],
        "altitude": [100.+t*.01 for t in offsets], "distance": [t*4. for t in offsets]}))
    detail = {"type": sport, "start_date": "2026-11-01T08:00:00Z", "moving_time": 960., "elapsed_time": 960.}
    def settings(hr):
        return SimpleNamespace(hrmax_bpm=hr, zone_bounds_bpm=bounds_from_hrmax(hr, (45,72,79,89,94,100)))
    old, new = settings(193), settings(188)
    immutable, before = compute_activity_shadow(detail=detail, normalized=normalized,
        zone_bounds_bpm=old.zone_bounds_bpm, explicit_hrmax_bpm=old.hrmax_bpm)
    _, expected = compute_activity_shadow(detail=detail, normalized=normalized,
        zone_bounds_bpm=new.zone_bounds_bpm, explicit_hrmax_bpm=new.hrmax_bpm)
    def unexpected(*args, **kwargs):
        raise AssertionError("Unchanged speed must not be recomputed")
    monkeypatch.setattr(activity_shadow_pipeline, "run_vflat_b65_shadow", unexpected)
    original = deepcopy(before)
    actual = recalibrate_shadow(immutable, before, detail=detail, settings=new)
    assert before == original
    assert actual == expected


@pytest.mark.parametrize("personal_calibration", [False, True])
def test_local_month_rebuild_matches_full_refresh_and_keeps_prefix(monkeypatch, personal_calibration):
    from dataclasses import replace
    from datetime import date
    from apps.api.cloud import AthleteModelSettings, InMemorySnapshotRepository
    from apps.api.real_service import refresh
    from apps.api.hr_recalculation import refresh_hr_calibration
    from apps.api import model_service
    from apps.api.trainability_history import history_from_calendar
    from tests.api.test_real_cloud_pipeline import Client, ENV
    from intervals_inspector.intervals_client import IntervalsResponse
    from tests.api.test_hr_settings_history import calibration

    class Recordings(Client):
        def get_activities_result(self, *args):
            return IntervalsResponse(200, [{"id": day, "start_date_local": day+"T08:00:00"}
                for day in (f"2026-08-{day:02d}" for day in range(7, 16))])
        def get_activity_result(self, key, **kwargs):
            return IntervalsResponse(200, {"id": key, "type": "Run", "start_date_local": key+"T08:00:00",
                "elapsed_time": 1020, "moving_time": 960, "icu_recording_time": 1020, "distance": 3840})
        def get_streams_result(self, key):
            return IntervalsResponse(200, [{"type": "time", "data": list(range(961))},
                {"type": "heartrate", "data": [130.+(t%120)/2 for t in range(961)]},
                {"type": "velocity_smooth", "data": [4.]*961}, {"type": "gradient", "data": [0.]*961}])

    class Repository(InMemorySnapshotRepository):
        def __init__(self):
            super().__init__()
            self.read_dates = []
            if personal_calibration:
                self._request = lambda *args, **kwargs: None
        def iter_hr_calibration_recordings(self, alias, selected):
            for row in selected:
                self.read_dates.append(row["local_date"])
                run = next(r for r in self._activity_runs[(alias, row["activity_ref"])]
                           if r["run_key"] == row["latest_shadow_run_key"])
                original = self._activity_inputs[(alias, row["activity_ref"], run["result_payload"]["input_hash"])]
                yield row, deepcopy(original), deepcopy(run["result_payload"])
        def publish_recalibrated_shadow(self, *, athlete_alias, activity_ref, input_key, derived_payload):
            original = self._activity_inputs[(athlete_alias, activity_ref, derived_payload["input_hash"])]
            return self.publish_activity_shadow(athlete_alias=athlete_alias, activity_ref=activity_ref,
                                                input_payload=original, derived_payload=derived_payload)

    old = AthleteModelSettings(bounds_from_hrmax(193,(45,72,79,89,94,100)), "UTC", 193,
        "AUTOMATIC_HRMAX", (45,72,79,89,94,100), hrmax_source="MANUAL",
        hr_history=(calibration(193,"0001-01-01"),), hr_settings_revision="old")
    new = replace(old, hrmax_bpm=188, zone_bounds_bpm=bounds_from_hrmax(188,(45,72,79,89,94,100)),
        hr_history=(calibration(193,"0001-01-01"),calibration(188,"2026-08-14")),
        hr_recompute_from="2026-08-14", hr_settings_revision="new")
    def speed_inputs(repository, alias):
        calendar = {"generation_id": "base", "activities": list(repository._activity_catalog.values()),
                    "snapshot_payload": repository.latest(alias)}
        return {"alias": alias, "settings": repository.settings, "today": date(2026,8,15), "calendar": calendar,
                "entries": [], "initial_profile": {"training_experience_years": 3},
                "history": history_from_calendar(repository, alias, calendar)}
    monkeypatch.setattr(model_service, "speed_view_inputs", speed_inputs)
    if not personal_calibration:
        monkeypatch.setattr(model_service, "speed_view", lambda repository, alias, sport, **kwargs: {"sport": sport})
    repo = Repository()
    repo.settings = old
    refresh(repo, environ=ENV, client=Recordings(), period_end=date(2026,8,15), athlete_settings=old)
    before = repo.latest("pilot")
    old_catalog = deepcopy(repo._activity_catalog)
    for row in repo._activity_catalog.values():
        row["input_key"] = repo._activity_runs[("pilot",row["activity_ref"])][-1]["input_key"]
    repo.settings = new
    refresh_hr_calibration(repo, athlete_alias="pilot", athlete_settings=new, base_generation_id="base")
    local = repo.latest("pilot")
    assert repo.read_dates == ["2026-08-14", "2026-08-15"]
    assert local["wellness_calendar"] == before["wellness_calendar"]
    assert local["load_history"]["hr_settings_revision"] == "new"
    for key in ("daily", "activities"):
        assert [r for r in local["load_history"][key] if r["date"] < "2026-08-14"] == [
            r for r in before["load_history"][key] if r["date"] < "2026-08-14"]
    for key, row in old_catalog.items():
        if row["local_date"] < "2026-08-14":
            assert repo._activity_catalog[key]["latest_shadow_run_key"] == row["latest_shadow_run_key"]
            assert repo._activity_catalog[key]["latest_canonical_run_key"] == row["latest_canonical_run_key"]
    # Independent complete provider calculation under the same dated settings.
    full_repo = Repository()
    full_repo.settings = new
    full_repo._activity_identity = deepcopy(repo._activity_identity)
    refresh(full_repo, environ=ENV, client=Recordings(), period_end=date(2026,8,15), athlete_settings=new)
    full = full_repo.latest("pilot")
    def same(actual, expected):
        if isinstance(expected, float):
            assert actual == pytest.approx(expected, rel=1e-12, abs=1e-10)
        elif isinstance(expected, dict):
            assert actual.keys() == expected.keys()
            for key in expected:
                same(actual[key], expected[key])
        elif isinstance(expected, list):
            assert len(actual) == len(expected)
            for a, e in zip(actual, expected, strict=True):
                same(a, e)
        else:
            assert actual == expected
    for key in ("daily", "zones", "activities"):
        same(local["load_history"][key], full["load_history"][key])
    same(local["training_status"]["zones"], full["training_status"]["zones"])
    same(local["recovery_history"], full["recovery_history"])


@pytest.mark.parametrize("wrong_input", [False, True])
def test_stored_recordings_are_scoped_and_pinned_and_raw_inputs_are_never_rewritten(wrong_input):
    from apps.api.oauth_store import SupabasePilotRepository, PersistentStoreFailure
    repository = object.__new__(SupabasePilotRepository)
    calls = []
    alias, ref, key, input_key = "ath-one", "act_"+"1"*32, "a"*64, "b"*64
    input_payload = {"input_hash": "c"*64, "samples": []}
    derived = {"result_hash": "d"*64, "schema_version": "activity-shadow-derived-v3"}
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "POST":
            return None
        assert "athlete_alias=eq.ath-one" in path
        if "onflows_activity_derived_runs" in path:
            return [{"run_key": key, "activity_ref": ref, "input_key": input_key, "result_payload": derived}]
        return [{"input_key": input_key, "activity_ref": "other" if wrong_input else ref,
                 "input_payload": input_payload}]
    repository._request = request
    repository._json = lambda response: response
    repository._hydrate_history = lambda payload, kind, key: payload
    repository._decode_shadow = lambda payload, key: payload
    activities = [{"activity_ref": ref, "latest_shadow_run_key": key, "input_key": input_key}]
    if wrong_input:
        with pytest.raises(PersistentStoreFailure, match="input mismatch"):
            list(repository.iter_hr_calibration_recordings(alias, activities))
        return
    assert list(repository.iter_hr_calibration_recordings(alias, activities)) == [(activities[0], input_payload, derived)]
    repository.publish_recalibrated_shadow(athlete_alias=alias, activity_ref=ref,
                                           input_key=input_key, derived_payload=derived)
    mutations = [(path, args) for method, path, args in calls if method == "POST"]
    assert len(mutations) == 1 and mutations[0][0].startswith("/onflows_activity_derived_runs?")
    assert mutations[0][1]["json"]["input_key"] == input_key
    assert "input_payload" not in mutations[0][1]["json"]
