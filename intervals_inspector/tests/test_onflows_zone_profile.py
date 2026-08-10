from __future__ import annotations

from copy import deepcopy

import pytest

from intervals_inspector.onflows_zone_profile import (
    DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM,
    DEFAULT_PROFILE_ROWS,
    DEFAULT_PROFILE_SOURCE,
    INTRA_ZONE_EQUIVALENCE_VERSION,
    MANUAL_PROFILE_SOURCE,
    PROFILE_SCHEMA_VERSION,
    build_onflows_zone_profile,
    default_onflows_zone_profile,
    profile_edit_rows,
    profile_from_safe_dict,
    safe_profile_dict,
)


def test_default_profile_exactly_matches_linear_equivalence_configuration() -> None:
    profile = default_onflows_zone_profile()

    assert profile.schema_version == PROFILE_SCHEMA_VERSION
    assert profile.equivalence_version == INTRA_ZONE_EQUIVALENCE_VERSION
    assert profile.source == DEFAULT_PROFILE_SOURCE
    assert profile_edit_rows(profile) == [dict(row) for row in DEFAULT_PROFILE_ROWS]
    assert len(profile.zones) == 5
    assert all(
        zone.equivalence_slope_pp_per_bpm
        == DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM
        == 3.0
        for zone in profile.zones
    )
    assert all(
        "weight_low" not in row
        and "weight_high" not in row
        and "power" not in row
        for row in profile_edit_rows(profile)
    )


def test_default_integer_boundaries_use_shared_half_bpm_dividers() -> None:
    profile = default_onflows_zone_profile()

    assert profile.zones[0].membership_high_bpm == 125.5
    assert profile.zones[0].membership_upper_inclusive is False
    assert profile.zones[1].membership_low_bpm == 125.5
    assert profile.zones[1].membership_lower_inclusive is True
    assert profile.zones[1].membership_high_bpm == 145.5
    # Valid quality-controlled samples above the Z5 reference HRmax remain Z5;
    # only their equivalence coefficient is capped at the configured HRmax.
    assert profile.zones[-1].hr_high == 195.0
    assert profile.zones[-1].membership_high_bpm == 300.0
    assert profile.zones[-1].membership_upper_inclusive is True


def test_fingerprint_is_deterministic_excludes_source_and_includes_slope() -> None:
    default = default_onflows_zone_profile()
    again = default_onflows_zone_profile()
    manual = build_onflows_zone_profile(
        DEFAULT_PROFILE_ROWS,
        source=MANUAL_PROFILE_SOURCE,
    )
    changed_rows = deepcopy([dict(row) for row in DEFAULT_PROFILE_ROWS])
    changed_rows[0]["equivalence_slope_pp_per_bpm"] = 2.5
    changed = build_onflows_zone_profile(
        changed_rows,
        source=MANUAL_PROFILE_SOURCE,
    )

    assert default.fingerprint == again.fingerprint == manual.fingerprint
    assert changed.fingerprint != default.fingerprint
    assert len(default.fingerprint) == len(changed.fingerprint) == 64
    assert "athlete" not in default.fingerprint


def test_safe_profile_round_trip_preserves_version_slope_and_fingerprint() -> None:
    profile = default_onflows_zone_profile()
    safe = safe_profile_dict(profile)

    restored = profile_from_safe_dict(safe)

    assert restored == profile
    assert safe["schema_version"] == PROFILE_SCHEMA_VERSION
    assert safe["equivalence_version"] == INTRA_ZONE_EQUIVALENCE_VERSION
    assert safe["fingerprint"] == profile.fingerprint
    assert safe["zones"][0]["equivalence_slope_pp_per_bpm"] == 3.0
    assert "membership_high_bpm" in safe["zones"][0]


def test_independently_configured_zone_slopes_are_preserved() -> None:
    rows = deepcopy([dict(row) for row in DEFAULT_PROFILE_ROWS])
    expected = [1.0, 2.0, 3.0, 4.0, 5.0]
    for row, slope in zip(rows, expected):
        row["equivalence_slope_pp_per_bpm"] = slope

    profile = build_onflows_zone_profile(rows, source=MANUAL_PROFILE_SOURCE)

    assert [zone.equivalence_slope_pp_per_bpm for zone in profile.zones] == expected
    assert profile_edit_rows(profile) == rows


@pytest.mark.parametrize(
    ("mutator", "message"),
    (
        (lambda rows: rows.__setitem__(1, {**rows[1], "zone": "Z1"}), "unique"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "zone": ""}), "unique"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "hr_high": 100}), "hr_low"),
        (
            lambda rows: rows.__setitem__(1, {**rows[1], "hr_low": 125}),
            "non-overlapping",
        ),
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "equivalence_slope_pp_per_bpm": -0.1}
            ),
            "between 0 and 100",
        ),
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "equivalence_slope_pp_per_bpm": 100.1}
            ),
            "between 0 and 100",
        ),
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "equivalence_slope_pp_per_bpm": float("nan")}
            ),
            "finite",
        ),
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "equivalence_slope_pp_per_bpm": None}
            ),
            "finite",
        ),
    ),
)
def test_invalid_profile_rows_are_rejected(mutator, message: str) -> None:
    rows = deepcopy([dict(row) for row in DEFAULT_PROFILE_ROWS])
    mutator(rows)

    with pytest.raises(ValueError, match=message):
        build_onflows_zone_profile(rows, source=MANUAL_PROFILE_SOURCE)


@pytest.mark.parametrize("zone_count", [1, 3, 5, 7])
def test_profile_supports_arbitrary_zone_count(zone_count: int) -> None:
    rows = [
        {
            "zone": f"B{index + 1}",
            "hr_low": 60 + index * 20,
            "hr_high": 79 + index * 20,
            "equivalence_slope_pp_per_bpm": 1.0 + index / 10,
        }
        for index in range(zone_count)
    ]

    profile = build_onflows_zone_profile(rows, source=MANUAL_PROFILE_SOURCE)

    assert len(profile.zones) == zone_count
    assert profile.equivalence_version == INTRA_ZONE_EQUIVALENCE_VERSION
    assert [zone.equivalence_slope_pp_per_bpm for zone in profile.zones] == [
        1.0 + index / 10 for index in range(zone_count)
    ]


def test_profile_state_with_old_or_missing_version_is_rejected() -> None:
    safe = safe_profile_dict(default_onflows_zone_profile())

    for schema_version in ("onflows-zone-profile-v1", None):
        candidate = dict(safe)
        candidate["schema_version"] = schema_version
        with pytest.raises(ValueError, match="schema version"):
            profile_from_safe_dict(candidate)
