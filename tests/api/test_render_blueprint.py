from pathlib import Path


ANALYSIS_SECRET_KEYS = (
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
    "ONFLOWS_TOKEN_ENCRYPTION_KEY",
    "ONFLOWS_SNAPSHOT_SALT",
    "ONFLOWS_ACTIVITY_ID_SECRET",
)


def _service_block(blueprint: str, service_name: str) -> str:
    marker = f"    name: {service_name}\n"
    start = blueprint.index(marker)
    next_service = blueprint.find("\n  - type:", start + len(marker))
    return blueprint[start:] if next_service == -1 else blueprint[start:next_service]


def test_production_services_pin_the_approved_recovery_version() -> None:
    blueprint = Path("render.yaml").read_text(encoding="utf-8")

    assert blueprint.count(
        "- key: ONFLOWS_RECOVERY_VERSION\n"
        "        value: main-load-recovery-v1"
    ) == 2
    assert (
        "- key: ONFLOWS_RECOVERY_VERSION\n"
        "        sync: false"
    ) not in blueprint


def test_production_api_and_worker_share_one_analysis_secret_group() -> None:
    blueprint = Path("render.yaml").read_text(encoding="utf-8")
    api = _service_block(blueprint, "onflows-api-preview")
    worker = _service_block(blueprint, "onflows-sync-worker-preview")
    web = _service_block(blueprint, "onflows-web-preview")

    assert blueprint.count("- fromGroup: onflows-preview-analysis-shared") == 2
    assert "- fromGroup: onflows-preview-analysis-shared" in api
    assert "- fromGroup: onflows-preview-analysis-shared" in worker
    assert "- fromGroup: onflows-preview-analysis-shared" not in web
    for key in ANALYSIS_SECRET_KEYS:
        assert f"- key: {key}\n" not in blueprint


def test_staging_services_follow_the_integration_branch() -> None:
    blueprint = Path("render.staging.yaml").read_text(encoding="utf-8")

    integration_branch = "branch: codex/build-end-to-end-cloud-integration"
    assert blueprint.count(integration_branch) == 3
    assert "branch: codex/async-refresh-generation-v1" not in blueprint
