"""Private variants retain canonical structures, intensity and dose constraints."""
from copy import deepcopy

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.api.personal_method_schemas import PersonalMethod
from biathlon import method_dosing, method_library, method_registry, personal_methods, training_methods


def proposal(**changes):
    return {"id": "PERSONAL-eight-minutes", "name": "Моите осемминутни интервали",
            "base_method_id": "XC01", "work_range": [480., 480.], "repetitions": [4, 4], **changes}


def profile(*methods):
    return {"sport": "NordicSki", "actual_sport": "NordicSki", "athlete_level": "HIGHLY_TRAINED",
            "method_library": {"personal_methods": list(methods)}}


def hr_view():
    return {"sport": "NordicSki", "status": "UNAVAILABLE", "sport_hr_policy": {
        "sport_hrmax_bpm": 200, "sport_zone_bounds_bpm": [100, 150, 170, 180, 190, 200]}}


def test_variant_is_athlete_local_and_source_cache_is_unchanged():
    before = method_library.entries()
    athlete = profile(proposal())
    own = method_library.profile_entries(athlete)
    assert own[-1]["id"] == "PERSONAL-eight-minutes"
    assert own[-1]["personal_variant"]["scope"] == "ATHLETE"
    own[-1]["prescription"]["anchor"]["type"] = "TAMPERED"
    assert method_library.entries() == before
    assert all(not m["id"].startswith("PERSONAL-") for m in method_library.profile_entries(profile()))
    assert method_library.profile_entries(athlete)[-1]["prescription"]["anchor"]["type"] == "HRMAX"


def test_proposal_correction_is_deterministic_and_explicit_for_rest_work_and_reps():
    item = proposal(work_range=[600., 600.], repetitions=[10, 10], recovery_seconds=[30., 30.])
    normalized, reports = personal_methods.normalize_personal_methods([item], profile())
    assert normalized[0]["work_range"] == [480, 480]
    assert normalized[0]["repetitions"] == [5, 5]
    assert normalized[0]["recovery_seconds"] == [120, 120]
    assert {c["field"] for c in reports[0]["corrections"]} == {"work_range", "repetitions", "recovery_seconds"}
    assert reports[0]["status"] == "CORRECTED"
    assert personal_methods.normalize_personal_methods([item], profile()) == (normalized, reports)
    assert personal_methods.normalize_personal_methods(normalized, profile())[1][0]["status"] == "ACCEPTED"


def test_complete_declared_variant_prevents_independent_maxima_and_intensity_changes():
    base = {"id": "COUPLED", "prescription": {"structure": "INTERVALS",
        "work": {"unit": "seconds", "range": [120, 240]}, "repetitions": [4, 8],
        "speed_multiplier": [1., 1.1], "recovery": {"seconds": [60, 60]},
        "standard_variant": {"repetitions": 4, "work": {"unit": "seconds", "range": [240, 240]}, "speed_multiplier": [1., 1.]},
        "allowed_variants": [
            {"repetitions": 4, "work": {"unit": "seconds", "range": [240, 240]}, "speed_multiplier": [1., 1.]},
            {"repetitions": 6, "work": {"unit": "seconds", "range": [120, 120]}, "speed_multiplier": [1., 1.]},
            {"repetitions": 8, "work": {"unit": "seconds", "range": [240, 240]}, "speed_multiplier": [1.1, 1.1]},
        ]}}
    item = proposal(base_method_id="COUPLED", work_range=[240., 240.], repetitions=[8, 8])
    values, report = personal_methods.normalize_personal_methods([item], {}, [base], [])
    assert values[0]["repetitions"] == [4, 4]
    assert report[0]["status"] == "CORRECTED"
    resolved = personal_methods.source_methods(profile(values[0]), [base])[0]
    assert resolved["prescription"]["standard_variant"]["speed_multiplier"] == [1., 1.]
    assert len(resolved["prescription"]["allowed_variants"]) == 1


