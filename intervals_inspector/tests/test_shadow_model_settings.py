from __future__ import annotations

import json
from pathlib import Path

import pytest

from intervals_inspector.model_registry import (
    explanation_text,
    validate_registry_items,
)
from intervals_inspector.shadow_model import (
    EDITABLE_FIELDS,
    READ_ONLY_FIELDS,
    build_model_registry,
    calculate_shadow_comparison,
    calculate_shadow_result,
    configuration_from_safe_dict,
    configuration_to_safe_dict,
    configuration_with_overrides,
    default_shadow_configuration,
    export_shadow_diagnostics_json,
    reset_shadow_configuration,
)
from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    normalize_stream_intervals,
)


def _real_interval_result():
    return normalize_stream_intervals(
        NormalizerInput(
            offsets=list(range(61)),
            metrics={"heartrate": [145.0] * 61},
            elapsed_time_sec=60.0,
            icu_recording_time_sec=60.0,
        )
    )


def test_every_displayed_parameter_result_and_warning_has_one_explanation() -> None:
    configuration = default_shadow_configuration()
    registry = build_model_registry(configuration)

    validate_registry_items(registry, registry)
    assert len(registry) == len(set(registry))
    for item_id, definition in registry.items():
        rendered = explanation_text(definition)
        assert definition["full_name"] in rendered
        assert "Формула" in rendered
        assert "Мерна единица" in rendered
        assert definition["version"] in rendered
        assert not definition["sensitive"], item_id


def test_parameter_registry_exposes_unit_version_source_initial_and_current() -> None:
    baseline = default_shadow_configuration()
    experimental = configuration_with_overrides(
        {"parameter.Z2.power": 1.45}, baseline=baseline
    )
    registry = build_model_registry(experimental, baseline=baseline)
    power = registry["parameter.Z2.power"]

    assert power["unit"] == "без единица"
    assert power["version"] == experimental.physiology_profile_version
    assert power["value_source"] == "индивидуален override"
    assert power["initial_value"] == pytest.approx(1.10)
    assert power["current_value"] == pytest.approx(1.45)


@pytest.mark.parametrize(
    ("item_id", "value"),
    (
        ("parameter.Z1.weight_low", 0.0),
        ("parameter.Z1.weight_high", 2001.0),
        ("parameter.Z1.power", 4.01),
        ("parameter.Z2.spill_threshold_fraction", -0.01),
        ("parameter.Z2.spill_down_fraction", 1.01),
        ("parameter.Z2.spill_up_fraction", -1.0),
        ("parameter.Z3.tref_min", -0.1),
        ("parameter.Z3.tref_max", 10081.0),
        ("parameter.Z4.bounds_factor", 1.51),
    ),
)
def test_experimental_values_outside_allowed_ranges_are_rejected(
    item_id: str, value: float
) -> None:
    with pytest.raises(ValueError, match="must be between"):
        configuration_with_overrides({item_id: value})


def test_cross_field_validation_and_read_only_protection() -> None:
    with pytest.raises(ValueError, match="weight_high"):
        configuration_with_overrides(
            {
                "parameter.Z2.weight_low": 500.0,
                "parameter.Z2.weight_high": 400.0,
            }
        )
    with pytest.raises(ValueError, match="read-only"):
        configuration_with_overrides(
            {"parameter.Z2.profile_version": 2.0}
        )


def test_safe_round_trip_and_reset_restore_the_initial_configuration() -> None:
    baseline = default_shadow_configuration()
    changed = configuration_with_overrides(
        {
            "parameter.Z1.power": 1.9,
            "parameter.Z5.spill_up_fraction": 0.0,
        }
    )

    restored_state = configuration_from_safe_dict(
        configuration_to_safe_dict(changed)
    )
    reset = reset_shadow_configuration()

    assert restored_state == changed
    assert changed.fingerprint != baseline.fingerprint
    assert reset == baseline
    assert reset.overrides == ()


