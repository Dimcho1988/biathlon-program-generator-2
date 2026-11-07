"""One request-scoped settings row with unchanged lazy field validation."""
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from apps.api.cloud import AthletePlanningCalendar
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.read_session import ReadSession
from tests.api.test_cloud_boundaries import planning_profile
from tests.api.test_supabase_generation_rpc_contract import ENCRYPTION_KEY


READERS = (
    "athlete_settings", "athlete_planning_profile",
    "athlete_mesocycle_accent_preferences", "athlete_planning_calendar",
)


def settings_row():
    return {
        "hr_zone_bounds": [100, 120, 140, 160, 180, 200], "timezone": "Europe/Sofia",
        "hrmax_bpm": 200, "hr_zone_source": "MANUAL", "hr_zone_percentages": None,
        "planning_profile": planning_profile().to_payload(),
        "mesocycle_accent_preferences": {"schema_version": "mesocycle-accent-preferences-v1",
                                          "accent_mode": "HYBRID", "accent_limit": 3,
                                          "manual_components": ["Z5"]},
        "planning_calendar": {"schema_version": "planning-calendar-v1", "events": []},
    }


def repository(rows):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/rest/v1/onflows_athlete_settings"
        alias = request.url.params["athlete_alias"].removeprefix("eq.")
        if request.method == "PATCH":
            import json
            rows[alias].update(json.loads(request.content))
            return httpx.Response(204)
        assert request.method == "GET"
        selected = request.url.params["select"].split(",")
        row = rows.get(alias)
        return httpx.Response(200, json=[] if row is None else [
            {key: deepcopy(row[key]) for key in selected if key in row}])

    repo = SupabasePilotRepository(
        supabase_url="https://project.supabase.co", secret_key="sb_secret_server-key",
        encryption_key=ENCRYPTION_KEY,
        client=httpx.Client(transport=httpx.MockTransport(handler)), generation_reads=True,
    )
    return repo, requests


def test_settings_bundle_matches_all_four_individual_results_with_one_http_read():
    repo, calls = repository({"ath&one/two?": settings_row()})
    expected = {name: getattr(repo, name)("ath&one/two?") for name in READERS}
    assert len(calls) == 4
    calls.clear()
    scope = ReadSession(repo, planning_settings_bundle=True)
    # Calendar-first callers use the same bundle as physiology-first callers.
    actual = {name: getattr(scope, name)("ath&one/two?") for name in reversed(READERS)}
    assert actual == expected
    assert len(calls) == 1
    assert "athlete_alias=eq.ath%26one%2Ftwo%3F" in str(calls[0].url)
    assert set(calls[0].url.params["select"].split(",")) == set(settings_row())
    assert scope.athlete_settings("ath&one/two?") == expected["athlete_settings"]
    assert len(calls) == 1


def test_unrelated_read_sessions_keep_narrow_settings_projections_by_default():
    repo, calls = repository({"athlete": settings_row()})
    scope = ReadSession(repo)
    assert scope.athlete_settings("athlete").hrmax_bpm == 200
    assert set(calls[0].url.params["select"].split(",")) == {
        "hr_zone_bounds", "timezone", "hrmax_bpm", "hr_zone_source", "hr_zone_percentages", "model_preferences"}
    assert scope.athlete_planning_calendar("athlete") == AthletePlanningCalendar(())
    assert calls[1].url.params["select"] == "planning_calendar"


@pytest.mark.parametrize("inner_opt_in", [False, True])
def test_nested_read_sessions_share_the_optional_bundle_capability(inner_opt_in):
    repo, calls = repository({"athlete": settings_row()})
    inner = ReadSession(repo, planning_settings_bundle=inner_opt_in)
    outer = ReadSession(inner, planning_settings_bundle=True)
    assert outer.athlete_settings("athlete").hrmax_bpm == 200
    assert outer.athlete_planning_calendar("athlete") == AthletePlanningCalendar(())
    assert outer.athlete_planning_profile("athlete") == planning_profile()
    assert len(calls) == 1


def test_nested_context_reuses_settings_already_captured_by_the_page():
    row = settings_row()
    repo, calls = repository({"athlete": row})
    page = ReadSession(repo, planning_settings_bundle=True)
    assert page.athlete_settings("athlete").hrmax_bpm == 200
    row["hrmax_bpm"] = 201
    row["planning_calendar"] = None
    context = ReadSession(page, planning_settings_bundle=True)
    assert context.athlete_settings("athlete").hrmax_bpm == 200
    assert context.athlete_planning_calendar("athlete") == AthletePlanningCalendar(())
    assert len(calls) == 1
    assert repo.athlete_settings("athlete").hrmax_bpm == 201


