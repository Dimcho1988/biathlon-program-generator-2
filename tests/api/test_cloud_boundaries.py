from datetime import date, datetime, timedelta, timezone
import math
from fastapi.testclient import TestClient
import pytest

from apps.api.cloud import (
    AthleteContext,
    AthleteMesocycleAccentPreferences,
    AthleteModelSettings,
    AthletePlanningCalendar,
    AthletePlanningCalendarEvent,
    AthletePlanningProfile,
    InMemorySnapshotRepository,
    normalize_wellness,
    planning_generation_context,
    service_token_valid,
    summarize_wellness_coverage,
)
from apps.api.hrmod import calculate_hrmod
from apps.api import main as api_main
from apps.api.main import app
from apps.api.oauth_service import OAuthFlowError
from biathlon.methodology import canonical_methodology


def test_athlete_configuration_validation_and_private_fingerprint():
    context = AthleteContext("pilot", "private-123", (90, 110, 130, 150, 170, 190), "Europe/Sofia", "intra_zone_linear_v1", "tref-300-180-70-20-20-v1", "recovery-v1")
    assert len(context.validate().fingerprint) == 64
    assert "private-123" not in context.fingerprint


def test_athlete_model_settings_require_real_boundaries_and_timezone():
    settings = AthleteModelSettings((100, 120, 140, 160, 180, 200), "Europe/Sofia")
    assert settings.validate() is settings
    with pytest.raises(ValueError, match="strictly increasing"):
        AthleteModelSettings((100, 120, 140, 140, 180, 200), "Europe/Sofia").validate()
    with pytest.raises(ValueError, match="IANA timezone"):
        AthleteModelSettings((100, 120, 140, 160, 180, 200), "Sofia").validate()


def planning_profile(**overrides):
    values = {
        "season_start": date(2026, 1, 1),
        "season_end": date(2026, 12, 31),
        "annual_target_hours": 600.0,
        "sessions_per_week": 9,
        "rest_days": (0,),
        "double_session_days": (2, 5),
        "long_session_day": 6,
        "intensity_days": (2, 5),
        "strength_days": (1, 4),
        "max_key_sessions_per_week": 3,
        "mesocycle_anchor_date": date(2026, 1, 1),
        "mesocycle_length_weeks": 4,
        "camp_default_accent_limit": 2,
        "double_threshold_enabled": False,
        "double_threshold_day": 2,
        "double_threshold_components": ("Z3", "Z4"),
    }
    values.update(overrides)
    return AthletePlanningProfile(**values)


def test_planning_profile_contains_only_individual_inputs_and_validates_structure():
    profile = planning_profile().validate()
    assert profile.to_payload()["schema_version"] == "planning-profile-v1"
    assert AthletePlanningProfile.from_mapping(profile.to_payload()) == profile
    assert "double_threshold_min_readiness" not in profile.to_payload()
    assert "annual_goal_influence" not in profile.to_payload()
    with pytest.raises(ValueError, match="session count"):
        planning_profile(sessions_per_week=13, rest_days=(0,)).validate()
    with pytest.raises(ValueError, match="cannot be a rest day"):
        planning_profile(
            double_threshold_enabled=True,
            double_threshold_day=0,
        ).validate()
    with pytest.raises(ValueError, match="fields are invalid"):
        AthletePlanningProfile.from_mapping(
            {**profile.to_payload(), "sessions_per_week": True}
        )


def test_mesocycle_accent_preferences_are_individual_and_do_not_guess_auto_slots():
    preferences = AthleteMesocycleAccentPreferences(
        accent_mode="HYBRID",
        accent_limit=3,
        manual_components=("Z5",),
    ).validate()

    assert preferences.to_payload() == {
        "schema_version": "mesocycle-accent-preferences-v1",
        "accent_mode": "HYBRID",
        "accent_limit": 3,
        "manual_components": ["Z5"],
    }
    assert preferences.resolution_preview() == {
        "fixed_components": ["Z5"],
        "automatic_slots": 2,
        "resolution_stage": "PLAN_GENERATION",
    }
    assert AthleteMesocycleAccentPreferences.from_mapping(
        preferences.to_payload()
    ) == preferences
    with pytest.raises(ValueError, match="require a manual component"):
        AthleteMesocycleAccentPreferences("MANUAL", 2, ()).validate()
    with pytest.raises(ValueError, match="cannot contain manual"):
        AthleteMesocycleAccentPreferences("AUTO", 2, ("Z1",)).validate()


