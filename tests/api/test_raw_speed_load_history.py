from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from apps.api import actual_load_projection, model_service, speed_load
from biathlon import speed_equivalence
from tests.test_raw_speed_load import raw_profile, raw_stream
from tests.test_unified_zone_load import BOUNDS, profile
from vflat_b65.sports import speed_model_versions
from vflat_b65.load import VERSION as LOAD_VERSION


def test_direct_and_current_comparison_select_their_own_speed_coordinate():
    shadow = raw_stream()
    shadow.update(vflat_model_version=speed_model_versions("Run")[0], vflat_config_version=speed_model_versions("Run")[1])
    shadow["speed_load_summary"] = {"version": LOAD_VERSION}
    activity = {"activity_ref": "run", "sport": "Run", "local_date": "2026-10-28", "duration_min": 1.}
    raw = raw_profile()
    raw["parameters"] = speed_equivalence.prepare(raw)
    corrected = profile()
    corrected["parameters"] = speed_equivalence.prepare(corrected)
    for historical, current, observed in [(raw, corrected, 12.), (corrected, raw, 25.)]:
        result = speed_load._activity_record(activity, {"activity_ref": "run", "shadow_payload": shadow},
                                            historical, None, current["parameters"])
        expected = speed_equivalence.evaluate(historical["parameters"], observed)
        assert next(z for z in result["zones"] if z["zone"] == expected["zone"])["equivalent_minutes"] == pytest.approx(expected["coefficient"])
        comparison_observed = 12. if current.get("speed_role") == "RAW_MOVING_SPEED" else 25.
        c = speed_equivalence.evaluate(current["parameters"], comparison_observed)
        assert next(z for z in result["speed_equivalence_comparison"]["zones"] if z["zone"] == c["zone"])["equivalent_minutes"] == pytest.approx(c["coefficient"])


def test_raw_history_reader_is_selected_once_without_a_second_stream_download():
    shadow = raw_stream()
    shadow.update(vflat_model_version="old", vflat_config_version="old")
    rows = {"key": {"activity_ref": "run", "shadow_payload": shadow}}
    calls = []
    repo = SimpleNamespace(activity_speed_history_samples=lambda *args: calls.append("raw") or deepcopy(rows),
        activity_speed_exposure_samples=lambda *args: (_ for _ in ()).throw(AssertionError("Wrong coordinate reader")))
    activity = {"activity_ref": "run", "sport": "Run", "local_date": "2026-10-28", "duration_min": 1., "latest_shadow_run_key": "key"}
    p = raw_profile()
    context = {"zone_profile": p, "comparison_parameters": speed_equivalence.prepare(p),
        "minutes": {}, "sources": {}, "references": {}, "fingerprint": "raw"}
    result = speed_load._activity_records(repo, "athlete", [activity], [], SimpleNamespace(), None,
        comparison_by_sport={"Run": context["comparison_parameters"]},
        causal_contexts={("Run", date(2026,10,28)): context})
    assert calls == ["raw"]
    assert result[0]["classified_minutes"] == pytest.approx(1.)
    assert result[0]["initial_heuristic"] is True and result[0]["reason"] is None


def test_training_years_change_invalidates_reconstructed_load(monkeypatch):
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=200, leading_indicator="HEART_RATE")
    activity = {"activity_ref": "run", "sport": "Run", "local_date": "2026-10-28", "latest_shadow_run_key": "key"}
    inputs = {"alias": "raw-cache", "today": date(2026,10,29), "calendar": {"activities": [activity]},
        "entries": [], "initial_profile": {"training_experience_years": 4., "revision": 1}}
    calls = []
    def view(*args, **kwargs):
        calls.append(kwargs["_inputs"]["initial_profile"]["training_experience_years"])
        return {"sport": "Run", "speed_zones": raw_profile(), "status": "UNAVAILABLE"}
    monkeypatch.setattr(model_service, "speed_view", view)
    repo = SimpleNamespace(athlete_settings=lambda _: settings,
        activity_speed_history_samples=lambda *args: {"key": {"activity_ref": "run", "shadow_payload": raw_stream()}})
    source = {"period_start": "2026-10-28", "period_end": "2026-10-28", "activities": [{
        "activity_ref": "run", "sport": "Run", "date": "2026-10-28", "duration_min": 1.,
        "zones": [{"zone": z, "effective_load": 0.} for z in speed_equivalence.ZONES]}]}
    first = actual_load_projection.reconstruct_history(repo, "raw-cache", source, speed_inputs=inputs)
    inputs["initial_profile"].update(training_experience_years=8., revision=2)
    second = actual_load_projection.reconstruct_history(repo, "raw-cache", source, speed_inputs=inputs)
    assert calls == [4., 8.]
    assert first["load_selection"]["fingerprint"] != second["load_selection"]["fingerprint"]
    assert second["activities"][0]["load_source"]["initial_heuristic"] is True


def test_speed_zone_exposure_and_planning_estimate_share_raw_coordinate():
    from apps.api import speed_zone_history
    p = raw_profile()
    shadow = raw_stream()
    shadow.update(vflat_model_version="obsolete", vflat_config_version="obsolete")
    rows = {"key": {"activity_ref":"run", "shadow_payload":shadow}}
    activity = {"activity_ref":"run", "sport":"Run", "local_date":"2026-10-28",
        "duration_min":1.,"elapsed_time_s":60.,"latest_shadow_run_key":"key"}
    repo = SimpleNamespace(active_activity_calendar=lambda *args: {
        "activities":[activity], "generation_id":"g", "revision":1},
        activity_speed_history_samples=lambda *args:rows,
        activity_speed_exposure_samples=lambda *args:(_ for _ in ()).throw(AssertionError("Wrong reader")))
    view = {"sport":"Run", "speed_role":"RAW_MOVING_SPEED", "initial_heuristic":True,
        "speed_zones":p,"source_generation_id":"g","source_revision":1}
    report = speed_zone_history.exposure_history(repo,"athlete",view,date(2026,10,29))
    assert report["speed_role"] == "RAW_MOVING_SPEED" and report["initial_heuristic"] is True
    assert report["classified_minutes"] == pytest.approx(1.)
    assert report["estimated_descent_minutes"] == 0.
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=200)
    mapping, reason = speed_zone_history._mapping(view,settings,date(2026,10,29),"Run")
    assert reason is None
    estimate, reason = speed_zone_history._estimate(shadow,mapping,missing_minutes=1.,coverage=0.,elapsed=60.)
    assert reason is None and estimate["covered_missing_minutes"] == pytest.approx(1.)
    assert estimate["initial_heuristic"] is True and estimate["paired_lag_seconds"] == 0
    assert estimate["provenance"]["speed"] == "RAW_MOVING_SPEED"
    assert speed_zone_history._mapping(view,settings,date(2026,10,29),"TrailRun")[0] is None
