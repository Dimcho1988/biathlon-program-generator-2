from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
import time

import pytest

from apps.api.real_service import (
    ConfigurationError,
    ProviderFailure,
    RecoverySourceRefreshRequired,
)
from apps.api.cloud import InMemorySnapshotRepository
from apps.api.sync_contracts import ClaimedSyncJob, SyncContractError
from apps.api.sync_worker import (
    SyncPipelines,
    process_claimed_job,
    run_worker,
)
from intervals_inspector.intervals_client import IntervalsAPIError


NOW = datetime(2026, 8, 27, 6, tzinfo=timezone.utc)
ACTIVITY_REF = "act_" + "1" * 32
BASE_ACTIVITY_HASH = "a" * 64


def snapshot(*, marker: str = "original") -> dict:
    return {
        "schema_version": "athlete-snapshot-v1",
        "training_status": {
            "schema_version": "training-status-v1",
            "as_of": "2026-08-27",
            "athlete_id": "athlete-one",
            "model": {"algorithm_version": "canonical-v1"},
            "marker": marker,
        },
        "load_history": {
            "schema_version": "load-history-v2",
            "athlete_id": "athlete-one",
            "period_start": "2026-07-18",
            "period_end": "2026-08-27",
            "tref_bounds_profile_version": "bounded-40d-tref-v1",
            "zones": [
                {"zone": zone, "tref_min": tref}
                for zone, tref in zip(
                    ("Z1", "Z2", "Z3", "Z4", "Z5"),
                    (300.0, 180.0, 70.0, 20.0, 20.0),
                )
            ],
            "strength": {"summary": {"tref_min": 56.0}},
            "marker": marker,
        },
        "recovery_history": {
            "schema_version": "recovery-history-v1",
            "athlete_id": "athlete-one",
            "period_start": "2026-07-18",
            "period_end": "2026-08-27",
            "model": {"algorithm_version": "recovery-v1"},
            "marker": marker,
        },
        "wellness_calendar": [],
    }


def claim(kind: str = "FULL_SYNC", **overrides) -> dict:
    payload = {
        "job_id": "job-001",
        "athlete_alias": "athlete-one",
        "job_kind": kind,
        "request_payload": {"as_of": "2026-08-27"},
        "request_sequence": 2,
        "generation_id": "generation-002",
        "attempt_no": 1,
        "base_generation_id": (
            None if kind == "FULL_SYNC" else "generation-001"
        ),
        "base_revision": 1 if kind != "FULL_SYNC" else 0,
        "base_activity_set_hash": (
            None if kind == "FULL_SYNC" else BASE_ACTIVITY_HASH
        ),
        "lease_token": "lease-token-001",
        "lease_expires_at": "2026-08-27T06:05:00+00:00",
    }
    payload.update(overrides)
    return payload


class Repository:
    def __init__(self) -> None:
        self.current = snapshot()
        self.stage_calls: list[dict] = []
        self.activate_calls: list[dict] = []
        self.fail_calls: list[dict] = []
        self.canonical_calls: list[dict] = []
        self.shadow_calls: list[dict] = []
        self.forbidden_replace_calls = 0
        self.forbidden_catalog_calls = 0
        self.forbidden_canonical_publish_calls = 0
        self.renew_results: list[bool] = []
        self.renew_calls: list[dict] = []
        self.activation_outcome = "ACTIVATED"
        self.fail_status_override: str | None = None
        self.claims: list[dict] = []
        self.latest_envelope_override: dict | None = None

    def connection(self, athlete_alias):
        assert athlete_alias == "athlete-one"
        return SimpleNamespace(
            status="CONNECTED",
            provider_athlete_id="private-provider-id",
            access_token="private-access-token",
        )

    def athlete_settings(self, athlete_alias):
        assert athlete_alias == "athlete-one"
        return SimpleNamespace(timezone="Europe/Sofia")

    def latest(self, athlete_alias):
        assert athlete_alias == "athlete-one"
        return deepcopy(self.current)

    def latest_envelope(self, athlete_alias):
        assert athlete_alias == "athlete-one"
        return deepcopy(
            self.latest_envelope_override
            or {
                "payload": self.current,
                "generation_id": "generation-001",
                "revision": 1,
                "activated_at": "2026-08-27T05:00:00+00:00",
            }
        )

    # These active-state methods must never be reached through the staging proxy.
    def replace(self, athlete_alias, value):
        self.forbidden_replace_calls += 1
        raise AssertionError("worker pipeline must capture snapshot replace")

    def upsert_activity_catalog(self, athlete_alias, values):
        self.forbidden_catalog_calls += 1
        raise AssertionError("worker pipeline must capture catalog writes")

    def publish_canonical_activity_result(self, **values):
        self.forbidden_canonical_publish_calls += 1
        raise AssertionError("worker must use insert-only canonical publication")

    def store_canonical_activity_result(self, **values):
        self.canonical_calls.append(deepcopy(values))
        return "c" * 64

    def publish_activity_shadow(self, **values):
        self.shadow_calls.append(deepcopy(values))
        return "s" * 64

    def stage_analysis_generation(self, **values):
        self.stage_calls.append(deepcopy(values))
        return {"outcome": "READY", "activity_count": len(values["activities"])}

    def renew_sync_lease(self, **values):
        self.renew_calls.append(deepcopy(values))
        return self.renew_results.pop(0) if self.renew_results else True

    def activate_analysis_generation(self, **values):
        self.activate_calls.append(deepcopy(values))
        return {
            "outcome": self.activation_outcome,
            "active_generation_id": "generation-002",
            "active_revision": 2,
        }

    def fail_sync_job(self, **values):
        self.fail_calls.append(deepcopy(values))
        return {
            "status": self.fail_status_override
            or ("RETRY_WAIT" if values["retryable"] else "FAILED"),
            "available_at": "2026-08-27T06:01:00+00:00",
        }

    def claim_sync_job(self, **values):
        del values
        return self.claims.pop(0) if self.claims else None