def test_planning_calendar_is_versioned_canonical_and_never_infers_events():
    calendar = AthletePlanningCalendar(
        events=(
            AthletePlanningCalendarEvent(
                event_id="event-control-01",
                event_type="CONTROL_RACE",
                name="Контролен старт",
                start_date=date(2026, 10, 10),
                end_date=date(2026, 10, 10),
            ),
            AthletePlanningCalendarEvent(
                event_id="event-main-0001",
                event_type="MAIN_RACE",
                name="Основен старт",
                start_date=date(2026, 12, 12),
                end_date=date(2026, 12, 13),
            ),
        )
    ).validate()

    payload = calendar.to_payload()
    assert payload["schema_version"] == "planning-calendar-v1"
    assert [event["event_id"] for event in payload["events"]] == [
        "event-control-01",
        "event-main-0001",
    ]
    assert AthletePlanningCalendar.from_mapping(payload).to_payload() == payload
    with pytest.raises(ValueError, match="end precedes"):
        AthletePlanningCalendarEvent(
            "event-invalid-1",
            "CAMP",
            "Лагер",
            date(2026, 2, 2),
            date(2026, 2, 1),
        ).validate()


def test_planning_context_requires_a_real_future_main_race_and_stays_inactive():
    as_of = date(2026, 8, 19)
    calendar = AthletePlanningCalendar(
        events=(
            AthletePlanningCalendarEvent(
                "event-main-past",
                "MAIN_RACE",
                "Минал старт",
                date(2026, 7, 1),
                date(2026, 7, 1),
            ),
        )
    )
    incomplete = planning_generation_context(
        calendar=calendar,
        profile=planning_profile(),
        accent_preferences=AthleteMesocycleAccentPreferences("AUTO", 2, ()),
        training_snapshot={"training_status": {}},
        as_of=as_of,
    )
    assert incomplete["ready_for_generation"] is False
    assert incomplete["missing_inputs"] == ["FUTURE_MAIN_RACE"]
    assert incomplete["next_main_race"] is None
    assert incomplete["generator_status"] == "NOT_ACTIVE"

    future = AthletePlanningCalendar(
        events=(
            *calendar.events,
            AthletePlanningCalendarEvent(
                "event-main-future",
                "MAIN_RACE",
                "Реален основен старт",
                date(2026, 12, 12),
                date(2026, 12, 13),
            ),
        )
    )
    ready = planning_generation_context(
        calendar=future,
        profile=planning_profile(),
        accent_preferences=AthleteMesocycleAccentPreferences("AUTO", 2, ()),
        training_snapshot={"training_status": {}},
        as_of=as_of,
    )
    assert ready["ready_for_generation"] is True
    assert ready["missing_inputs"] == []
    assert ready["next_main_race"]["event_id"] == "event-main-future"
    assert ready["generator_status"] == "NOT_ACTIVE"


def test_wellness_preserves_missing_stale_invalid_and_units():
    result = normalize_wellness({"id": "2026-01-01", "sleepSecs": 28800, "sleepQuality": 4, "fatigue": float("nan"), "illness": False}, now=datetime(2026, 1, 5, tzinfo=timezone.utc))
    assert result["freshness"] == "stale"
    assert result["values"]["sleep_duration"] == {"value": 28800.0, "unit": "s", "state": "valid"}
    assert result["values"]["sleep_quality"] == {"value": 4.0, "unit": "score", "state": "valid"}
    assert result["values"]["fatigue"]["state"] == "invalid"
    assert result["values"]["stress"]["state"] == "missing"
    assert result["values"]["illness"]["value"] is False


