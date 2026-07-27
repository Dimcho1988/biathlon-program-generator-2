from __future__ import annotations

import math
from copy import deepcopy
from datetime import date

import pandas as pd
import pytest

from biathlon.constants import COMPONENTS, fresh_parameters
from biathlon.demo_data import generate_demo_bundle
from biathlon.mesocycles import normalize_camp_prescriptions
from biathlon.planning import build_weekly_targets
from biathlon.preferences import default_planning_preferences, normalize_preferences
from biathlon.service import _hash_inputs, analyze_athlete


START = pd.Timestamp("2026-01-05")
CALENDAR_COLUMNS = [
    "event_id",
    "athlete_id",
    "type",
    "name",
    "start_date",
    "end_date",
    "priority",
    "goal",
    "locked",
    "note",
]
CAMP_PRESCRIPTION_COLUMNS = [
    "event_id",
    "athlete_id",
    "schema_version",
    "mesocycle_type",
    "mesocycle_length_weeks",
    "accent_mode",
    "accent_limit",
    *[f"accent_{component}" for component in COMPONENTS],
    "volume_factor",
    "stress_factor",
    "maintenance_factor",
    "post_camp_behavior",
    "post_camp_recovery_weeks",
    "note",
]


def _neutral_load_stats() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "base_load": [10.0] * len(COMPONENTS),
            "E40_daily": [10.0] * len(COMPONENTS),
        },
        index=COMPONENTS,
    )


def _neutral_integrated() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "adaptive_multiplier": [1.0] * len(COMPONENTS),
            "integrated_readiness": [100.0] * len(COMPONENTS),
            "hard_flag": [False] * len(COMPONENTS),
        },
        index=COMPONENTS,
    )


def _event(
    event_id: str,
    event_type: str,
    start: pd.Timestamp,
    end: pd.Timestamp | None = None,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "athlete_id": "A",
        "type": event_type,
        "name": event_id,
        "start_date": start,
        "end_date": end or start,
        "priority": "B",
        "goal": "",
        "locked": False,
        "note": "",
    }


def _prescription(
    event_id: str,
    *,
    mesocycle_type: str = "AUTO",
    mesocycle_length_weeks: int = 0,
    accent_mode: str = "AUTO",
    accent_limit: int = 2,
    accents: dict[str, float] | None = None,
    volume_factor: float = 1.07,
    stress_factor: float = 1.07,
    maintenance_factor: float = 0.98,
    post_camp_behavior: str = "AUTO",
    note: str = "",
) -> dict[str, object]:
    accents = accents or {}
    return {
        "event_id": event_id,
        "athlete_id": "A",
        "schema_version": 1,
        "mesocycle_type": mesocycle_type,
        "mesocycle_length_weeks": mesocycle_length_weeks,
        "accent_mode": accent_mode,
        "accent_limit": accent_limit,
        **{
            f"accent_{component}": float(accents.get(component, 0.0))
            for component in COMPONENTS
        },
        "volume_factor": volume_factor,
        "stress_factor": stress_factor,
        "maintenance_factor": maintenance_factor,
        "post_camp_behavior": post_camp_behavior,
        "post_camp_recovery_weeks": 1,
        "note": note,
    }


def _targets(
    calendar: pd.DataFrame,
    *,
    start: pd.Timestamp = START,
    preferences: dict[str, object] | None = None,
    camp_prescriptions: pd.DataFrame | None = None,
    integrated: pd.DataFrame | None = None,
) -> pd.DataFrame:
    return build_weekly_targets(
        _neutral_load_stats(),
        integrated if integrated is not None else _neutral_integrated(),
        calendar,
        "A",
        fresh_parameters(),
        start_date=start,
        minimum_weeks=8,
        planning_preferences=preferences
        or default_planning_preferences("A", date(2026, 1, 5)),
        annual_context={"volume_factor": 1.0},
        camp_prescriptions=camp_prescriptions,
    )