def full_pipeline(repository, **kwargs):
    alias = kwargs["athlete_alias"]
    shadow_key = repository.publish_activity_shadow(
        athlete_alias=alias,
        activity_ref=ACTIVITY_REF,
        input_payload={"input_hash": "i" * 64},
        derived_payload={"result_hash": "r" * 64},
    )
    canonical_key = repository.publish_canonical_activity_result(
        athlete_alias=alias,
        activity_ref=ACTIVITY_REF,
        scientific_input_hash="h" * 64,
        result_payload={"schema_version": "canonical-v1", "load": 12.0},
    )
    repository.upsert_activity_catalog(
        alias,
        [
            {
                "activity_ref": ACTIVITY_REF,
                "start_at_utc": "2026-08-27T05:00:00Z",
                "local_date": "2026-08-27",
                "latest_canonical_run_key": canonical_key,
                "latest_shadow_run_key": shadow_key,
                "canonical_summary": {"load": 12.0},
            }
        ],
    )
    repository.replace(alias, snapshot(marker="full"))


def wellness_pipeline(repository, **kwargs):
    alias = kwargs["athlete_alias"]
    updated = dict(repository.latest(alias))
    updated["wellness_calendar"] = [
        {"date": "2026-08-27", "metrics": {"hrv": {"value": 80, "unit": "ms"}}}
    ]
    repository.replace(alias, updated)


def recovery_pipeline(repository, **kwargs):
    alias = kwargs["athlete_alias"]
    updated = dict(repository.latest(alias))
    updated["recovery_history"] = {
        **dict(updated["recovery_history"]),
        "marker": "restored",
    }
    repository.replace(alias, updated)


PIPELINES = SyncPipelines(
    full=full_pipeline,
    wellness=wellness_pipeline,
    recovery=recovery_pipeline,
)


def process(repo: Repository, queued: dict, **kwargs):
    return process_claimed_job(
        repo,
        queued,
        pipelines=PIPELINES,
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
        **kwargs,
    )