def test_wellness_coverage_uses_distinct_days_and_never_persists_values():
    result = summarize_wellness_coverage(
        [
            {"id": "2026-01-01", "sleepSecs": 28800, "restingHR": 50},
            {"id": "2026-01-02", "sleepQuality": 4, "fatigue": 2, "hrv": 80},
            {"id": "2026-01-03", "sleepScore": 75, "fatigue": "bad", "soreness": 3},
            {"id": "2026-01-03", "fatigue": 4},
            {"id": "2026-01-06", "sleepSecs": 30000},
            {"id": "invalid", "stress": 2},
        ],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 5),
        now=datetime(2026, 1, 4, 12, tzinfo=timezone.utc),
    )

    assert result["schema_version"] == "wellness-coverage-v1"
    assert result["records_received"] == 4
    assert result["days_with_any_recognized_data"] == 3
    assert result["daily_presence_percent"] == 60.0
    assert result["recognized_field_coverage_percent"] == 10.0
    assert result["latest_observed_date"] == "2026-01-03"
    assert result["freshness"] == "fresh"
    assert result["affects_recovery"] is False
    assert result["unresolved_canonical_inputs"] == [
        "soreness_legs",
        "soreness_upper",
        "pain",
        "illness",
    ]
    by_field = {row["field"]: row for row in result["fields"]}
    assert by_field["sleep_quality"]["source_fields"] == ["sleepQuality"]
    assert by_field["sleep_score"]["source_fields"] == ["sleepScore"]
    assert by_field["sleep_quality"]["valid_days"] == 1
    assert by_field["sleep_score"]["valid_days"] == 1
    assert by_field["fatigue"]["valid_days"] == 2
    assert by_field["fatigue"]["invalid_days"] == 0
    assert "28800" not in repr(result)


def test_hrmod_is_deterministic_hr_only_and_finite():
    hr = [120, 121, None, 123, 124, 130, 131, 132, 133, 134]
    first = calculate_hrmod(hr)
    assert first == calculate_hrmod(hr)
    assert first["affects_final_decision"] is False and first["coverage"] == .9
    assert all(x is None or math.isfinite(x) for x in first["hrmod"])


def test_snapshot_atomic_replacement_retains_last_valid():
    repo = InMemorySnapshotRepository(); repo.replace("pilot", {"version": 1})
    assert repo.latest("pilot") == {"version": 1}


def test_real_endpoint_auth_and_no_snapshot(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    client = TestClient(app)
    assert client.get("/api/v2/real/training-status").status_code == 401
    assert client.get("/api/v2/real/training-status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v2/real/training-status", headers={"Authorization": "Bearer secret-value"}).status_code == 503
    assert client.get("/api/v2/real/load-history").status_code == 401
    assert client.get("/api/v2/real/volume-history").status_code == 401
    assert client.get("/api/v2/real/load-history", headers={"Authorization": "Bearer secret-value"}).status_code == 503
    assert client.get("/api/v2/real/completed-work").status_code == 401
    assert client.get("/api/v2/real/completed-work", headers={"Authorization": "Bearer secret-value"}).status_code == 503
    assert client.get("/api/v2/real/recovery-history").status_code == 401
    assert client.get("/api/v2/real/recovery-history", headers={"Authorization": "Bearer secret-value"}).status_code == 503


def test_planning_methodology_is_protected_shared_and_versioned(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    client = TestClient(app)

    assert client.get("/api/v2/planning/methodology").status_code == 401
    response = client.get(
        "/api/v2/planning/methodology",
        headers={"Authorization": "Bearer secret-value"},
    )

    assert response.status_code == 200
    assert response.json() == canonical_methodology()


def test_constant_time_boundary_semantics():
    assert service_token_valid("same", "same")
    assert not service_token_valid(None, "same")
    assert not service_token_valid("same", "")


def test_real_endpoint_selects_only_the_server_authenticated_athlete(monkeypatch):
    class Repository:
        def __init__(self):
            self.requested_alias = None

        def latest(self, athlete_alias):
            self.requested_alias = athlete_alias
            return None

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setenv("ONFLOWS_ATHLETE_ALIAS", "pilot")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)

    response = client.get(
        "/api/v2/real/training-status",
        headers={
            "Authorization": "Bearer secret-value",
            "X-OnFlows-Athlete-Alias": "ath-second-profile",
        },
    )

    assert response.status_code == 503
    assert repository.requested_alias == "ath-second-profile"


def test_athlete_settings_are_scoped_to_the_authenticated_profile(monkeypatch):
    class Connection:
        status = "CONNECTED"

    class Repository:
        def __init__(self):
            self.items = {}

        def connection(self, athlete_alias):
            return Connection()

        def athlete_settings(self, athlete_alias):
            return self.items.get(athlete_alias)

        def save_athlete_settings(self, athlete_alias, settings):
            self.items[athlete_alias] = settings

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer secret-value",
        "X-OnFlows-Athlete-Alias": "ath-second-profile",
    }
    body = {
        "hr_zone_bounds_bpm": [100, 120, 140, 160, 180, 200],
        "timezone": "Europe/Sofia",
    }

    saved = client.put("/api/v2/athlete/settings", headers=headers, json=body)
    loaded = client.get("/api/v2/athlete/settings", headers=headers)

    assert saved.status_code == 200
    assert loaded.json() == {"configured": True, **body}
    assert list(repository.items) == ["ath-second-profile"]


