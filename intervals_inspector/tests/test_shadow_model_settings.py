from __future__ import annotations

import json
from pathlib import Path

import pytest

from intervals_inspector.model_registry import (
    REGISTRY_VERSION,
    explanation_text,
    validate_registry_items,
)
from intervals_inspector.onflows_zone_profile import (
    INTRA_ZONE_EQUIVALENCE_VERSION,
)
from intervals_inspector.shadow_model import (
    EDITABLE_FIELDS,
    READ_ONLY_FIELDS,
    TREF_BOUNDS_MINUTES,
    TREF_PROFILE_VERSION,
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


def _real_interval_result(hr: float = 126.0):
    return normalize_stream_intervals(
        NormalizerInput(
            offsets=list(range(61)),
            metrics={"heartrate": [hr] * 61},
            elapsed_time_sec=60.0,
            icu_recording_time_sec=60.0,
        )
    )


def _analysis_with_equivalent_minutes(
    equivalent_time: dict[str, float],
) -> dict[str, object]:
    configuration = default_shadow_configuration()
    return {
        "hr_coverage_percent": 100.0,
        "zones": [
            {
                "zone": zone.zone,
                "real_seconds": 0.0,
                "equivalent_seconds": 60.0
                * float(equivalent_time.get(zone.zone, 0.0)),
            }
            for zone in configuration.zones
        ],
    }


def test_every_displayed_parameter_result_and_warning_has_one_explanation() -> None:
    configuration = default_shadow_configuration()
    registry = build_model_registry(configuration)

    validate_registry_items(registry, registry)
    assert len(registry) == len(set(registry))
    for item_id, definition in registry.items():
        rendered = explanation_text(definition)
        assert definition["id"] == item_id
        assert definition["full_name"] in rendered
        assert definition["formula"] in rendered
        assert definition["unit"] in rendered
        assert definition["version"] in rendered
        assert definition["registry_version"] == REGISTRY_VERSION
        assert not definition["sensitive"], item_id


def test_registry_exposes_slope_version_source_initial_and_current() -> None:
    baseline = default_shadow_configuration()
    experimental = configuration_with_overrides(
        {"parameter.Z2.equivalence_slope_pp_per_bpm": 1.45},
        baseline=baseline,
    )
    baseline_registry = build_model_registry(baseline)
    registry = build_model_registry(experimental, baseline=baseline)
    slope = registry["parameter.Z2.equivalence_slope_pp_per_bpm"]

    assert slope["version"] == INTRA_ZONE_EQUIVALENCE_VERSION
    assert slope["initial_value"] == pytest.approx(3.0)
    assert slope["current_value"] == pytest.approx(1.45)
    assert slope["value_source"] != baseline_registry[
        "parameter.Z2.equivalence_slope_pp_per_bpm"
    ]["value_source"]
    assert experimental.equivalence_version == INTRA_ZONE_EQUIVALENCE_VERSION
    assert experimental.fingerprint != baseline.fingerprint


def test_registry_exposes_fixed_tref_bounds_and_no_legacy_tref_value() -> None:
    registry = build_model_registry(default_shadow_configuration())
    legacy_fields = {
        "weight_low",
        "weight_high",
        "power",
        "tref_minutes",
        "bounds_factor",
    }

    for zone, (expected_min, expected_max) in TREF_BOUNDS_MINUTES.items():
        lower = registry[f"parameter.{zone}.tref_min"]
        upper = registry[f"parameter.{zone}.tref_max"]
        assert lower["initial_value"] == pytest.approx(expected_min)
        assert lower["current_value"] == pytest.approx(expected_min)
        assert upper["initial_value"] == pytest.approx(expected_max)
        assert upper["current_value"] == pytest.approx(expected_max)
        assert lower["editable"] is False
        assert upper["editable"] is False
        assert lower["version"] == TREF_PROFILE_VERSION
        assert upper["version"] == TREF_PROFILE_VERSION
    assert not any(
        item_id.rsplit(".", 1)[-1] in legacy_fields
        for item_id in registry
    )
    assert "result.equivalent_time" in registry
    assert "result.q" not in registry
    assert "result.qref" not in registry


@pytest.mark.parametrize(
    ("item_id", "value"),
    (
        ("parameter.Z1.equivalence_slope_pp_per_bpm", -0.01),
        ("parameter.Z1.equivalence_slope_pp_per_bpm", 100.01),
        ("parameter.Z2.spill_threshold_fraction", -0.01),
        ("parameter.Z2.spill_down_fraction", 1.01),
        ("parameter.Z2.spill_up_fraction", -1.0),
    ),
)
def test_experimental_values_outside_allowed_ranges_are_rejected(
    item_id: str,
    value: float,
) -> None:
    with pytest.raises(ValueError, match="must be between"):
        configuration_with_overrides({item_id: value})


@pytest.mark.parametrize(
    "item_id",
    (
        "parameter.Z2.tref_min",
        "parameter.Z2.tref_max",
        "parameter.Z2.tref_minutes",
        "parameter.Z2.profile_version",
        "parameter.Z2.equivalence_version",
        "parameter.Z2.tref_profile_version",
        "parameter.Z2.power",
    ),
)
def test_fixed_version_and_legacy_parameters_cannot_be_overridden(
    item_id: str,
) -> None:
    with pytest.raises(ValueError, match="read-only"):
        configuration_with_overrides({item_id: 1.0})


def test_safe_round_trip_preserves_versions_and_reset_restores_initial() -> None:
    baseline = default_shadow_configuration()
    changed = configuration_with_overrides(
        {
            "parameter.Z1.equivalence_slope_pp_per_bpm": 1.9,
            "parameter.Z5.spill_up_fraction": 0.0,
        }
    )
    payload = configuration_to_safe_dict(changed)

    restored_state = configuration_from_safe_dict(payload)
    reset = reset_shadow_configuration()

    assert restored_state == changed
    assert payload["equivalence_version"] == INTRA_ZONE_EQUIVALENCE_VERSION
    assert payload["tref_profile_version"] == TREF_PROFILE_VERSION
    assert changed.fingerprint != baseline.fingerprint
    assert reset == baseline
    assert reset.overrides == ()

    with pytest.raises(ValueError, match="equivalence version"):
        configuration_from_safe_dict(
            {**payload, "equivalence_version": "stale-linear-model"}
        )


def test_slope_override_recalculates_only_the_single_equivalent_time_dose() -> None:
    baseline = default_shadow_configuration()
    experimental = configuration_with_overrides(
        {"parameter.Z2.equivalence_slope_pp_per_bpm": 1.0},
        baseline=baseline,
    )

    comparison = calculate_shadow_comparison(
        _real_interval_result(126.0),
        experimental_configuration=experimental,
    )
    z2 = next(
        row for row in comparison["comparison_rows"] if row["zone"] == "Z2"
    )

    assert z2["baseline_T_z"] == pytest.approx(z2["experimental_T_z"])
    assert z2["baseline_T_eq_z"] == pytest.approx(0.43)
    assert z2["experimental_T_eq_z"] == pytest.approx(0.81)
    assert z2["delta_T_eq_z"] == pytest.approx(0.38)
    assert z2["baseline_direct_ratio"] != pytest.approx(
        z2["experimental_direct_ratio"]
    )
    assert comparison["baseline"]["rows"]
    assert comparison["experimental"]["rows"]
    assert comparison["experimental"]["experimental"] is True
    assert comparison["intrazone_calculation_count"] == 2


def test_equivalent_time_drives_cascade_bidirectional_spill_and_effect() -> None:
    result = calculate_shadow_result(
        _analysis_with_equivalent_minutes({"Z2": 100.0}),
        default_shadow_configuration(),
    )
    rows = {row["zone"]: row for row in result["rows"]}

    # With no history Z2 uses its upper bound 180: excess = 100 - 0.5 x 180.
    assert rows["Z2"]["T_eq_z"] == pytest.approx(100.0)
    assert rows["Z2"]["tref_effective"] == pytest.approx(180.0)
    assert rows["Z2"]["direct_ratio"] == pytest.approx(100.0 / 180.0)
    assert rows["Z2"]["spillover_excess"] == pytest.approx(10.0)
    assert rows["Z1"]["spillover_received"] == pytest.approx(2.0)
    assert rows["Z3"]["spillover_received"] == pytest.approx(1.0)
    assert rows["Z1"]["cascade"] == pytest.approx(100.0)
    assert rows["Z1"]["E_z"] == pytest.approx(102.0)
    assert rows["Z3"]["E_z"] == pytest.approx(1.0)
    for row in rows.values():
        assert {
            "T_z",
            "T_eq_z",
            "direct_ratio",
            "cascade",
            "spillover_received",
            "E_z",
            "h40_equivalent_minutes",
            "tref_effective",
            "tref_min_effective",
            "tref_max_effective",
            "tref_bound_applied",
        } <= row.keys()


def test_shadow_layer_is_memory_only_and_integrated_without_streamlit() -> None:
    comparison = calculate_shadow_comparison(_real_interval_result())
    repository_root = Path(__file__).resolve().parents[2]
    main_source = (repository_root / "app.py").read_text(encoding="utf-8")
    shadow_source = (
        repository_root / "intervals_inspector" / "shadow_model.py"
    ).read_text(encoding="utf-8")

    assert comparison["memory_only"] is True
    assert comparison["persistence_backend"] is None
    assert comparison["intrazone_calculation_count"] == 1
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
    assert "qref" not in lowered
    assert '"q_z"' not in lowered
    assert "baseline_q_z" not in lowered
    assert "experimental_q_z" not in lowered
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