def test_full_sync_stages_all_mutable_outputs_then_activates_once():
    repo = Repository()

    result = process(repo, claim())

    assert result.outcome == "ACTIVATED"
    assert result.active_revision == 2
    assert repo.forbidden_replace_calls == 0
    assert repo.forbidden_catalog_calls == 0
    assert repo.forbidden_canonical_publish_calls == 0
    assert len(repo.canonical_calls) == 1
    assert len(repo.shadow_calls) == 1
    assert len(repo.stage_calls) == 1
    staged = repo.stage_calls[0]
    assert staged["snapshot_payload"]["training_status"]["marker"] == "full"
    assert staged["activities"][0]["activity_ref"] == ACTIVITY_REF
    assert staged["activities"][0]["latest_canonical_run_key"] == "c" * 64
    assert staged["activities"][0]["latest_shadow_run_key"] == "s" * 64
    assert staged["inherit_activities"] is False
    assert staged["period_start"].isoformat() == "2026-07-18"
    assert staged["period_end"].isoformat() == "2026-08-27"
    assert staged["as_of"].isoformat() == "2026-08-27"
    assert len(staged["snapshot_hash"]) == 64
    assert len(staged["provenance"]["activity_set_hash"]) == 64
    assert (
        staged["provenance"]["tref_bounds_profile_version"]
        == "bounded-40d-tref-v1"
    )
    assert staged["provenance"]["tref_current_min"] == {
        "zones": [
            {"zone": zone, "tref_min": tref}
            for zone, tref in zip(
                ("Z1", "Z2", "Z3", "Z4", "Z5"),
                (300.0, 180.0, 70.0, 20.0, 20.0),
            )
        ],
        "strength": 56.0,
    }
    rendered = repr(staged)
    assert "private-access-token" not in rendered
    assert "private-provider-id" not in rendered
    assert len(repo.activate_calls) == 1