def test_planning_profile_is_scoped_and_rejects_inconsistent_structure(monkeypatch):
    class Repository:
        def __init__(self):
            self.profiles = {}

        def athlete_settings(self, athlete_alias):
            return AthleteModelSettings(
                (100, 120, 140, 160, 180, 200), "Europe/Sofia"
            )

        def athlete_planning_profile(self, athlete_alias):
            return self.profiles.get(athlete_alias)

        def save_athlete_planning_profile(self, athlete_alias, profile):
            self.profiles[athlete_alias] = profile

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer secret-value",
        "X-OnFlows-Athlete-Alias": "ath-second-profile",
    }
    body = planning_profile().to_payload()

    saved = client.put(
        "/api/v2/athlete/planning-profile", headers=headers, json=body
    )
    loaded = client.get("/api/v2/athlete/planning-profile", headers=headers)

    assert saved.status_code == 200
    assert loaded.json() == {"configured": True, "profile": body}
    assert list(repository.profiles) == ["ath-second-profile"]

    invalid = {
        **body,
        "double_threshold_enabled": True,
        "double_threshold_day": 0,
    }
    rejected = client.put(
        "/api/v2/athlete/planning-profile", headers=headers, json=invalid
    )
    assert rejected.status_code == 422
    assert repository.profiles["ath-second-profile"].to_payload() == body


def test_mesocycle_accent_preferences_are_scoped_and_require_a_profile(monkeypatch):
    class Repository:
        def __init__(self):
            self.profiles = {"ath-second-profile": planning_profile()}
            self.preferences = {}

        def athlete_planning_profile(self, athlete_alias):
            return self.profiles.get(athlete_alias)

        def athlete_mesocycle_accent_preferences(self, athlete_alias):
            return self.preferences.get(athlete_alias)

        def save_athlete_mesocycle_accent_preferences(
            self, athlete_alias, preferences
        ):
            self.preferences[athlete_alias] = preferences

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer secret-value",
        "X-OnFlows-Athlete-Alias": "ath-second-profile",
    }
    body = {
        "schema_version": "mesocycle-accent-preferences-v1",
        "accent_mode": "HYBRID",
        "accent_limit": 3,
        "manual_components": ["Z5"],
    }

    assert client.get(
        "/api/v2/athlete/mesocycle-accent-preferences"
    ).status_code == 401
    saved = client.put(
        "/api/v2/athlete/mesocycle-accent-preferences",
        headers=headers,
        json=body,
    )
    loaded = client.get(
        "/api/v2/athlete/mesocycle-accent-preferences", headers=headers
    )

    expected = {
        "configured": True,
        "preferences": body,
        "resolution": {
            "methodology_version": "onflows-canonical-v1",
            "fixed_components": ["Z5"],
            "automatic_slots": 2,
            "resolution_stage": "PLAN_GENERATION",
        },
    }
    assert saved.status_code == 200
    assert saved.json() == expected
    assert loaded.json() == expected
    assert list(repository.preferences) == ["ath-second-profile"]

    invalid = client.put(
        "/api/v2/athlete/mesocycle-accent-preferences",
        headers=headers,
        json={**body, "accent_mode": "AUTO"},
    )
    assert invalid.status_code == 422
    assert repository.preferences["ath-second-profile"].to_payload() == body

    missing_profile_headers = {
        **headers,
        "X-OnFlows-Athlete-Alias": "ath-missing-profile",
    }
    missing_profile = client.put(
        "/api/v2/athlete/mesocycle-accent-preferences",
        headers=missing_profile_headers,
        json={
            **body,
            "accent_mode": "MANUAL",
            "accent_limit": 1,
        },
    )
    assert missing_profile.status_code == 409


