from __future__ import annotations

from datetime import date

from biathlon.demo_data import generate_demo_bundle
from biathlon.methodology import canonical_methodology
from biathlon.service import analyze_athlete


def test_canonical_methodology_describes_existing_rules_without_stress_activation() -> None:
    methodology = canonical_methodology()

    assert methodology["schema_version"] == "planning-methodology-v1"
    assert methodology["methodology_version"] == "onflows-canonical-v1"
    assert methodology["source_scope"] == "BUILT_IN"
    assert methodology["mesocycle_pattern"] == [0.96, 1.04, 1.10, 0.78]
    assert methodology["supported_accent_modes"] == ["AUTO", "MANUAL", "HYBRID"]
    assert methodology["default_accent_limit"] == 2
    assert methodology["stress_mesocycle"] == {
        "status": "DESIGNED_NOT_ACTIVE",
        "automatic_enabled": False,
        "manual_dose_required": True,
        "selected_accents_only": True,
        "mandatory_recovery": True,
        "affects_canonical_result": False,
    }


def test_generated_decision_and_plan_snapshots_record_methodology_identity() -> None:
    bundle = generate_demo_bundle(
        seed=20250315,
        history_days=120,
        reference_date=date(2026, 6, 20),
    )

    result = analyze_athlete(
        bundle,
        "A",
        as_of=date(2026, 6, 20),
        generate_plan=True,
    )
    expected = {
        "schema_version": "planning-methodology-v1",
        "methodology_id": "onflows-canonical",
        "methodology_version": "onflows-canonical-v1",
        "source_scope": "BUILT_IN",
    }

    assert result["decision_snapshot"]["planning_methodology"] == expected
    assert result["decision_snapshot"]["plan"]["methodology"] == expected
    assert result["decision_snapshot"]["plan"]["accent_mode"] in {
        "AUTO",
        "MANUAL",
        "HYBRID",
    }