def test_current_four_week_mesocycle_pattern_is_characterized() -> None:
    calendar = pd.DataFrame(
        [_event("main", "MAIN_RACE", START + pd.Timedelta(days=56))],
        columns=CALENDAR_COLUMNS,
    )

    targets = _targets(calendar)
    z1 = targets.loc[targets["component"] == "Z1"].reset_index(drop=True)

    assert z1.loc[:3, "mesocycle_week"].tolist() == [1, 2, 3, 4]
    assert z1.loc[3, "status"].startswith("Възстановителна седмица")


@pytest.mark.parametrize(
    ("component", "expected_factor"),
    [
        ("Z1", 1.07),
        ("Z2", 1.07),
        ("Z3", 1.07),
        ("Z4", 0.98),
        ("Z5", 0.98),
        ("STR", 1.07),
    ],
)
def test_legacy_camp_keeps_numeric_factors_but_displaces_recovery(
    component: str,
    expected_factor: float,
) -> None:
    calendar = pd.DataFrame(
        [
            _event(
                "camp",
                "CAMP",
                START + pd.Timedelta(days=21),
                START + pd.Timedelta(days=27),
            ),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=56)),
        ],
        columns=CALENDAR_COLUMNS,
    )

    targets = _targets(calendar)
    row = targets.loc[
        (targets["week_no"] == 4) & (targets["component"] == component)
    ].iloc[0]

    assert row["calendar_factor"] == pytest.approx(expected_factor)
    assert row["mesocycle_type"] == "MAINTAIN"
    assert str(row["status"]).startswith("Лагер · поддържащ")
    displaced = targets.loc[
        (targets["week_no"] == 5) & (targets["component"] == component)
    ].iloc[0]
    assert displaced["mesocycle_type"] == "RECOVERY"
    assert bool(displaced["recovery_displaced"])


def test_mesocycle_anchor_keeps_position_stable_across_recalculation_dates() -> None:
    calendar = pd.DataFrame(
        [_event("main", "MAIN_RACE", START + pd.Timedelta(days=70))],
        columns=CALENDAR_COLUMNS,
    )
    preferences = default_planning_preferences("A", date(2026, 1, 5))
    preferences["mesocycle_anchor_date"] = START

    day_eight = _targets(
        calendar,
        start=START + pd.Timedelta(days=7),
        preferences=preferences,
    )
    day_nine = _targets(
        calendar,
        start=START + pd.Timedelta(days=8),
        preferences=preferences,
    )

    assert int(day_eight.iloc[0]["mesocycle_week"]) == 2
    assert int(day_nine.iloc[0]["mesocycle_week"]) == 2
    assert day_eight.iloc[0]["mesocycle_id"] == day_nine.iloc[0]["mesocycle_id"]
    assert float(day_eight.iloc[0]["phase_progress"]) == pytest.approx(0.0)
    assert float(day_nine.iloc[0]["phase_progress"]) == pytest.approx(0.0)