@pytest.mark.parametrize("field,reader,healthy", [
    ("hr_zone_bounds", "athlete_settings", "athlete_planning_calendar"),
    ("planning_profile", "athlete_planning_profile", "athlete_settings"),
    ("mesocycle_accent_preferences", "athlete_mesocycle_accent_preferences", "athlete_settings"),
    ("planning_calendar", "athlete_planning_calendar", "athlete_settings"),
])
def test_bundle_keeps_each_fields_validation_lazy(field, reader, healthy):
    row = settings_row()
    row[field] = []
    repo, calls = repository({"athlete": row})
    with pytest.raises(PersistentStoreFailure) as direct:
        getattr(repo, reader)("athlete")
    expected = getattr(repo, healthy)("athlete")
    calls.clear()
    scope = ReadSession(repo, planning_settings_bundle=True)
    assert getattr(scope, healthy)("athlete") == expected
    with pytest.raises(PersistentStoreFailure, match=str(direct.value)):
        getattr(scope, reader)("athlete")
    assert len(calls) == 1


def test_settings_bundle_is_athlete_and_request_scoped_and_raw_write_checks_stay_fresh():
    first = settings_row()
    second = {**settings_row(), "timezone": "UTC"}
    repo, calls = repository({"one": first, "two": second})
    scope = ReadSession(repo, planning_settings_bundle=True)
    assert scope.athlete_settings("one").timezone == "Europe/Sofia"
    assert scope.athlete_settings("two").timezone == "UTC"
    assert len(calls) == 2
    first["planning_calendar"] = None
    # Already captured within this read phase; a raw final check stays live.
    assert scope.athlete_planning_calendar("one") == AthletePlanningCalendar(())
    assert repo.athlete_planning_calendar("one") is None
    assert ReadSession(repo, planning_settings_bundle=True).athlete_planning_calendar("one") is None
    repo.save_athlete_planning_calendar("one", AthletePlanningCalendar(()))
    assert repo.athlete_planning_calendar("one") == AthletePlanningCalendar(())
    with pytest.raises(AttributeError):
        scope.save_athlete_planning_calendar
    assert [r.method for r in calls] == ["GET", "GET", "GET", "GET", "PATCH", "GET"]


def test_missing_settings_and_optional_fields_keep_their_original_results():
    row = settings_row()
    for field in ("planning_profile", "mesocycle_accent_preferences", "planning_calendar"):
        row[field] = None
    repo, calls = repository({"configured": row})
    for alias in ("missing", "configured"):
        scope = ReadSession(repo, planning_settings_bundle=True)
        for name in READERS:
            assert getattr(scope, name)(alias) == getattr(repo, name)(alias)
    assert len(calls) == 10  # Two bundles and eight independent live checks.


def test_failed_bundle_read_is_retried_and_does_not_poison_the_read_phase(monkeypatch):
    repo, calls = repository({"athlete": settings_row()})
    original = repo.athlete_planning_settings_bundle
    attempts = []

    def bundle(alias):
        attempts.append(alias)
        if len(attempts) == 1:
            raise PersistentStoreFailure("Temporary store failure")
        return original(alias)

    monkeypatch.setattr(repo, "athlete_planning_settings_bundle", bundle)
    scope = ReadSession(repo, planning_settings_bundle=True)
    with pytest.raises(PersistentStoreFailure):
        scope.athlete_settings("athlete")
    assert scope.athlete_settings("athlete").hrmax_bpm == 200
    assert scope.athlete_planning_calendar("athlete") == AthletePlanningCalendar(())
    assert len(attempts) == 2 and len(calls) == 1


def test_repositories_without_bundle_keep_the_existing_per_method_fallback():
    calls = []
    values = {name: {"nested": [name]} for name in READERS}

    def reader(name):
        def read(alias):
            calls.append((name, alias))
            return values[name]
        return read

    repo = SimpleNamespace(**{name: reader(name) for name in READERS})
    scope = ReadSession(repo, planning_settings_bundle=True)
    for name in READERS:
        loaded = getattr(scope, name)("athlete")
        loaded["nested"].append("caller mutation")
        assert getattr(scope, name)("athlete") == values[name]
    assert calls == [(name, "athlete") for name in READERS]
