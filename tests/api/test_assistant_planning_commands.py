from copy import deepcopy

import pytest
from fastapi import HTTPException

from apps.api.assistant_planning_commands import prepare
from apps.api.management_schemas import ManagementProfile
from tests.api.test_management_schedule import body


class Repository:
    def __init__(self):
        self.profile = ManagementProfile.model_validate(body()).model_dump(mode="json")
        self.calendar = {"schema_version": "planning-calendar-v1", "events": [
            {"event_id": "race-main-0001", "event_type": "MAIN_RACE", "name": "Основен старт",
             "start_date": "2027-02-01", "end_date": "2027-02-01"},
            {"event_id": "camp-main-0002", "event_type": "CAMP", "name": "Лагер",
             "start_date": "2027-01-10", "end_date": "2027-01-20"},
        ]}

    def _request(self, method, path, **kwargs):
        assert method == "GET"
        if path.startswith("/onflows_management_entries?"):
            return [{"kind": "PROFILE", "entry_key": "management-profile-v1", "revision": 3,
                     "payload": deepcopy(self.profile)}]
        assert path.startswith("/onflows_athlete_settings?select=planning_calendar,calendar_revision")
        return [{"planning_calendar": deepcopy(self.calendar), "calendar_revision": 4}]

    def _json(self, value):
        return value


def test_calendar_changes_only_identified_event_and_preserves_unedited_event():
    repo = Repository()
    original = deepcopy(repo.calendar)
    result = prepare(repo, "athlete", "CALENDAR_EVENT", "race-main-0001",
                     {"start_date": "2027-02-10", "end_date": "2027-02-10"}, 4)
    by_id = {e["event_id"]: e for e in result["payload"]["events"]}
    assert by_id["race-main-0001"]["start_date"] == "2027-02-10"
    assert by_id["camp-main-0002"] == original["events"][1]
    assert by_id["race-main-0001"]["name"] == "Основен старт"
    assert repo.calendar == original


def test_calendar_delete_is_exact_and_does_not_remove_other_events():
    result = prepare(Repository(), "athlete", "CALENDAR_EVENT", "race-main-0001", {"delete": True}, 4)
    assert [e["event_id"] for e in result["payload"]["events"]] == ["camp-main-0002"]
    with pytest.raises(HTTPException) as exc:
        prepare(Repository(), "athlete", "CALENDAR_EVENT", "missing-event", {"delete": True}, 4)
    assert exc.value.status_code == 404


def test_explicit_group_update_moves_only_exact_copies_and_preserves_their_ids():
    repo = Repository()
    duplicate = {**deepcopy(repo.calendar["events"][0]), "event_id": "race-copy-0003"}
    other = {**deepcopy(duplicate), "event_id": "race-other-0004", "name": "Друг старт"}
    repo.calendar["events"].extend([duplicate, other])
    original = deepcopy(repo.calendar)
    patch = {"start_date": "2027-01-09", "end_date": "2027-01-10", "include_identical_copies": True}
    result = prepare(repo, "athlete", "CALENDAR_EVENT", "race-main-0001", patch, 4)
    by_id = {e["event_id"]: e for e in result["payload"]["events"]}
    assert len(by_id) == 4
    for identifier in ("race-main-0001", "race-copy-0003"):
        assert by_id[identifier]["start_date"] == "2027-01-09" and by_id[identifier]["end_date"] == "2027-01-10"
        assert "include_identical_copies" not in by_id[identifier]
    assert by_id["race-other-0004"] == other
    assert by_id["camp-main-0002"] == original["events"][1]
    assert repo.calendar == original
    # Omitting the group flag retains exact single-record semantics.
    single = prepare(repo, "athlete", "CALENDAR_EVENT", "race-main-0001", {"name": "Промяна"}, 4)
    assert next(e for e in single["payload"]["events"] if e["event_id"] == duplicate["event_id"]) == duplicate


@pytest.mark.parametrize("key,patch,status", [
    ("new-event", {"include_identical_copies": True, "name": "Старт"}, 409),
    ("race-main-0001", {"include_identical_copies": True, "name": "Старт"}, 409),
    ("race-main-0001", {"include_identical_copies": False, "name": "Старт"}, 422),
    ("race-main-0001", {"include_identical_copies": True, "delete": True}, 422),
])
def test_group_update_requires_current_exact_copies_and_cannot_expand_deletion(key, patch, status):
    with pytest.raises(HTTPException) as error:
        prepare(Repository(), "athlete", "CALENDAR_EVENT", key, patch, 4)
    assert error.value.status_code == status


@pytest.mark.parametrize("patch", [{"start_date": "2027-02-10"}, {"unknown": 1},
                                      {"delete": True, "name": "x"}, {"delete": False}])