def test_planning_calendar_is_scoped_and_reports_generation_readiness(monkeypatch):
    class Repository:
        def __init__(self):
            self.calendars = {}

        def athlete_settings(self, athlete_alias):
            return AthleteModelSettings(
                (100, 120, 140, 160, 180, 200), "Europe/Sofia"
            )

        def athlete_planning_profile(self, athlete_alias):
            return planning_profile()

        def athlete_mesocycle_accent_preferences(self, athlete_alias):
            return AthleteMesocycleAccentPreferences("AUTO", 2, ())

        def athlete_planning_calendar(self, athlete_alias):
            return self.calendars.get(athlete_alias)

        def save_athlete_planning_calendar(self, athlete_alias, calendar):
            self.calendars[athlete_alias] = calendar

        def latest(self, athlete_alias):
            return {"training_status": {"athlete_id": athlete_alias}}

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer secret-value",
        "X-OnFlows-Athlete-Alias": "ath-second-profile",
    }
    main_date = date.today() + timedelta(days=60)
    body = {
        "schema_version": "planning-calendar-v1",
        "events": [
            {
                "event_id": "event-main-0001",
                "event_type": "MAIN_RACE",
                "name": "Основен старт",
                "start_date": main_date.isoformat(),
                "end_date": main_date.isoformat(),
            }
        ],
    }

    assert client.get("/api/v2/athlete/planning-calendar").status_code == 401
    saved = client.put(
        "/api/v2/athlete/planning-calendar", headers=headers, json=body
    )
    loaded = client.get("/api/v2/athlete/planning-calendar", headers=headers)

    assert saved.status_code == 200
    assert loaded.json() == saved.json()
    assert saved.json()["configured"] is True
    assert saved.json()["calendar"] == body
    assert saved.json()["context"]["ready_for_generation"] is True
    assert saved.json()["context"]["generator_status"] == "NOT_ACTIVE"
    assert saved.json()["context"]["next_main_race"]["event_id"] == "event-main-0001"
    assert list(repository.calendars) == ["ath-second-profile"]

    invalid = client.put(
        "/api/v2/athlete/planning-calendar",
        headers=headers,
        json={
            **body,
            "events": [{**body["events"][0], "end_date": "2026-01-01"}],
        },
    )
    assert invalid.status_code == 422


def test_login_ticket_is_consumed_through_the_protected_server_boundary(monkeypatch):
    class Repository:
        def __init__(self):
            self.tickets = {"one-time-ticket-value-with-32-chars": "ath-profile"}

        def consume_login_ticket(self, ticket):
            return self.tickets.pop(ticket, None)

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)
    body = {"ticket": "one-time-ticket-value-with-32-chars"}

    first = client.post(
        "/api/v2/session/exchange",
        headers={"Authorization": "Bearer secret-value"},
        json=body,
    )
    replay = client.post(
        "/api/v2/session/exchange",
        headers={"Authorization": "Bearer secret-value"},
        json=body,
    )

    assert first.status_code == 200
    assert first.json() == {"athlete_alias": "ath-profile"}
    assert replay.status_code == 401


def test_oauth_callback_exposes_only_safe_failure_stage(monkeypatch):
    monkeypatch.setenv("INTERVALS_CLIENT_ID", "client-id")
    monkeypatch.setenv("INTERVALS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "INTERVALS_REDIRECT_URI",
        "https://api.example.test/api/v2/integrations/intervals/callback",
    )
    monkeypatch.setenv("OAUTH_STATE_SECRET", "state-secret")
    monkeypatch.setenv("ONFLOWS_WEB_BASE_URL", "https://web.example.test")
    monkeypatch.setattr(api_main, "_repository", lambda: object())

    def fail_safely(*_args, **_kwargs):
        raise OAuthFlowError("provider detail remains private", stage="permissions")

    monkeypatch.setattr(api_main, "complete_authorization", fail_safely)
    response = TestClient(app).get(
        "/api/v2/integrations/intervals/callback?code=private&state=private",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://web.example.test/?intervals=error-permissions"
    )
    assert "provider detail" not in response.headers["location"]
