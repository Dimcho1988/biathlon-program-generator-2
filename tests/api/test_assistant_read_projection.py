"""Plan permissions retain prescribed training and controls, not raw evidence."""
from copy import deepcopy
import json

import pytest

from apps.api.assistant_read_projection import project_management, project_methods, project_profile


PRIVATE = "PRIVATE_OBSERVATION_MUST_NOT_APPEAR"


def test_profile_preserves_editable_scientific_controls_without_computed_evidence():
    profile = {"sport": "Run", "actual_sport": "Run", "discipline": "5000",
        "ant_references": [{"sport": "Run", "speed_kmh": 17, "source": "MANUAL", "private_evidence": PRIVATE}],
        "lactate_guidance_enabled": True, "lactate_profiles": [{"sport": "Run", "observations": PRIVATE}],
        "planning_controls": {"weekly_target_hours": 8, "capacity_policy": "MODEL_WITH_PRIOR", "private_evidence": PRIVATE},
        "individual_learning": {"mode": "SHADOW", "memory": PRIVATE},
        "method_library": {"enabled": True, "overrides": {"M1": {"enabled": False, "work_range": [60, 90], "private_evidence": PRIVATE}}},
        "_method_progression": {"measurements": PRIVATE}}
    source = {"configured": True, "revision": 4, "profile": profile, "history": {"observations": PRIVATE}}
    original = deepcopy(source)
    result = project_profile(source)
    assert result["revision"] == 4
    assert result["profile"]["planning_controls"] == {"weekly_target_hours": 8, "capacity_policy": "MODEL_WITH_PRIOR"}
    assert result["profile"]["method_library"]["overrides"]["M1"] == {"enabled": False, "work_range": [60, 90]}
    assert result["profile"]["ant_references"][0]["speed_kmh"] == 17
    assert "lactate_profiles" in result["profile"]
    assert PRIVATE not in json.dumps(result)
    assert source == original


def draft():
    return {"status": "DRAFT", "start_date": "2026-11-12", "end_date": "2026-11-18",
        "input_snapshot": {"profile_revision": 4, "response_revisions": PRIVATE, "analysis_revision": PRIVATE},
        "source": {"generation_id": PRIVATE}, "component_history": PRIVATE,
        "days": [{"date": "2026-11-12", "status": "TRAINING", "actual_minutes": PRIVATE,
            "readiness_before": PRIVATE, "explanation": PRIVATE,
            "sessions": [{"title": "Интервали", "total_minutes": 45,
                "dose_evidence": {"observations": PRIVATE},
                "blocks": [{"kind": "WORK", "target_speed_kmh": 18, "duration_s": 180, "observations": PRIVATE}]}]}],
        "long_term": {"baseline": PRIVATE, "individual_learning": PRIVATE,
            "weeks": [{"start_date": "2026-11-12", "end_date": "2026-11-18", "components": {
                "Z4": {"target_weekly_q": 25, "actual_index_7_40": PRIVATE}}, "actual_minutes": PRIVATE}]}}


@pytest.mark.parametrize("view", ["week", "active", "drafts", "overview"])
def test_all_plan_views_preserve_prescription_and_filter_nested_evidence(view):
    plan = draft()
    payload = {"plan": plan, "proposal": plan, "revision": 7} if view in {"week", "active"} else (
        {"drafts": [{"revision": 7, "payload": plan}]} if view == "drafts" else
        {"overview_active": {"revision": 7, "days": plan["days"], "outcomes": PRIVATE},
         "profile": {"configured": True, "profile": {"sport": "Run"}, "history": PRIVATE},
         "outlook": {"configured": True, "outlook": plan}, "drafts": {"drafts": []}})
    original = deepcopy(payload)
    result = project_management(payload, view)
    assert PRIVATE not in json.dumps(result)
    assert "Интервали" in json.dumps(result, ensure_ascii=False)
    assert "18" in json.dumps(result)
    assert payload == original


def test_outlook_keeps_planned_component_targets_but_not_actual_baseline():
    result = project_management({"configured": True, "outlook": draft()}, "outlook")
    assert result["outlook"]["long_term"]["weeks"][0]["components"]["Z4"] == {"target_weekly_q": 25}
    assert PRIVATE not in json.dumps(result)


def test_method_resolution_retains_prescribed_segments_and_excludes_model_anchor_evidence():
    method = {"id": "M1", "title": "Метод", "guidance": {"execution": "Равномерно"},
        "prescription": {"work": {"unit": "seconds", "range": [180, 240]}},
        "resolution": {"status": "READY", "target_speed_kmh": 18, "anchor": {"evidence": PRIVATE},
            "segments": [{"work_duration_seconds": 180, "target_speed_range_kmh": [17, 18], "model_evidence": PRIVATE}]}}
    source = {"version": "v1", "curve": PRIVATE, "method": method,
        "preferences": {"enabled": True, "overrides": {"M1": {"enabled": False}}}}
    original = deepcopy(source)
    result = project_methods(source)
    assert result["method"]["resolution"]["segments"] == [{"work_duration_seconds": 180, "target_speed_range_kmh": [17, 18]}]
    assert result["method"]["prescription"] == method["prescription"]
    assert result["preferences"] == source["preferences"]
    assert PRIVATE not in json.dumps(result)
    assert source == original


@pytest.mark.parametrize("job", [None, {"job_id": "abc", "status": "RUNNING", "stage": "SEARCH", "result_revision": None}])
def test_generation_job_status_is_not_lost(job):
    assert project_management({"job": job}, "generation-jobs") == {"job": job}


def test_metadata_and_pending_response_survive_projection():
    metadata = {"start_date": "2026-11-12", "latest_revision": 4, "revision": 4}
    assert project_management(metadata, "draft-metadata") == metadata
    pending = {"availability": "PENDING", "reason": "PREPARED_VIEW_PENDING", "view_kind": "management-state"}
    assert project_management(pending, "state") == pending


def test_compact_catalog_does_not_infer_a_missing_recipe_or_missing_sources():
    summary = {"id": "M1", "title": "Метод", "registry_kind": "SOURCE_RECIPE"}
    assert project_methods({"methods": [summary]})["methods"] == [summary]


@pytest.mark.parametrize('view', ['overview', 'active', 'week', 'outlook'])
def test_preparing_management_view_retains_safe_provenance_with_saved_content(view):
    preparation = {'status': 'UPDATING', 'prepared_at': '2026-11-12T12:00:00Z',
        'evaluated_on': '2026-11-12', 'reason': 'INPUTS_CHANGED', 'private_observation': PRIVATE}
    payload = {'preparation': preparation, 'revision': 7, 'plan': draft(), 'outlook': draft()}
    result = project_management(payload, view)
    assert result['preparation'] == {k: v for k, v in preparation.items() if k != 'private_observation'}
    assert PRIVATE not in json.dumps(result)