def test_post_camp_recovery_survives_neighboring_analysis_dates() -> None:
    calendar = pd.DataFrame(
        [
            _event("camp", "CAMP", START),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescription = _prescription(
        "camp",
        mesocycle_type="BUILD",
        post_camp_behavior="RECOVERY",
    )
    prescription["post_camp_recovery_weeks"] = 2
    prescriptions = pd.DataFrame(
        [prescription],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )
    preferences = default_planning_preferences("A", START)
    preferences["mesocycle_anchor_date"] = START

    day_eight = _targets(
        calendar,
        start=START + pd.Timedelta(days=7),
        preferences=preferences,
        camp_prescriptions=prescriptions,
    )
    day_nine = _targets(
        calendar,
        start=START + pd.Timedelta(days=8),
        preferences=preferences,
        camp_prescriptions=prescriptions,
    )

    for targets in (day_eight, day_nine):
        current = targets.loc[targets["week_no"] == 1]
        assert set(current["mesocycle_type"]) == {"RECOVERY"}
        assert set(current["recovery_origin"]) == {"camp"}


def test_default_camp_accent_limit_controls_unprescribed_camp() -> None:
    calendar = pd.DataFrame(
        [
            _event("camp", "CAMP", START),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    one_accent = default_planning_preferences("A", START)
    one_accent["camp_default_accent_limit"] = 1
    four_accents = default_planning_preferences("A", START)
    four_accents["camp_default_accent_limit"] = 4

    one = _targets(calendar, preferences=one_accent)
    four = _targets(calendar, preferences=four_accents)
    one_camp = one.loc[one["week_no"] == 1, "accent_components"].iloc[0]
    four_camp = four.loc[four["week_no"] == 1, "accent_components"].iloc[0]

    assert len(one_camp.split(", ")) == 1
    assert len(four_camp.split(", ")) == 4
    assert not one.loc[
        one["week_no"] == 2,
        "target_index",
    ].reset_index(drop=True).equals(
        four.loc[
            four["week_no"] == 2,
            "target_index",
        ].reset_index(drop=True)
    )


def test_configurable_five_week_mesocycle_moves_recovery_boundary() -> None:
    calendar = pd.DataFrame(
        [_event("main", "MAIN_RACE", START + pd.Timedelta(days=70))],
        columns=CALENDAR_COLUMNS,
    )
    preferences = default_planning_preferences("A", date(2026, 1, 5))
    preferences.update(
        {
            "mesocycle_anchor_date": START,
            "mesocycle_length_weeks": 5,
        }
    )

    targets = _targets(calendar, preferences=preferences)
    z1 = targets.loc[targets["component"] == "Z1"].reset_index(drop=True)

    assert z1.loc[:4, "mesocycle_week"].tolist() == [1, 2, 3, 4, 5]
    assert not str(z1.loc[3, "status"]).startswith("Възстановителна седмица")
    assert str(z1.loc[4, "status"]).startswith("Възстановителна седмица")


def test_structured_camp_displaces_recovery_until_after_camp() -> None:
    calendar = pd.DataFrame(
        [
            _event(
                "camp",
                "CAMP",
                START + pd.Timedelta(days=21),
                START + pd.Timedelta(days=27),
            ),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [_prescription("camp")],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    targets = _targets(calendar, camp_prescriptions=prescriptions)
    week_four = targets.loc[targets["week_no"] == 4]
    week_five = targets.loc[targets["week_no"] == 5]

    assert set(week_four["mesocycle_type"]) == {"MAINTAIN"}
    assert not week_four["status"].str.startswith("Възстановителна седмица").any()
    assert set(week_five["mesocycle_type"]) == {"RECOVERY"}
    assert week_five["recovery_displaced"].all()


def test_explicit_recovery_camp_is_not_promoted_to_loading() -> None:
    calendar = pd.DataFrame(
        [
            _event(
                "camp",
                "CAMP",
                START + pd.Timedelta(days=21),
                START + pd.Timedelta(days=27),
            ),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="RECOVERY",
                note="Изрично възстановително задание.",
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    targets = _targets(calendar, camp_prescriptions=prescriptions)
    week_four = targets.loc[targets["week_no"] == 4]

    assert set(week_four["mesocycle_type"]) == {"RECOVERY"}
    assert week_four["status"].str.startswith("Възстановителна седмица").all()


def test_post_camp_recovery_prioritizes_complementary_components_relatively() -> None:
    calendar = pd.DataFrame(
        [
            _event(
                "camp",
                "CAMP",
                START + pd.Timedelta(days=21),
                START + pd.Timedelta(days=27),
            ),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accent_limit=4,
                accents={"Z1": 1.0, "Z3": 1.0, "Z5": 1.0, "STR": 1.0},
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    targets = _targets(calendar, camp_prescriptions=prescriptions)
    recovery = targets.loc[targets["week_no"] == 5].set_index("component")

    assert set(recovery["accent_components"]) == {"Z2, Z4"}
    assert recovery.loc["Z2", "component_role"] == "Допълващ акцент"
    assert recovery.loc["Z4", "component_role"] == "Допълващ акцент"
    assert recovery.loc["Z2", "target_index"] > recovery.loc["Z1", "target_index"]


def test_manual_camp_accents_control_component_factors() -> None:
    calendar = pd.DataFrame(
        [
            _event("camp", "CAMP", START, START + pd.Timedelta(days=6)),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accent_limit=4,
                accents={"Z1": 1.0, "Z3": 1.0, "Z5": 1.0, "STR": 1.0},
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    targets = _targets(calendar, camp_prescriptions=prescriptions)
    first_week = targets.loc[targets["week_no"] == 1].set_index("component")

    assert set(first_week["accent_components"]) == {"Z1, Z3, Z5, STR"}
    assert first_week.loc["Z1", "calendar_factor"] == pytest.approx(1.07)
    assert first_week.loc["Z3", "calendar_factor"] == pytest.approx(1.07)
    assert first_week.loc["Z5", "calendar_factor"] == pytest.approx(1.07)
    assert first_week.loc["STR", "calendar_factor"] == pytest.approx(1.07)
    assert first_week.loc["Z2", "calendar_factor"] == pytest.approx(0.98)
    assert first_week.loc["Z4", "calendar_factor"] == pytest.approx(0.98)
    assert first_week.loc["Z1", "component_role"] == "Акцент"
    assert first_week.loc["Z2", "component_role"] == "Поддържане"


def test_taper_suppresses_structured_camp_load_increase() -> None:
    calendar = pd.DataFrame(
        [
            _event("camp", "CAMP", START, START + pd.Timedelta(days=6)),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=7)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accents={"Z1": 1.0, "Z3": 1.0},
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    targets = _targets(calendar, camp_prescriptions=prescriptions)
    first_week = targets.loc[targets["week_no"] == 1]

    assert set(first_week["mesocycle_type"]) == {"TAPER"}
    assert first_week["taper_locked"].all()
    assert first_week["calendar_factor"].eq(1.0).all()


def test_old_preferences_receive_safe_mesocycle_defaults() -> None:
    old = {
        "sessions_per_week": 7,
        "rest_days": [0],
    }

    normalized = normalize_preferences(old, "A", START)

    assert normalized["mesocycle_length_weeks"] == 4
    assert pd.Timestamp(normalized["mesocycle_anchor_date"]) == START


def test_partial_structured_camp_week_scales_factors_by_overlap() -> None:
    calendar = pd.DataFrame(
        [
            _event("camp", "CAMP", START, START),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accent_limit=1,
                accents={"Z1": 1.0},
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    first_week = _targets(
        calendar,
        camp_prescriptions=prescriptions,
    ).loc[lambda frame: frame["week_no"] == 1].set_index("component")

    assert first_week.loc["Z1", "camp_overlap_days"] == 1
    assert first_week.loc["Z1", "calendar_factor"] == pytest.approx(1.01)
    assert first_week.loc["Z2", "calendar_factor"] == pytest.approx(
        1.0 + (0.98 - 1.0) / 7.0
    )


def test_overlapping_structured_camps_use_one_deterministic_priority() -> None:
    low_priority = _event("camp-b", "CAMP", START, START + pd.Timedelta(days=6))
    high_priority = _event("camp-a", "CAMP", START, START + pd.Timedelta(days=6))
    low_priority["priority"] = "B"
    high_priority["priority"] = "A"
    calendar = pd.DataFrame(
        [
            low_priority,
            high_priority,
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp-a",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accent_limit=1,
                accents={"Z1": 1.0},
                volume_factor=1.10,
            ),
            _prescription(
                "camp-b",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accent_limit=1,
                accents={"Z1": 1.0},
                volume_factor=1.20,
            ),
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    first_week = _targets(
        calendar,
        camp_prescriptions=prescriptions,
    ).loc[lambda frame: frame["week_no"] == 1].set_index("component")

    assert set(first_week["camp_event_id"]) == {"camp-a"}
    assert first_week.loc["Z1", "calendar_factor"] == pytest.approx(1.10)


def test_demo_bundle_contains_explicit_camp_prescriptions() -> None:
    bundle = generate_demo_bundle(history_days=30)
    camp_ids = set(
        bundle["calendar"].loc[
            bundle["calendar"]["type"] == "CAMP", "event_id"
        ].astype(str)
    )

    assert camp_ids
    assert camp_ids == set(bundle["camp_prescriptions"]["event_id"].astype(str))


def test_future_camp_changes_plan_but_not_current_readiness_history() -> None:
    base = generate_demo_bundle(history_days=90)
    changed = deepcopy(base)
    changed["camp_prescriptions"].loc[
        changed["camp_prescriptions"]["athlete_id"] == "A",
        "volume_factor",
    ] = 1.20

    before = analyze_athlete(base, "A", generate_plan=False)
    after = analyze_athlete(changed, "A", generate_plan=False)

    pd.testing.assert_frame_equal(before["daily_loads"], after["daily_loads"])
    pd.testing.assert_frame_equal(
        before["load_readiness"],
        after["load_readiness"],
    )
    assert not before["weekly_targets"]["target_index"].equals(
        after["weekly_targets"]["target_index"]
    )


def test_camp_prescription_values_participate_in_audit_hash() -> None:
    bundle = generate_demo_bundle(history_days=30)
    changed = deepcopy(bundle)
    row_index = changed["camp_prescriptions"].index[0]
    changed["camp_prescriptions"].loc[row_index, "accent_Z3"] = 0.35

    assert _hash_inputs(bundle, "A") != _hash_inputs(changed, "A")


def test_non_aligned_rolling_week_applies_camp_on_its_boundary_day() -> None:
    rolling_start = START + pd.Timedelta(days=1)
    camp_day = START + pd.Timedelta(days=7)
    calendar = pd.DataFrame(
        [
            _event("camp", "CAMP", camp_day),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accent_limit=1,
                accents={"Z1": 1.0},
                volume_factor=1.20,
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )
    preferences = default_planning_preferences("A", START)
    preferences["mesocycle_anchor_date"] = START

    first_week = _targets(
        calendar,
        start=rolling_start,
        preferences=preferences,
        camp_prescriptions=prescriptions,
    ).loc[lambda frame: frame["week_no"] == 1].set_index("component")

    assert set(first_week["camp_event_id"]) == {"camp"}
    assert first_week["camp_overlap_days"].eq(1).all()
    assert first_week.loc["Z1", "calendar_factor"] == pytest.approx(
        1.0 + (1.20 - 1.0) / 7.0
    )


def test_taper_ends_after_main_race_and_next_main_race_takes_control() -> None:
    calendar = pd.DataFrame(
        [
            _event("main-1", "MAIN_RACE", START + pd.Timedelta(days=7)),
            _event("main-2", "MAIN_RACE", START + pd.Timedelta(days=42)),
        ],
        columns=CALENDAR_COLUMNS,
    )

    z1 = (
        _targets(calendar)
        .loc[lambda frame: frame["component"] == "Z1"]
        .set_index("week_no")
    )
    after_first_race = z1.loc[3]
    second_race_taper = z1.loc[5]

    assert after_first_race["main_race"] == "main-2"
    assert int(after_first_race["weeks_to_main_race"]) == 4
    assert after_first_race["mesocycle_type"] != "TAPER"
    assert after_first_race["taper_factor"] == pytest.approx(1.0)
    assert not str(after_first_race["status"]).startswith("Тейпър")

    assert second_race_taper["main_race"] == "main-2"
    assert int(second_race_taper["weeks_to_main_race"]) == 2
    assert second_race_taper["mesocycle_type"] == "TAPER"
    assert second_race_taper["taper_factor"] == pytest.approx(0.84)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("schema_version", "bad"),
        ("mesocycle_length_weeks", "bad"),
        ("accent_limit", "bad"),
        ("accent_Z3", "bad"),
        ("volume_factor", "bad"),
        ("stress_factor", "bad"),
        ("maintenance_factor", "bad"),
        ("post_camp_recovery_weeks", "bad"),
    ],
)
def test_malformed_prescription_numerics_fall_back_without_nan(
    field: str,
    bad_value: object,
) -> None:
    raw = _prescription("camp")
    raw[field] = bad_value

    normalized = normalize_camp_prescriptions(pd.DataFrame([raw]))
    row = normalized.iloc[0]

    assert len(normalized) == 1
    assert int(row["schema_version"]) >= 1
    assert int(row["mesocycle_length_weeks"]) == 0 or 2 <= int(
        row["mesocycle_length_weeks"]
    ) <= 6
    assert 1 <= int(row["accent_limit"]) <= len(COMPONENTS)
    assert 0.0 <= float(row["accent_Z3"]) <= 1.0
    assert 0.75 <= float(row["volume_factor"]) <= 1.25
    assert 0.75 <= float(row["stress_factor"]) <= 1.25
    assert 0.75 <= float(row["maintenance_factor"]) <= 1.10
    assert 0 <= int(row["post_camp_recovery_weeks"]) <= 2
    for numeric_field in [
        "schema_version",
        "mesocycle_length_weeks",
        "accent_limit",
        "accent_Z3",
        "volume_factor",
        "stress_factor",
        "maintenance_factor",
        "post_camp_recovery_weeks",
    ]:
        assert math.isfinite(float(row[numeric_field]))


def test_same_event_id_is_retained_independently_for_each_athlete() -> None:
    athlete_a = _prescription("shared")
    athlete_b = _prescription("shared")
    athlete_b["athlete_id"] = "B"

    normalized = normalize_camp_prescriptions(
        pd.DataFrame([athlete_a, athlete_b])
    )

    assert set(
        normalized[["athlete_id", "event_id"]].itertuples(
            index=False,
            name=None,
        )
    ) == {("A", "shared"), ("B", "shared")}


def test_recovery_prescription_without_note_falls_back_to_auto() -> None:
    raw = _prescription(
        "camp",
        mesocycle_type="RECOVERY",
        note="   ",
    )

    normalized = normalize_camp_prescriptions(pd.DataFrame([raw]))

    assert normalized.iloc[0]["mesocycle_type"] == "AUTO"


def test_distinct_camp_channels_fractional_weights_and_accent_limit() -> None:
    calendar = pd.DataFrame(
        [
            _event("camp", "CAMP", START, START + pd.Timedelta(days=6)),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accent_limit=3,
                accents={
                    "Z1": 0.50,
                    "Z3": 0.80,
                    "Z5": 0.75,
                    "STR": 0.60,
                },
                volume_factor=1.20,
                stress_factor=1.10,
                maintenance_factor=0.90,
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    first_week = _targets(
        calendar,
        camp_prescriptions=prescriptions,
    ).loc[lambda frame: frame["week_no"] == 1].set_index("component")

    assert set(first_week["accent_components"]) == {"Z3, Z5, STR"}
    assert first_week.loc["Z3", "calendar_factor"] == pytest.approx(1.16)
    assert first_week.loc["Z5", "calendar_factor"] == pytest.approx(1.075)
    assert first_week.loc["STR", "calendar_factor"] == pytest.approx(1.06)
    assert first_week.loc["Z1", "calendar_factor"] == pytest.approx(0.90)
    assert first_week.loc["Z2", "calendar_factor"] == pytest.approx(0.90)
    assert first_week.loc["Z4", "calendar_factor"] == pytest.approx(0.90)


def test_multweek_camp_displaces_recovery_until_after_the_whole_block() -> None:
    calendar = pd.DataFrame(
        [
            _event(
                "camp",
                "CAMP",
                START + pd.Timedelta(days=14),
                START + pd.Timedelta(days=27),
            ),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [_prescription("camp", mesocycle_type="BUILD")],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    z1 = (
        _targets(calendar, camp_prescriptions=prescriptions)
        .loc[lambda frame: frame["component"] == "Z1"]
        .set_index("week_no")
    )

    assert z1.loc[3, "mesocycle_type"] == "BUILD"
    assert z1.loc[4, "mesocycle_type"] == "BUILD"
    assert z1.loc[5, "mesocycle_type"] == "RECOVERY"
    assert bool(z1.loc[5, "recovery_displaced"])


def test_camp_specific_length_returns_to_global_default_after_recovery() -> None:
    calendar = pd.DataFrame(
        [
            _event("camp", "CAMP", START, START + pd.Timedelta(days=6)),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=84)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="BUILD",
                mesocycle_length_weeks=6,
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    z1 = (
        _targets(calendar, camp_prescriptions=prescriptions)
        .loc[lambda frame: frame["component"] == "Z1"]
        .set_index("week_no")
    )

    assert z1.loc[1:6, "mesocycle_length_weeks"].eq(6).all()
    assert z1.loc[6, "mesocycle_type"] == "RECOVERY"
    assert int(z1.loc[7, "mesocycle_length_weeks"]) == 4
    assert int(z1.loc[7, "mesocycle_week"]) == 1


def test_taper_camp_does_not_mutate_the_following_mesocycle_state() -> None:
    main = _event(
        "main",
        "MAIN_RACE",
        START + pd.Timedelta(days=21),
    )
    base_calendar = pd.DataFrame([main], columns=CALENDAR_COLUMNS)
    camp_calendar = pd.DataFrame(
        [
            _event(
                "camp",
                "CAMP",
                START + pd.Timedelta(days=21),
                START + pd.Timedelta(days=27),
            ),
            main,
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="BUILD",
                mesocycle_length_weeks=6,
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )

    without_camp = (
        _targets(base_calendar)
        .loc[lambda frame: frame["component"] == "Z1"]
        .set_index("week_no")
    )
    with_camp = (
        _targets(camp_calendar, camp_prescriptions=prescriptions)
        .loc[lambda frame: frame["component"] == "Z1"]
        .set_index("week_no")
    )

    assert with_camp.loc[4, "mesocycle_type"] == "TAPER"
    assert with_camp.loc[4, "calendar_factor"] == pytest.approx(1.0)
    for column in [
        "mesocycle_week",
        "mesocycle_length_weeks",
        "mesocycle_type",
    ]:
        assert with_camp.loc[5, column] == without_camp.loc[5, column]
    for column in ["mesocycle_factor", "target_index"]:
        assert with_camp.loc[5, column] == pytest.approx(
            without_camp.loc[5, column]
        )


def test_hard_flag_suppresses_camp_increase_at_weekly_target_level() -> None:
    calendar_without_camp = pd.DataFrame(
        [_event("main", "MAIN_RACE", START + pd.Timedelta(days=70))],
        columns=CALENDAR_COLUMNS,
    )
    calendar_with_camp = pd.DataFrame(
        [
            _event(
                "camp",
                "CAMP",
                START + pd.Timedelta(days=21),
                START + pd.Timedelta(days=27),
            ),
            _event("main", "MAIN_RACE", START + pd.Timedelta(days=70)),
        ],
        columns=CALENDAR_COLUMNS,
    )
    prescriptions = pd.DataFrame(
        [
            _prescription(
                "camp",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accent_limit=2,
                accents={"Z3": 1.0, "Z5": 1.0},
                volume_factor=1.25,
                stress_factor=1.25,
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )
    hard = _neutral_integrated()
    hard["hard_flag"] = True

    without_camp = (
        _targets(calendar_without_camp, integrated=hard)
        .loc[lambda frame: frame["week_no"] == 4]
        .set_index("component")
    )
    with_camp = (
        _targets(
            calendar_with_camp,
            camp_prescriptions=prescriptions,
            integrated=hard,
        )
        .loc[lambda frame: frame["week_no"] == 4]
        .set_index("component")
    )

    assert with_camp["safety_limited"].all()
    assert with_camp["applied_mesocycle_type"].eq("RECOVERY").all()
    assert (
        with_camp["target_index"]
        <= without_camp["target_index"] + 1e-12
    ).all()


def test_camp_plus_recent_illness_generates_no_high_or_key_session() -> None:
    bundle = generate_demo_bundle(history_days=120)
    today = pd.Timestamp(date.today())
    other_calendar = bundle["calendar"].loc[
        bundle["calendar"]["athlete_id"].astype(str) != "A"
    ].copy()
    athlete_calendar = pd.DataFrame(
        [
            _event(
                "current-camp",
                "CAMP",
                today,
                today + pd.Timedelta(days=6),
            ),
            _event(
                "future-main",
                "MAIN_RACE",
                today + pd.Timedelta(days=70),
            ),
        ],
        columns=CALENDAR_COLUMNS,
    )
    bundle["calendar"] = pd.concat(
        [other_calendar, athlete_calendar],
        ignore_index=True,
    )
    other_prescriptions = bundle["camp_prescriptions"].loc[
        bundle["camp_prescriptions"]["athlete_id"].astype(str) != "A"
    ].copy()
    current_prescription = pd.DataFrame(
        [
            _prescription(
                "current-camp",
                mesocycle_type="BUILD",
                accent_mode="MANUAL",
                accent_limit=3,
                accents={"Z3": 1.0, "Z5": 1.0, "STR": 1.0},
                volume_factor=1.25,
                stress_factor=1.25,
            )
        ],
        columns=CAMP_PRESCRIPTION_COLUMNS,
    )
    bundle["camp_prescriptions"] = pd.concat(
        [other_prescriptions, current_prescription],
        ignore_index=True,
    )
    wellness_mask = bundle["wellness"]["athlete_id"].astype(str) == "A"
    latest_wellness = bundle["wellness"].loc[wellness_mask, "date"].idxmax()
    bundle["wellness"].loc[latest_wellness, "date"] = today
    bundle["wellness"].loc[latest_wellness, "illness"] = True

    analysis = analyze_athlete(
        bundle,
        "A",
        as_of=today,
        generate_plan=True,
    )
    training = analysis["plan"].loc[analysis["plan"]["focus"] != "REST"]

    assert analysis["integrated"]["hard_flag"].all()
    assert not training["key_stimulus"].any()
    assert not training["focus"].isin({"Z3", "Z4", "Z5", "STR"}).any()


def test_mismatched_athlete_sidecar_cannot_change_plan_outside_audit_hash() -> None:
    base = generate_demo_bundle(history_days=30)
    camp = base["calendar"].loc[
        (base["calendar"]["athlete_id"].astype(str) == "A")
        & (base["calendar"]["type"].astype(str) == "CAMP")
    ].iloc[0]
    event_id = str(camp["event_id"])
    base["camp_prescriptions"] = base["camp_prescriptions"].loc[
        ~(
            (base["camp_prescriptions"]["athlete_id"].astype(str) == "A")
            & (
                base["camp_prescriptions"]["event_id"].astype(str)
                == event_id
            )
        )
    ].copy()
    malformed = deepcopy(base)
    wrong_owner = _prescription(
        event_id,
        mesocycle_type="BUILD",
        volume_factor=1.25,
    )
    wrong_owner["athlete_id"] = "B"
    malformed["camp_prescriptions"] = pd.concat(
        [
            malformed["camp_prescriptions"],
            pd.DataFrame(
                [wrong_owner],
                columns=CAMP_PRESCRIPTION_COLUMNS,
            ),
        ],
        ignore_index=True,
    )

    before = analyze_athlete(base, "A", generate_plan=False)
    after = analyze_athlete(malformed, "A", generate_plan=False)

    assert _hash_inputs(base, "A") == _hash_inputs(malformed, "A")
    pd.testing.assert_frame_equal(
        before["weekly_targets"],
        after["weekly_targets"],
    )


def test_read_only_analysis_does_not_migrate_legacy_bundle_inputs() -> None:
    bundle = generate_demo_bundle(history_days=30)
    bundle["planning_preferences"]["A"].pop("mesocycle_anchor_date")
    bundle.pop("camp_prescriptions")
    preferences_before = deepcopy(bundle["planning_preferences"]["A"])

    analyze_athlete(
        bundle,
        "A",
        as_of=START,
        generate_plan=False,
    )

    assert bundle["planning_preferences"]["A"] == preferences_before
    assert "camp_prescriptions" not in bundle