def test_empty_full_sync_has_an_explicit_empty_activity_set_identity():
    repo = Repository()

    def empty_full(repository, **kwargs):
        alias = kwargs["athlete_alias"]
        repository.upsert_activity_catalog(alias, [])
        repository.replace(alias, snapshot(marker="empty"))

    result = process_claimed_job(
        repo,
        claim(base_activity_set_hash=BASE_ACTIVITY_HASH),
        pipelines=SyncPipelines(
            full=empty_full,
            wellness=wellness_pipeline,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "ACTIVATED"
    activity_set_hash = repo.stage_calls[0]["provenance"]["activity_set_hash"]
    assert len(activity_set_hash) == 64
    assert activity_set_hash != BASE_ACTIVITY_HASH


def test_new_activity_stage_pins_exact_runs_and_resolvable_shadow_input():
    repo = InMemorySnapshotRepository()
    repo.connection = lambda alias: SimpleNamespace(  # type: ignore[attr-defined]
        status="CONNECTED",
        provider_athlete_id="private-provider-id",
        access_token="private-access-token",
    )
    repo.athlete_settings = lambda alias: SimpleNamespace(  # type: ignore[attr-defined]
        timezone="Europe/Sofia"
    )
    activity_ref = repo.resolve_activity_ref("athlete-one", "p" * 64)
    published: dict[str, str] = {}

    def new_activity_full(repository, **kwargs):
        alias = kwargs["athlete_alias"]
        published["shadow"] = repository.publish_activity_shadow(
            athlete_alias=alias,
            activity_ref=activity_ref,
            input_payload={"input_hash": "i" * 64, "samples": []},
            derived_payload={"result_hash": "r" * 64},
        )
        published["canonical"] = repository.publish_canonical_activity_result(
            athlete_alias=alias,
            activity_ref=activity_ref,
            scientific_input_hash="h" * 64,
            result_payload={"schema_version": "canonical-v1", "load": 12.0},
        )
        repository.upsert_activity_catalog(
            alias,
            [
                {
                    "activity_ref": activity_ref,
                    "provider_activity_key": "p" * 64,
                    "start_at_utc": "2026-08-27T05:00:00Z",
                    "local_date": "2026-08-27",
                    "latest_canonical_run_key": published["canonical"],
                    "latest_shadow_run_key": published["shadow"],
                }
            ],
        )
        generated = snapshot(marker="new-activity")
        generated["load_history"]["activities"] = [
            {"activity_ref": activity_ref}
        ]
        repository.replace(alias, generated)

    repo.enqueue_sync_job(
        athlete_alias="athlete-one",
        job_kind="FULL_SYNC",
        idempotency_key="d" * 64,
        request_payload={"as_of": "2026-08-27"},
    )
    queued = repo.claim_sync_job(worker_id="worker-one", lease_seconds=30)
    assert queued is not None

    result = process_claimed_job(
        repo,
        queued,
        pipelines=SyncPipelines(
            full=new_activity_full,
            wellness=wellness_pipeline,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "ACTIVATED"
    pinned = repo._generation_activities[(result.generation_id, activity_ref)]
    assert pinned["canonical_run_key"] == published["canonical"]
    assert pinned["shadow_run_key"] == published["shadow"]
    assert isinstance(pinned["input_key"], str) and len(pinned["input_key"]) == 64
    assert repo.activity_series("athlete-one", activity_ref) is not None


@pytest.mark.parametrize(
    ("kind", "marker"),
    [("WELLNESS_SYNC", "original"), ("RECOVERY_RESTORE", "restored")],
)
def test_patch_jobs_inherit_exact_active_generation_activities(kind, marker):
    repo = Repository()

    result = process(repo, claim(kind))

    assert result.outcome == "ACTIVATED"
    staged = repo.stage_calls[0]
    assert staged["activities"] == []
    assert staged["inherit_activities"] is True
    assert staged["provenance"]["base_generation_id"] == "generation-001"
    assert staged["provenance"]["activity_set_hash"] == BASE_ACTIVITY_HASH
    if kind == "WELLNESS_SYNC":
        assert staged["snapshot_payload"]["wellness_calendar"]
    else:
        assert staged["snapshot_payload"]["recovery_history"]["marker"] == marker


def test_patch_job_without_an_active_generation_fails_closed():
    repo = Repository()
    queued = claim("WELLNESS_SYNC", base_generation_id=None, base_revision=0)

    result = process(repo, queued)

    assert result.outcome == "FAILED"
    assert result.failure_code == "ACTIVE_GENERATION_REQUIRED"
    assert repo.stage_calls == []
    assert repo.activate_calls == []
    assert repo.fail_calls[0]["retryable"] is False


def test_patch_job_retries_without_provider_work_if_claim_base_changed():
    repo = Repository()
    repo.latest_envelope_override = {
        "payload": snapshot(marker="newer"),
        "generation_id": "generation-newer",
        "revision": 2,
        "activated_at": "2026-08-27T05:30:00+00:00",
    }
    calls = 0

    def must_not_run(repository, **kwargs):
        nonlocal calls
        del repository, kwargs
        calls += 1

    result = process_claimed_job(
        repo,
        claim("WELLNESS_SYNC"),
        pipelines=SyncPipelines(
            full=full_pipeline,
            wellness=must_not_run,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "RETRY_SCHEDULED"
    assert result.failure_code == "BASE_GENERATION_CHANGED"
    assert calls == 0
    assert repo.stage_calls == []
    assert repo.fail_calls[0]["retryable"] is True


def test_final_lease_fence_prevents_activation_by_a_stale_worker():
    repo = Repository()
    repo.renew_results = [True, False]

    result = process(repo, claim())

    assert result.outcome == "LEASE_LOST"
    assert len(repo.stage_calls) == 1
    assert repo.activate_calls == []
    assert repo.fail_calls == []


def test_heartbeat_lease_loss_prevents_staging_and_failure_write():
    repo = Repository()
    repo.renew_results = [True, False]

    def slow_full(repository, **kwargs):
        full_pipeline(repository, **kwargs)
        time.sleep(0.04)

    result = process_claimed_job(
        repo,
        claim(),
        pipelines=SyncPipelines(
            full=slow_full,
            wellness=wellness_pipeline,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=0.005,
        clock=lambda: NOW,
    )

    assert result.outcome == "LEASE_LOST"
    assert repo.stage_calls == []
    assert repo.activate_calls == []
    assert repo.fail_calls == []


def test_stale_claim_is_fenced_before_spending_provider_quota():
    repo = Repository()
    repo.renew_results = [False]
    calls = 0

    def must_not_run(repository, **kwargs):
        nonlocal calls
        del repository, kwargs
        calls += 1

    result = process_claimed_job(
        repo,
        claim(),
        pipelines=SyncPipelines(
            full=must_not_run,
            wellness=wellness_pipeline,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "LEASE_LOST"
    assert calls == 0
    assert repo.stage_calls == []
    assert repo.fail_calls == []


def test_retryable_provider_failure_is_sanitized_and_bounded_by_store():
    repo = Repository()

    def rate_limited(repository, **kwargs):
        del repository, kwargs
        try:
            raise IntervalsAPIError(
                "private response body",
                status_code=429,
                retry_after_seconds=21_600.2,
                retryable=True,
            )
        except IntervalsAPIError as exc:
            raise ProviderFailure("Intervals provider request failed") from exc

    result = process_claimed_job(
        repo,
        claim(),
        pipelines=SyncPipelines(
            full=rate_limited,
            wellness=wellness_pipeline,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "RETRY_SCHEDULED"
    assert result.failure_code == "PROVIDER_RATE_LIMITED"
    assert repo.fail_calls == [
        {
            "job_id": "job-001",
            "generation_id": "generation-002",
            "lease_token": "lease-token-001",
            "error_code": "PROVIDER_RATE_LIMITED",
            "retryable": True,
            "retry_after_seconds": 21_601,
        }
    ]
    assert "private response body" not in repr(repo.fail_calls)


def test_invalid_scientific_configuration_is_not_retried():
    repo = Repository()

    def invalid(repository, **kwargs):
        del repository, kwargs
        raise ConfigurationError("private configuration detail")

    result = process_claimed_job(
        repo,
        claim(),
        pipelines=SyncPipelines(
            full=invalid,
            wellness=wellness_pipeline,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "FAILED"
    assert result.failure_code == "SCIENTIFIC_CONFIGURATION_INVALID"
    assert repo.fail_calls[0]["retryable"] is False
    assert repo.fail_calls[0]["retry_after_seconds"] is None


def test_recovery_source_upgrade_requirement_has_a_typed_terminal_code():
    repo = Repository()

    def source_upgrade_required(repository, **kwargs):
        del repository, kwargs
        raise RecoverySourceRefreshRequired("private source detail")

    result = process_claimed_job(
        repo,
        claim("RECOVERY_RESTORE"),
        pipelines=SyncPipelines(
            full=full_pipeline,
            wellness=wellness_pipeline,
            recovery=source_upgrade_required,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "FAILED"
    assert result.failure_code == "RECOVERY_SOURCE_REFRESH_REQUIRED"
    assert repo.fail_calls[0]["retryable"] is False


def test_provider_wrapper_around_invalid_scientific_data_is_not_retried():
    repo = Repository()

    def invalid_provider_data(repository, **kwargs):
        del repository, kwargs
        try:
            raise ValueError("private invalid payload detail")
        except ValueError as exc:
            raise ProviderFailure("Normalization failed") from exc

    result = process_claimed_job(
        repo,
        claim(),
        pipelines=SyncPipelines(
            full=invalid_provider_data,
            wellness=wellness_pipeline,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "FAILED"
    assert result.failure_code == "SCIENTIFIC_RESULT_INVALID"
    assert repo.fail_calls[0]["retryable"] is False


def test_failed_attempt_reports_terminal_supersession_by_a_followup_job():
    repo = Repository()
    repo.fail_status_override = "SUPERSEDED"

    def transient(repository, **kwargs):
        del repository, kwargs
        raise ProviderFailure("Provider unavailable")

    result = process_claimed_job(
        repo,
        claim(),
        pipelines=SyncPipelines(
            full=transient,
            wellness=wellness_pipeline,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "SUPERSEDED"
    assert result.failure_code == "PROVIDER_UNAVAILABLE"


def test_explicit_terminal_provider_classification_is_not_retried():
    repo = Repository()

    def terminal_provider_error(repository, **kwargs):
        del repository, kwargs
        try:
            raise IntervalsAPIError(
                "private response body",
                status_code=422,
                terminal=True,
            )
        except IntervalsAPIError as exc:
            raise ProviderFailure("Intervals provider request failed") from exc

    result = process_claimed_job(
        repo,
        claim(),
        pipelines=SyncPipelines(
            full=terminal_provider_error,
            wellness=wellness_pipeline,
            recovery=recovery_pipeline,
        ),
        lease_seconds=30,
        heartbeat_interval_seconds=30,
        clock=lambda: NOW,
    )

    assert result.outcome == "FAILED"
    assert result.failure_code == "PROVIDER_REQUEST_REJECTED"
    assert repo.fail_calls[0]["retryable"] is False


def test_stale_activation_is_terminal_and_does_not_fail_the_job_again():
    repo = Repository()
    repo.activation_outcome = "STALE"

    result = process(repo, claim())

    assert result.outcome == "STALE"
    assert repo.fail_calls == []


def test_once_mode_claims_at_most_one_job():
    repo = Repository()
    repo.claims = [claim(), claim(job_id="job-002", generation_id="generation-003")]

    exit_code = run_worker(
        repo,
        worker_id="test-worker",
        once=True,
        lease_seconds=30,
        pipelines=PIPELINES,
        clock=lambda: NOW,
    )

    assert exit_code == 0
    assert len(repo.claims) == 1
    assert len(repo.activate_calls) == 1


def test_once_mode_is_successful_when_queue_is_empty():
    repo = Repository()

    assert run_worker(
        repo,
        worker_id="test-worker",
        once=True,
        lease_seconds=30,
        pipelines=PIPELINES,
        clock=lambda: NOW,
    ) == 0


def test_housekeeping_runs_after_first_completed_job_and_never_owns_its_lease():
    stopping = Event()

    class MaintenanceRepository(Repository):
        def __init__(self):
            super().__init__()
            self.maintenance_calls: list[dict] = []

        def claim_sync_job(self, **values):
            del values
            if self.claims:
                return self.claims.pop(0)
            stopping.set()
            return None

        def prune_analysis_generations(self, **values):
            assert self.activate_calls
            self.maintenance_calls.append(deepcopy(values))
            return {
                "deleted_generations": 2,
                "deleted_activity_rows": 3,
                "deleted_jobs": 1,
            }

    repo = MaintenanceRepository()
    repo.claims = [claim()]

    exit_code = run_worker(
        repo,
        worker_id="test-worker",
        lease_seconds=30,
        poll_seconds=0.001,
        max_idle_seconds=0.001,
        stop_event=stopping,
        pipelines=PIPELINES,
        clock=lambda: NOW,
        monotonic_clock=lambda: 100.0,
    )

    assert exit_code == 0
    assert repo.maintenance_calls == [
        {
            "athlete_alias": "athlete-one",
            "keep_superseded": 5,
            "terminal_older_than_days": 30,
            "batch_limit": 100,
        }
    ]


def test_once_mode_skips_optional_housekeeping():
    class MaintenanceRepository(Repository):
        def __init__(self):
            super().__init__()
            self.maintenance_calls = 0

        def prune_analysis_generations(self, **values):
            del values
            self.maintenance_calls += 1
            return {
                "deleted_generations": 0,
                "deleted_activity_rows": 0,
                "deleted_jobs": 0,
            }

    repo = MaintenanceRepository()
    repo.claims = [claim()]

    assert run_worker(
        repo,
        worker_id="test-worker",
        once=True,
        lease_seconds=30,
        pipelines=PIPELINES,
        clock=lambda: NOW,
    ) == 0
    assert repo.maintenance_calls == 0


def test_housekeeping_failure_does_not_block_further_claims():
    stopping = Event()

    class FailingMaintenanceRepository(Repository):
        def claim_sync_job(self, **values):
            del values
            if self.claims:
                return self.claims.pop(0)
            stopping.set()
            return None

        def prune_analysis_generations(self, **values):
            del values
            raise RuntimeError("private database detail")

    repo = FailingMaintenanceRepository()
    repo.claims = [claim()]

    assert run_worker(
        repo,
        worker_id="test-worker",
        lease_seconds=30,
        poll_seconds=0.001,
        max_idle_seconds=0.001,
        stop_event=stopping,
        pipelines=PIPELINES,
        clock=lambda: NOW,
        monotonic_clock=lambda: 100.0,
    ) == 0
    assert len(repo.activate_calls) == 1


def test_housekeeping_is_bounded_to_once_per_alias_every_six_hours():
    stopping = Event()

    class MaintenanceRepository(Repository):
        def __init__(self):
            super().__init__()
            self.maintenance_calls = 0

        def claim_sync_job(self, **values):
            del values
            if self.claims:
                return self.claims.pop(0)
            stopping.set()
            return None

        def prune_analysis_generations(self, **values):
            del values
            self.maintenance_calls += 1
            return {
                "deleted_generations": 0,
                "deleted_activity_rows": 0,
                "deleted_jobs": 0,
            }

    repo = MaintenanceRepository()
    repo.claims = [
        claim(job_id=f"job-00{index}", generation_id=f"generation-00{index}")
        for index in (1, 2, 3)
    ]
    monotonic_values = iter((0.0, 21_599.0, 21_600.0))

    assert run_worker(
        repo,
        worker_id="test-worker",
        lease_seconds=30,
        poll_seconds=0.001,
        max_idle_seconds=0.001,
        stop_event=stopping,
        pipelines=PIPELINES,
        clock=lambda: NOW,
        monotonic_clock=lambda: next(monotonic_values),
    ) == 0
    assert repo.maintenance_calls == 2


def test_claim_contract_rejects_unfenced_or_malformed_jobs():
    with pytest.raises(SyncContractError):
        ClaimedSyncJob.from_mapping(claim(lease_token=""))
    with pytest.raises(SyncContractError):
        ClaimedSyncJob.from_mapping(claim(base_activity_set_hash="not-a-hash"))
