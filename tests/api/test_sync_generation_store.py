from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from apps.api.cloud import InMemorySnapshotRepository


ALIAS = "ath-generation-store"
ACTIVITY_REF = "act_" + "a" * 32


def _snapshot(marker: str, *, with_activity: bool = True) -> dict:
    activities = [{"activity_ref": ACTIVITY_REF}] if with_activity else []
    return {
        "schema_version": "athlete-snapshot-v1",
        "training_status": {
            "schema_version": "training-status-v1",
            "athlete_id": ALIAS,
            "as_of": "2026-08-27",
            "marker": marker,
        },
        "load_history": {
            "schema_version": "load-history-v2",
            "athlete_id": ALIAS,
            "period_start": "2026-07-18",
            "period_end": "2026-08-27",
            "activities": activities,
            "marker": marker,
        },
        "recovery_history": {
            "schema_version": "recovery-history-v1",
            "athlete_id": ALIAS,
            "period_start": "2026-07-18",
            "period_end": "2026-08-27",
            "marker": marker,
        },
        "wellness_calendar": [],
    }


def _activity(repository: InMemorySnapshotRepository, marker: str) -> dict:
    input_hash = ("1" if marker == "first" else "2") * 64
    result_hash = ("3" if marker == "first" else "4") * 64
    shadow_key = repository.publish_activity_shadow(
        athlete_alias=ALIAS,
        activity_ref=ACTIVITY_REF,
        input_payload={
            "schema_version": "activity-model-input-v1",
            "input_hash": input_hash,
            "samples": [{"timestamp": marker, "elapsed_s": 0}],
        },
        derived_payload={
            "schema_version": "activity-derived-v1",
            "result_hash": result_hash,
            "zone_summary": [
                {"zone_name": "Z1", "hrmod_final_seconds": 60}
            ],
        },
    )
    canonical_key = repository.store_canonical_activity_result(
        athlete_alias=ALIAS,
        activity_ref=ACTIVITY_REF,
        scientific_input_hash=("5" if marker == "first" else "6") * 64,
        result_payload={
            "schema_version": "activity-canonical-summary-v2",
            "model_version": "canonical-v1",
            "marker": marker,
        },
    )
    return {
        "activity_ref": ACTIVITY_REF,
        "start_at_utc": "2026-08-27T06:00:00+00:00",
        "local_date": "2026-08-27",
        "sport": "Run",
        "marker": marker,
        "latest_canonical_run_key": canonical_key,
        "latest_shadow_run_key": shadow_key,
    }


def _build_generation(
    repository: InMemorySnapshotRepository,
    *,
    marker: str,
    job_kind: str = "FULL_SYNC",
    activities: list[dict] | None = None,
    inherit_activities: bool = False,
) -> tuple[dict, dict]:
    enqueued = repository.enqueue_sync_job(
        athlete_alias=ALIAS,
        job_kind=job_kind,
        idempotency_key=(marker[0] * 64),
        request_payload={"as_of": "2026-08-27"},
    )
    claimed = repository.claim_sync_job(worker_id="test-worker", lease_seconds=300)
    assert claimed is not None and claimed["job_id"] == enqueued["job_id"]
    snapshot = _snapshot(marker)
    staged = repository.stage_analysis_generation(
        job_id=claimed["job_id"],
        generation_id=claimed["generation_id"],
        lease_token=claimed["lease_token"],
        snapshot_payload=snapshot,
        snapshot_hash=("9" if marker == "first" else "8") * 64,
        period_start=date(2026, 7, 18),
        period_end=date(2026, 8, 27),
        as_of=date(2026, 8, 27),
        provenance={"activity_set_hash": "7" * 64},
        activities=activities or [],
        inherit_activities=inherit_activities,
    )
    assert staged["outcome"] == "READY"
    activated = repository.activate_analysis_generation(
        job_id=claimed["job_id"],
        generation_id=claimed["generation_id"],
        lease_token=claimed["lease_token"],
    )
    assert activated["outcome"] == "ACTIVATED"
    return claimed, activated


