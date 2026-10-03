from __future__ import annotations

from apps.api.cloud import InMemorySnapshotRepository


def _payload(seed: str):
    return {
        "schema_version": "activity-model-input-v1",
        "input_hash": seed * 64,
        "samples": [{"hr_raw_bpm": 140.0}],
    }


def _result(seed: str):
    return {
        "schema_version": "activity-shadow-derived-v1",
        "result_hash": seed * 64,
        "vflat_model_version": "vflat_b65_dynamic_v1",
        "hrmod_model_version": "hrmod_mirror_area_shift_v4",
    }


def test_inputs_are_immutable_and_derived_runs_are_versioned_append_only() -> None:
    repository = InMemorySnapshotRepository()
    original = _payload("a")
    first_key = repository.publish_activity_shadow(
        athlete_alias="athlete-one",
        activity_ref="shadow-" + "1" * 32,
        input_payload=original,
        derived_payload=_result("b"),
    )
    original["samples"][0]["hr_raw_bpm"] = 10.0
    second_key = repository.publish_activity_shadow(
        athlete_alias="athlete-one",
        activity_ref="shadow-" + "1" * 32,
        input_payload=_payload("a"),
        derived_payload=_result("c"),
    )
    assert first_key != second_key
    assert repository.activity_shadow(
        "athlete-one", "shadow-" + "1" * 32
    )["result_hash"] == "c" * 64
    assert len(repository.activity_shadow_index("athlete-one")) == 1