def test_allowed_change_recalculates_and_preserves_both_results() -> None:
    baseline = default_shadow_configuration()
    experimental = configuration_with_overrides(
        {
            "parameter.Z2.weight_high": 300.0,
            "parameter.Z2.power": 1.0,
        },
        baseline=baseline,
    )

    comparison = calculate_shadow_comparison(
        _real_interval_result(),
        experimental_configuration=experimental,
    )
    z2 = next(row for row in comparison["comparison_rows"] if row["zone"] == "Z2")

    assert z2["baseline_T_z"] == pytest.approx(z2["experimental_T_z"])
    assert z2["baseline_Q_z"] != pytest.approx(z2["experimental_Q_z"])
    assert z2["delta_Q_z"] == pytest.approx(
        z2["experimental_Q_z"] - z2["baseline_Q_z"]
    )
    assert comparison["baseline"]["rows"]
    assert comparison["experimental"]["rows"]
    assert comparison["experimental"]["experimental"] is True


def test_cascade_bidirectional_spill_and_tref_are_visible_per_zone() -> None:
    configuration = default_shadow_configuration()
    analysis = {
        "hr_coverage_percent": 100.0,
        "zones": [
            {
                "zone": zone.zone,
                "real_seconds": 0.0,
                "weighted_seconds": 90.0 * 60.0 if zone.zone == "Z2" else 0.0,
            }
            for zone in configuration.zones
        ],
    }

    result = calculate_shadow_result(analysis, configuration)
    rows = {row["zone"]: row for row in result["rows"]}

    # Tref_Z2 falls back visibly to 7 × main's 20 min/day base = 140.
    # Excess is 90 - 0.5 × 140 = 20; 20% down and 10% up.
    assert rows["Z2"]["tref_raw"] == pytest.approx(140.0)
    assert rows["Z1"]["spillover_received"] == pytest.approx(4.0)
    assert rows["Z3"]["spillover_received"] == pytest.approx(2.0)
    assert rows["Z1"]["cascade"] == pytest.approx(90.0)
    assert rows["Z1"]["E_z"] == pytest.approx(94.0)
    assert rows["Z3"]["E_z"] == pytest.approx(2.0)
    for row in rows.values():
        assert {
            "T_z",
            "Q_z",
            "cascade",
            "spillover_received",
            "E_z",
            "tref_raw",
            "tref_effective",
        } <= row.keys()


def test_shadow_layer_is_memory_only_and_integrated_without_streamlit() -> None:
    comparison = calculate_shadow_comparison(_real_interval_result())
    repository_root = Path(__file__).resolve().parents[2]
    main_source = (repository_root / "app.py").read_text(encoding="utf-8")
    shadow_source = (repository_root / "intervals_inspector" / "shadow_model.py").read_text(
        encoding="utf-8"
    )

    assert comparison["memory_only"] is True
    assert comparison["persistence_backend"] is None
    assert comparison["affects_main_demonstrator"] is False
    assert "render_integrated_page" in main_source
    assert "streamlit" not in shadow_source.lower()
    assert "supabase" not in shadow_source.lower()


def test_visible_does_not_mean_editable() -> None:
    registry = build_model_registry(default_shadow_configuration())
    for zone in ("Z1", "Z2", "Z3", "Z4", "Z5"):
        for field in EDITABLE_FIELDS:
            definition = registry[f"parameter.{zone}.{field}"]
            assert definition["visible"] is True
            assert definition["editable"] is True
            assert definition["editable_roles"] == ["tester", "administrator"]
        for field in READ_ONLY_FIELDS:
            definition = registry[f"parameter.{zone}.{field}"]
            assert definition["visible"] is True
            assert definition["editable"] is False
            assert definition["editable_roles"] == []


def test_sensitive_keys_are_rejected_from_diagnostic_export() -> None:
    safe = calculate_shadow_comparison(_real_interval_result())
    rendered = export_shadow_diagnostics_json(safe)
    parsed = json.loads(rendered)

    assert parsed["memory_only"] is True
    lowered = rendered.lower()
    for forbidden in (
        "access_token",
        "refresh_token",
        "client_secret",
        "password",
        "authorization",
        "email",
    ):
        assert forbidden not in lowered

    unsafe = dict(safe)
    unsafe["access_token"] = "must-never-export"
    with pytest.raises(ValueError, match="sensitive data"):
        export_shadow_diagnostics_json(unsafe)