@pytest.mark.parametrize("changes", [
    {"base_method_id": "UNKNOWN"}, {"base_method_id": "END-ALT-Z1Z2-01"}, {"work_unit": "meters"},
])
def test_unknown_or_unproved_structure_and_units_are_rejected(changes):
    with pytest.raises(ValueError):
        personal_methods.normalize_personal_methods([proposal(**changes)], profile())


@pytest.mark.parametrize("extra", [{"speed_multiplier": [1., 2.]}, {"total_capacity_ratio": 20.},
                                  {"corrections": []}, {"structure": "ANY"}, {"recovery_seconds": [float("nan"), 30.]}])
def test_proposal_cannot_supply_coefficients_structures_or_fake_validation(extra):
    with pytest.raises(ValidationError):
        PersonalMethod.model_validate(proposal(**extra))


def test_personal_source_method_uses_shared_planner_resolution_and_whole_recovery_doses():
    athlete = profile(proposal(recovery_seconds=[150., 150.]))
    record = method_library.profile_entries(athlete)[-1]
    r = method_library.resolve(record, athlete, hr_view())
    assert r["status"] == "READY", r
    assert r["standard_work_seconds"] == 4 * 480
    assert r["maximum_work_seconds"] == 4 * 480
    assert r["work_options_seconds"] == [480, 960, 1440, 1920]
    assert r["target_hr_bpm"] == pytest.approx(182)
    assert r["recovery_seconds"] == 150
    executable = [m for m in method_registry.executable_methods(athlete, {"NordicSki": hr_view()})
                  if m.get("method_library_id") == record["id"]]
    assert executable
    assert executable[0]["method_library"]["resolution"]["standard_work_seconds"] == r["standard_work_seconds"]
    assert executable[0]["method_library"]["personal_variant"]["base_method_id"] == "XC01"


def test_legacy_personal_numeric_override_cannot_escape_source_dose_or_level_gate():
    athlete = profile(proposal())
    athlete["method_library"]["overrides"] = {"PERSONAL-eight-minutes": {
        "enabled": True, "repetitions": [100, 100], "work_range": [10000, 10000], "total_capacity_ratio": 20.}}
    record = method_library.profile_entries(athlete)[-1]
    r = method_library.resolve(record, athlete, hr_view())
    assert r["status"] == "READY", r
    assert r["maximum_work_seconds"] == 1920
    assert r["dosing"]["total_capacity_ratio"] != 20
    athlete["athlete_level"] = "BEGINNER"
    assert method_library.resolve(record, athlete, hr_view())["status"] == "INCOMPATIBLE"


def test_native_personal_bounds_survive_central_budget_resolution():
    item = proposal(base_method_id="END-CROSS-TRAIN-01-Z2", work_range=[1800., 1800.], repetitions=[1, 1])
    athlete = profile(item)
    method = training_methods.resolved_methods(athlete)[-1]
    original = next(m for m in training_methods.resolved_methods(profile()) if m["id"] == item["base_method_id"])
    assert method["capacity_method"] == original
    evidence = {"capacity_minutes": 300.}
    method_dosing.prepare_native_budget(method, evidence, athlete)
    assert method["min_work_min"] == method["max_work_min"] == 30
    assert method["position"] == original["position"]
    assert method["dosing"]["total_capacity_ratio"] == 1


def test_native_minimal_variant_survives_schema_round_trip_with_optional_nulls():
    from apps.api.management_schemas import MethodLibraryPreferences
    settings = MethodLibraryPreferences.model_validate({"personal_methods": [{
        "id": "PERSONAL-minimal", "name": "Лека работа", "base_method_id": "END-CROSS-TRAIN-01-Z2"}]}).model_dump(mode="json")
    athlete = {**profile(), "method_library": settings}
    method = training_methods.resolved_methods(athlete)[-1]
    assert method["id"] == "PERSONAL-minimal"
    assert method_registry.coach_card(method)["id"] == "PERSONAL-minimal"
    assert method["execution_limits"] == {"min_work_min": 15., "max_work_min": 90.}


