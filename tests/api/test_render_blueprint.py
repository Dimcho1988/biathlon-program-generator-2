from pathlib import Path


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