def test_raw_catalog_row_pins_exact_shadow_input_and_hides_later_writes():
    repository = InMemorySnapshotRepository()
    first = _activity(repository, "first")
    first_claim, _ = _build_generation(
        repository, marker="first", activities=[first]
    )

    pinned = repository._generation_activities[
        (first_claim["generation_id"], ACTIVITY_REF)
    ]
    assert pinned["input_key"]
    assert pinned["shadow_run_key"] == first["latest_shadow_run_key"]

    later = _activity(repository, "later")
    repository.upsert_activity_catalog(ALIAS, [later])
    repository.replace(ALIAS, _snapshot("legacy-overwrite"))

    assert repository.latest(ALIAS)["training_status"]["marker"] == "first"
    detail = repository.activity_detail(ALIAS, ACTIVITY_REF)
    assert detail is not None and detail["marker"] == "first"
    assert detail["latest_shadow_run_key"] == first["latest_shadow_run_key"]
    assert repository.activity_series(ALIAS, ACTIVITY_REF)["series"][0][
        "timestamp"
    ] == "first"
    assert repository.activity_shadow(ALIAS, ACTIVITY_REF)["result_hash"] == "3" * 64
    view = repository.active_activity_view(ALIAS, ACTIVITY_REF)
    assert view is not None
    assert view["catalog_payload"]["marker"] == "first"
    assert view["series_payload"]["samples"][0]["timestamp"] == "first"
    assert view["shadow_payload"]["result_hash"] == "3" * 64


def test_patch_generation_shares_base_activity_set_without_copying_rows():
    repository = InMemorySnapshotRepository()
    activity = _activity(repository, "first")
    base_claim, _ = _build_generation(
        repository, marker="first", activities=[activity]
    )
    patch_claim, patch_activation = _build_generation(
        repository,
        marker="wellness",
        job_kind="WELLNESS_SYNC",
        inherit_activities=True,
    )

    patch = repository._analysis_generations[patch_claim["generation_id"]]
    assert patch["activity_set_generation_id"] == base_claim["generation_id"]
    assert not any(
        generation_id == patch_claim["generation_id"]
        for generation_id, _ in repository._generation_activities
    )
    assert patch_activation["active_revision"] == 2
    calendar = repository.active_activity_calendar(
        ALIAS, date(2026, 8, 27), date(2026, 8, 27)
    )
    assert calendar is not None
    assert calendar["activities"][0]["activity_ref"] == ACTIVITY_REF


def test_running_job_has_one_bounded_same_kind_successor_and_retry_supersedes_old():
    repository = InMemorySnapshotRepository()
    first = repository.enqueue_sync_job(
        athlete_alias=ALIAS,
        job_kind="FULL_SYNC",
        idempotency_key="a" * 64,
        request_payload={},
    )
    running = repository.claim_sync_job(worker_id="worker", lease_seconds=300)
    assert running is not None and running["job_id"] == first["job_id"]

    successor = repository.enqueue_sync_job(
        athlete_alias=ALIAS,
        job_kind="FULL_SYNC",
        idempotency_key="b" * 64,
        request_payload={},
    )
    coalesced = repository.enqueue_sync_job(
        athlete_alias=ALIAS,
        job_kind="FULL_SYNC",
        idempotency_key="c" * 64,
        request_payload={},
    )
    assert successor["job_id"] != first["job_id"]
    assert coalesced["job_id"] == successor["job_id"]
    assert coalesced["deduplicated"] is True

    failed = repository.fail_sync_job(
        job_id=running["job_id"],
        generation_id=running["generation_id"],
        lease_token=running["lease_token"],
        error_code="PROVIDER_UNAVAILABLE",
        retryable=True,
        retry_after_seconds=86_400,
    )
    assert failed["status"] == "SUPERSEDED"
    assert repository.sync_state(ALIAS)["job_id"] == successor["job_id"]
    next_claim = repository.claim_sync_job(worker_id="worker", lease_seconds=300)
    assert next_claim is not None and next_claim["job_id"] == successor["job_id"]


def test_retry_after_is_bounded_to_twenty_four_hours():
    repository = InMemorySnapshotRepository()
    repository.enqueue_sync_job(
        athlete_alias=ALIAS,
        job_kind="FULL_SYNC",
        idempotency_key="a" * 64,
        request_payload={},
    )
    running = repository.claim_sync_job(worker_id="worker", lease_seconds=300)
    assert running is not None
    with pytest.raises(ValueError, match="retry delay"):
        repository.fail_sync_job(
            job_id=running["job_id"],
            generation_id=running["generation_id"],
            lease_token=running["lease_token"],
            error_code="RATE_LIMITED",
            retryable=True,
            retry_after_seconds=86_401,
        )