def test_native_interval_keeps_effort_anchor_rest_ratio_and_central_capacity_ceiling():
    athlete = {**profile(), "age_years": 25, "training_experience_years": 6,
               "planning_controls": {"automatic_intervals": True}}
    item = proposal(base_method_id="ONFLOWS-CONTROLLED-Z4-V2", work_range=[240., 240.],
                    repetitions=[2, 2], recovery_seconds=[60., 60.])
    values, reports = personal_methods.normalize_personal_methods([item], athlete)
    assert values[0]["work_range"] == values[0]["recovery_seconds"] == [180, 180]
    assert reports[0]["status"] == "CORRECTED"
    athlete["method_library"] = {"personal_methods": values,
        "overrides": {item["id"]: {"total_capacity_ratio": 20.}}}
    method = training_methods.resolved_methods(athlete)[-1]
    interval = method["interval_template"]
    assert interval["anchor"] == {"type": "V5"}
    assert interval["total_capacity_ratio"] == 2.
    assert interval["work_seconds"] == interval["recovery_seconds"] == 180
    evidence = {"capacity_minutes": 2.}
    method_dosing.prepare_native_budget(method, evidence, athlete)
    assert method["max_work_min"] == 4.
    assert method["min_work_min"] == 6.  # Too little capacity: no complete permitted dose.
    assert method["interval_template"]["max_repetitions"] == 1


def test_plan_provenance_changes_with_private_method_without_changing_global_provenance():
    before = method_library.provenance()
    plain = method_registry.plan_catalog(profile())
    private = method_registry.plan_catalog(profile(proposal()))
    changed = method_registry.plan_catalog(profile(proposal(repetitions=[5, 5])))
    assert private["personal_method_ids"] == ["PERSONAL-eight-minutes"]
    assert len({p["personal_methods_sha256"] for p in [plain, private, changed]}) == 3
    assert method_library.provenance() == before


def test_preferences_validate_personal_membership_and_disallow_numeric_override():
    from apps.api.method_library_service import validate_preferences
    settings = {"personal_methods": [proposal(work_range=[600., 600.])], "overrides": {}}
    saved = validate_preferences(settings, profile())
    assert saved["personal_methods"][0]["work_range"] == [480, 480]
    settings["overrides"] = {"PERSONAL-eight-minutes": {"total_capacity_ratio": 20.}}
    with pytest.raises(HTTPException, match="accepts only enabled"):
        validate_preferences(settings, profile())


@pytest.mark.parametrize("identity", ["PERSONAL-eight-minutes", "PERSONAL-eight-minutes-SHORT",
                                    "LIB-PERSONAL-eight-minutes-GENERAL_PREPARATION"])
def test_preferences_cannot_remove_personal_method_referenced_by_structure(identity):
    from apps.api.method_library_service import validate_preferences
    athlete = profile(proposal())
    athlete["training_structure"] = [{"days": [{"sessions": [{"method_ids": [identity]}]}]}]
    with pytest.raises(HTTPException, match="запазена структура"):
        validate_preferences({"personal_methods": [], "overrides": {}}, athlete)
    assert validate_preferences(athlete["method_library"], athlete)["personal_methods"]


def test_older_preferences_client_preserves_existing_personal_methods(monkeypatch):
    from apps.api import method_library_service as service
    from apps.api.management_schemas import MethodLibraryPreferencesWrite
    saved = []
    athlete = profile(proposal())
    class Store:
        def __init__(self, _repository):
            pass
        def profile(self, _alias):
            return {"configured": True, "revision": 4, "profile": deepcopy(athlete)}
        def save_profile(self, alias, payload, revision, actor):
            saved.append(payload)
            return {"revision": 5}
    monkeypatch.setattr(service, "ManagementStore", Store)
    body = MethodLibraryPreferencesWrite.model_validate({"expected_revision": 4,
        "preferences": {"enabled": True, "overrides": {}}})
    result = service.save_preferences(object(), "athlete", body, object())
    assert result["preferences"]["personal_methods"][0]["id"] == "PERSONAL-eight-minutes"
    assert saved[0]["method_library"]["personal_methods"][0]["id"] == "PERSONAL-eight-minutes"