def test_invalid_calendar_change_cannot_prepare_a_write(patch):
    with pytest.raises(HTTPException) as exc:
        prepare(Repository(), "athlete", "CALENDAR_EVENT", "race-main-0001", patch, 4)
    assert exc.value.status_code == 422


def test_profile_patch_changes_effective_controls_and_preserves_mode_and_science():
    repo = Repository()
    repo.profile["adaptation_mode"] = "REVIEW"
    original = deepcopy(repo.profile)
    result = prepare(repo, "athlete", "MANAGEMENT_PROFILE", "management-profile-v1",
                     {"planning_controls": {"sessions_per_week": 9}}, 3)
    assert result["payload"]["planning_controls"]["sessions_per_week"] == 9
    assert result["payload"]["adaptation_mode"] == "REVIEW"
    assert result["payload"]["individual_learning"] == original["individual_learning"]
    assert result["payload"]["building_fraction"] == original["building_fraction"]
    assert repo.profile == original


@pytest.mark.parametrize("patch", [{"planning_controls": {"unknown_fraction": .6}},
                                      {"maintenance_fraction": .7}])
def test_unknown_or_derived_fields_are_not_chat_inputs(patch):
    with pytest.raises(HTTPException) as exc:
        prepare(Repository(), "athlete", "MANAGEMENT_PROFILE", "management-profile-v1", patch, 3)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("patch", [{"adaptation_mode": "AUTO"}, {"individual_learning": {"mode": "CONTROL"}},
                                      {"building_fraction": .7}, {"planning_controls": None}, {"taper_days": 10}, {"strength_enabled": True}])
def test_form_profile_controls_use_same_validator_in_chat(patch):
    repo = Repository()
    result = prepare(repo, "athlete", "MANAGEMENT_PROFILE", "management-profile-v1", patch, 3)
    assert result["expected_revision"] == 3
    for key, value in patch.items():
        if isinstance(value, dict):
            assert all(result["payload"][key][field] == changed for field, changed in value.items())
        else:
            assert result["payload"][key] == value


def test_double_threshold_keeps_canonical_age_and_key_session_validation():
    repo = Repository()
    repo.profile["age_years"] = 17
    with pytest.raises(HTTPException) as exc:
        prepare(repo, "athlete", "MANAGEMENT_PROFILE", "management-profile-v1",
                {"planning_controls": {"double_threshold_days": [2]}}, 3)
    assert exc.value.status_code == 422


def test_note_or_empty_profile_changes_are_not_writes():
    for patch in ({}, {"note": "харесва ми"}):
        with pytest.raises(HTTPException) as exc:
            prepare(Repository(), "athlete", "MANAGEMENT_PROFILE", "management-profile-v1", patch, 3)
        assert exc.value.status_code == 422


def test_calendar_snapshot_reads_content_and_revision_in_one_query():
    from tests.api.test_planning_settings_batch import repository, settings_row
    row = {**settings_row(), "calendar_revision": 9}
    repo, calls = repository({"athlete": row})
    snapshot = repo.athlete_planning_calendar_snapshot("athlete")
    assert snapshot["revision"] == 9
    assert snapshot["calendar"].to_payload() == row["planning_calendar"]
    assert len(calls) == 1
    assert calls[0].url.params["select"] == "planning_calendar,calendar_revision"


def test_calendar_snapshot_without_revision_cannot_enable_a_form_write():
    from tests.api.test_planning_settings_batch import repository, settings_row
    from apps.api.oauth_store import PersistentStoreFailure
    repo, _ = repository({"athlete": settings_row()})
    with pytest.raises(PersistentStoreFailure):
        repo.athlete_planning_calendar_snapshot("athlete")


def test_method_override_edits_preserve_others_and_explicit_reset_clears():
    from biathlon import method_library
    identifiers = [row["id"] for row in method_library.entries()][:2]
    repo = Repository()
    repo.profile["method_library"] = {"enabled": True, "overrides": {key: {"enabled": False} for key in identifiers}}
    result = prepare(repo, "athlete", "MANAGEMENT_PROFILE", "management-profile-v1",
        {"method_library": {"overrides": {identifiers[0]: None}}}, 3)
    assert result["payload"]["method_library"]["overrides"] == {identifiers[1]: {"enabled": False}}
    reset = prepare(repo, "athlete", "MANAGEMENT_PROFILE", "management-profile-v1",
        {"method_library": {"overrides": {}}}, 3)
    assert reset["payload"]["method_library"]["overrides"] == {}


def test_unknown_method_override_rejected_by_canonical_catalog():
    with pytest.raises(HTTPException) as exc:
        prepare(Repository(), "athlete", "MANAGEMENT_PROFILE", "management-profile-v1",
            {"method_library": {"overrides": {"invented-method": {"enabled": False}}}}, 3)
    assert exc.value.status_code == 422
