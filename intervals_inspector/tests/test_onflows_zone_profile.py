from __future__ import annotations

from copy import deepcopy

import pytest

from intervals_inspector.onflows_zone_profile import (
    DEFAULT_PROFILE_ROWS,
    DEFAULT_PROFILE_SOURCE,
    MANUAL_PROFILE_SOURCE,
    PROFILE_SCHEMA_VERSION,
    build_onflows_zone_profile,
    default_onflows_zone_profile,
    profile_edit_rows,
    profile_from_safe_dict,
    safe_profile_dict,
)


def test_default_profile_exactly_matches_demo_configuration() -> None:
    profile = default_onflows_zone_profile()

    assert profile.schema_version == PROFILE_SCHEMA_VERSION
    assert profile.source == DEFAULT_PROFILE_SOURCE
    assert profile_edit_rows(profile) == [dict(row) for row in DEFAULT_PROFILE_ROWS]
    assert len(profile.zones) == 5


def test_default_integer_boundaries_use_shared_half_bpm_dividers() -> None:
    profile = default_onflows_zone_profile()

    assert profile.zones[0].membership_high_bpm == 125.5
    assert profile.zones[0].membership_upper_inclusive is False
    assert profile.zones[1].membership_low_bpm == 125.5
    assert profile.zones[1].membership_lower_inclusive is True
    assert profile.zones[1].membership_high_bpm == 145.5
    assert profile.zones[-1].membership_high_bpm == 195.0
    assert profile.zones[-1].membership_upper_inclusive is True


def test_fingerprint_is_deterministic_and_excludes_source_metadata() -> None:
    default = default_onflows_zone_profile()
    again = default_onflows_zone_profile()
    manual = build_onflows_zone_profile(
        DEFAULT_PROFILE_ROWS,
        source=MANUAL_PROFILE_SOURCE,
    )

    assert default.fingerprint == again.fingerprint == manual.fingerprint
    assert len(default.fingerprint) == 64
    assert "athlete" not in default.fingerprint


def test_safe_profile_round_trip_preserves_fingerprint() -> None:
    profile = default_onflows_zone_profile()
    safe = safe_profile_dict(profile)

    restored = profile_from_safe_dict(safe)

    assert restored == profile
    assert safe["fingerprint"] == profile.fingerprint
    assert "membership_high_bpm" in safe["zones"][0]


def test_weight_discontinuity_is_allowed_but_warned() -> None:
    rows = [dict(row) for row in DEFAULT_PROFILE_ROWS[:2]]
    rows[1]["weight_low"] = 130.0

    profile = build_onflows_zone_profile(
        rows,
        source=MANUAL_PROFILE_SOURCE,
    )

    assert profile.warnings[0].code == "interzone_weight_discontinuity"
    assert profile.warnings[0].zone == "Z1->Z2"


@pytest.mark.parametrize(
    ("mutator", "message"),
    (
        (lambda rows: rows.__setitem__(1, {**rows[1], "zone": "Z1"}), "unique"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "zone": ""}), "unique"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "hr_high": 100}), "hr_low"),
        (lambda rows: rows.__setitem__(1, {**rows[1], "hr_low": 125}), "non-overlapping"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "weight_low": 0}), "weight_low"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "weight_high": 99}), "weight_high"),
        (
            lambda rows: rows.__setitem__(
                0,
                {**rows[0], "weight_low": 1e-308, "weight_high": 1e308},
            ),
            "must be finite",
        ),
        (lambda rows: rows.__setitem__(0, {**rows[0], "power": 0}), "power"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "power": float("nan")}), "finite"),
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
            "weight_low": 100 + index * 10,
            "weight_high": 110 + index * 10,
            "power": 1.0 + index / 10,
        }
        for index in range(zone_count)
    ]

    profile = build_onflows_zone_profile(
        rows,
        source=MANUAL_PROFILE_SOURCE,
    )

    assert len(profile.zones) == zone_count