def test_rollback_reactivates_only_a_complete_previously_active_generation():
    repository = InMemorySnapshotRepository()
    first_activity = _activity(repository, "first")
    first_claim, _ = _build_generation(
        repository, marker="first", activities=[first_activity]
    )
    second_activity = _activity(repository, "later")
    _build_generation(repository, marker="later", activities=[second_activity])

    result = repository.rollback_analysis_generation(
        athlete_alias=ALIAS,
        target_generation_id=first_claim["generation_id"],
    )
    assert result["outcome"] == "ROLLED_BACK"
    assert result["active_revision"] == 3
    assert repository.latest(ALIAS)["training_status"]["marker"] == "first"


def test_snapshot_copies_cannot_mutate_active_generation():
    repository = InMemorySnapshotRepository()
    activity = _activity(repository, "first")
    _build_generation(repository, marker="first", activities=[activity])
    payload = repository.latest(ALIAS)
    assert payload is not None
    mutated = deepcopy(payload)
    mutated["training_status"]["marker"] = "caller-mutation"
    assert repository.latest(ALIAS)["training_status"]["marker"] == "first"


def test_revision_zero_activity_view_keeps_latest_series_and_shadow_fallbacks():
    repository = InMemorySnapshotRepository()
    repository.replace(ALIAS, _snapshot("legacy"))
    _activity(repository, "first")
    repository.upsert_activity_catalog(
        ALIAS,
        [
            {
                "activity_ref": ACTIVITY_REF,
                "start_at_utc": "2026-08-27T06:00:00+00:00",
                "local_date": "2026-08-27",
                "sport": "Run",
            }
        ],
    )

    with_shadow = repository.active_activity_view(ALIAS, ACTIVITY_REF)
    assert with_shadow is not None
    assert with_shadow["series_payload"] is not None
    assert with_shadow["shadow_payload"] is not None

    repository._activity_runs[(ALIAS, ACTIVITY_REF)] = []
    without_shadow = repository.active_activity_view(ALIAS, ACTIVITY_REF)
    assert without_shadow is not None
    assert without_shadow["series_payload"] is not None
    assert without_shadow["shadow_payload"] is None


def test_generation_migration_keeps_profile_and_activity_set_fks_deferred_and_scoped():
    migration = Path(
        "supabase/migrations/202608270001_sync_queue_generations.sql"
    ).read_text(encoding="utf-8")
    assert "foreign key (activity_set_generation_id, athlete_alias)" in migration
    assert "and ga.athlete_alias = p_athlete_alias" in migration
    assert (
        "onflows_activity_derived_runs_input_key_fkey\n"
        "  foreign key (input_key)" in migration
    )
    assert (
        "onflows_activity_canonical_runs_athlete_alias_activity_ref_fkey\n"
        "  foreign key (athlete_alias, activity_ref)" in migration
    )
    assert migration.count("deferrable initially deferred") >= 10
    assert "create function public.active_onflows_activity_view(" in migration
    assert "order by model_input.created_at desc, model_input.input_key desc" in migration
    assert "order by derived.created_at desc, derived.run_key desc" in migration
    assert "onflows_oauth_states_athlete_alias_fk" in migration
    assert "p_terminal_older_than < interval '30 days'" in migration
    release_pointers = migration.index(
        "set latest_shadow_run_key = null,"
    )
    shadow_delete = migration.index(
        "delete from public.onflows_activity_derived_runs"
    )
    canonical_delete = migration.index(
        "delete from public.onflows_activity_canonical_runs"
    )
    input_delete = migration.index(
        "delete from public.onflows_activity_model_inputs"
    )
    catalog_delete = migration.index("delete from public.onflows_activity_catalog")
    assert release_pointers < shadow_delete < input_delete < catalog_delete
    assert canonical_delete < catalog_delete
    assert migration.count("and ga.athlete_alias = p_athlete_alias") >= 4


def test_enqueue_repair_migration_uses_unambiguous_conflict_target():
    migration = Path(
        "supabase/migrations/202608270002_enqueue_sync_job_conflict_target.sql"
    ).read_text(encoding="utf-8")
    assert "create or replace function public.enqueue_onflows_sync_job(" in migration
    assert (
        "on conflict on constraint onflows_athlete_analysis_state_pkey do nothing"
        in migration
    )
    assert "\n  on conflict (athlete_alias)" not in migration
