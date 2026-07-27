"""Stable mesocycle scheduling and structured training-camp directives.

This module changes only the planning layer.  It does not alter the
physiological load, cascade, fatigue, or recovery formulas.
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd

from .constants import COMPONENTS


CAMP_MODES = ("AUTO", "BUILD", "MAINTAIN", "RECOVERY")
CAMP_ACCENT_MODES = ("AUTO", "MANUAL")
CAMP_POST_BEHAVIORS = ("AUTO", "RECOVERY", "COMPLEMENT")

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


def default_camp_prescription(
    event_id: str,
    athlete_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    """Return an independent, auditable default directive for one camp."""

    row: dict[str, Any] = {
        "event_id": str(event_id),
        "athlete_id": str(athlete_id),
        "schema_version": 1,
        "mesocycle_type": "AUTO",
        "mesocycle_length_weeks": 0,
        "accent_mode": "AUTO",
        "accent_limit": 2,
        **{f"accent_{component}": 0.0 for component in COMPONENTS},
        # These reuse the former CAMP factors as transparent editable defaults.
        "volume_factor": 1.07,
        "stress_factor": 1.07,
        "maintenance_factor": 0.98,
        "post_camp_behavior": "AUTO",
        "post_camp_recovery_weeks": 1,
        "note": "",
    }
    row.update(overrides)
    return row


def empty_camp_prescriptions() -> pd.DataFrame:
    return pd.DataFrame(columns=CAMP_PRESCRIPTION_COLUMNS)


def _clean_identifier(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        return ""
    result = str(value).strip()
    return "" if result.lower() in {"nan", "nat", "none"} else result


def _finite_number(value: Any, fallback: float) -> float:
    converted = pd.to_numeric(value, errors="coerce")
    try:
        number = float(converted)
    except (TypeError, ValueError):
        return float(fallback)
    return number if np.isfinite(number) else float(fallback)


def normalize_camp_prescriptions(
    prescriptions: pd.DataFrame | None,
) -> pd.DataFrame:
    """Normalize legacy, partial, and UI-edited sidecar tables safely."""

    if prescriptions is None or prescriptions.empty:
        return empty_camp_prescriptions()

    data = prescriptions.copy()
    for column in CAMP_PRESCRIPTION_COLUMNS:
        if column not in data:
            data[column] = np.nan

    rows: list[dict[str, Any]] = []
    for _, raw in data.iterrows():
        event_id = _clean_identifier(raw.get("event_id", ""))
        athlete_id = _clean_identifier(raw.get("athlete_id", ""))
        if not event_id or not athlete_id:
            continue
        defaults = default_camp_prescription(event_id, athlete_id)
        row = {
            column: raw.get(column, defaults[column])
            for column in CAMP_PRESCRIPTION_COLUMNS
        }
        for column, fallback in defaults.items():
            value = row.get(column)
            if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
                row[column] = fallback

        mode = str(row["mesocycle_type"]).upper()
        row["mesocycle_type"] = mode if mode in CAMP_MODES else "AUTO"
        accent_mode = str(row["accent_mode"]).upper()
        row["accent_mode"] = (
            accent_mode if accent_mode in CAMP_ACCENT_MODES else "AUTO"
        )
        post = str(row["post_camp_behavior"]).upper()
        row["post_camp_behavior"] = (
            post if post in CAMP_POST_BEHAVIORS else "AUTO"
        )
        row["schema_version"] = max(
            1,
            int(_finite_number(row["schema_version"], defaults["schema_version"])),
        )

        length = int(
            _finite_number(
                row["mesocycle_length_weeks"],
                defaults["mesocycle_length_weeks"],
            )
        )
        row["mesocycle_length_weeks"] = (
            0 if length <= 0 else int(np.clip(length, 2, 6))
        )
        row["accent_limit"] = int(
            np.clip(
                int(
                    _finite_number(
                        row["accent_limit"],
                        defaults["accent_limit"],
                    )
                ),
                1,
                6,
            )
        )
        row["post_camp_recovery_weeks"] = int(
            np.clip(
                int(
                    _finite_number(
                        row["post_camp_recovery_weeks"],
                        defaults["post_camp_recovery_weeks"],
                    )
                ),
                0,
                2,
            )
        )
        for component in COMPONENTS:
            column = f"accent_{component}"
            row[column] = float(
                np.clip(
                    _finite_number(row[column], defaults[column]),
                    0.0,
                    1.0,
                )
            )
        row["volume_factor"] = float(
            np.clip(
                _finite_number(
                    row["volume_factor"],
                    defaults["volume_factor"],
                ),
                0.75,
                1.25,
            )
        )
        row["stress_factor"] = float(
            np.clip(
                _finite_number(
                    row["stress_factor"],
                    defaults["stress_factor"],
                ),
                0.75,
                1.25,
            )
        )
        row["maintenance_factor"] = float(
            np.clip(
                _finite_number(
                    row["maintenance_factor"],
                    defaults["maintenance_factor"],
                ),
                0.75,
                1.10,
            )
        )
        row["note"] = str(row.get("note", "") or "")
        if row["mesocycle_type"] == "RECOVERY" and not row["note"].strip():
            row["mesocycle_type"] = "AUTO"
        rows.append(row)

    if not rows:
        return empty_camp_prescriptions()
    return (
        pd.DataFrame(rows, columns=CAMP_PRESCRIPTION_COLUMNS)
        .drop_duplicates(subset=["athlete_id", "event_id"], keep="last")
        .sort_values(["athlete_id", "event_id"])
        .reset_index(drop=True)
    )


def prescription_for_event(
    prescriptions: pd.DataFrame | None,
    event_id: str,
    athlete_id: str,
) -> dict[str, Any] | None:
    data = normalize_camp_prescriptions(prescriptions)
    if data.empty:
        return None
    match = data.loc[
        (data["event_id"].astype(str) == str(event_id))
        & (data["athlete_id"].astype(str) == str(athlete_id))
    ]
    return match.iloc[-1].to_dict() if not match.empty else None


def mesocycle_pattern_for_length(
    base_pattern: Iterable[float],
    length_weeks: int,
) -> list[float]:
    """Resize the editable planning wave while retaining its endpoints.

    The four-week default is returned byte-for-byte.  Other supported lengths
    interpolate only the planning wave; recovery physiology is untouched.
    """

    length = int(np.clip(length_weeks, 2, 6))
    values = [float(value) for value in base_pattern]
    if not values:
        values = [1.0, 0.78]
    if len(values) == 1:
        values = [values[0], 0.78]
    if length == len(values):
        return values

    loading = values[:-1]
    recovery = values[-1]
    loading_weeks = length - 1
    if loading_weeks == 1:
        resized = [float(np.median(loading))]
    else:
        source = np.arange(len(loading), dtype=float)
        target = np.linspace(0.0, float(len(loading) - 1), loading_weeks)
        resized = np.interp(target, source, loading).astype(float).tolist()
    return [*resized, recovery]


def _aligned_week_start(value: pd.Timestamp, anchor: pd.Timestamp) -> pd.Timestamp:
    days = int((pd.Timestamp(value).normalize() - anchor).days)
    return anchor + pd.Timedelta(days=7 * int(np.floor(days / 7.0)))


def _priority_rank(value: Any) -> int:
    return {"A": 0, "B": 1, "C": 2}.get(str(value).upper(), 3)


def _overlap_days(event: pd.Series, week_start: pd.Timestamp) -> int:
    week_end = week_start + pd.Timedelta(days=6)
    start = max(pd.Timestamp(event["start_date"]).normalize(), week_start)
    end = min(pd.Timestamp(event["end_date"]).normalize(), week_end)
    return max(0, int((end - start).days) + 1)


def _taper_locked(
    calendar: pd.DataFrame,
    athlete_id: str,
    week_start: pd.Timestamp,
) -> bool:
    if calendar.empty:
        return False
    main = calendar.loc[
        (calendar["athlete_id"].astype(str) == str(athlete_id))
        & (calendar["type"].astype(str) == "MAIN_RACE")
    ].copy()
    if main.empty:
        return False
    dates = pd.to_datetime(main["start_date"], errors="coerce").dt.normalize()
    days = (dates - week_start).dt.days
    return bool(((days >= 0) & (days <= 14)).any())


def build_mesocycle_schedule(
    week_starts: Iterable[pd.Timestamp],
    calendar: pd.DataFrame,
    camp_prescriptions: pd.DataFrame | None,
    athlete_id: str,
    parameters: dict[str, Any],
    planning_preferences: dict[str, Any],
) -> pd.DataFrame:
    """Build a stable calendar-anchored schedule for rolling seven-day blocks."""

    requested = [pd.Timestamp(value).normalize() for value in week_starts]
    if not requested:
        return pd.DataFrame()

    anchor = pd.Timestamp(
        planning_preferences.get("mesocycle_anchor_date", requested[0])
    ).normalize()
    default_length = int(
        np.clip(planning_preferences.get("mesocycle_length_weeks", 4), 2, 6)
    )
    default_camp_accent_limit = int(
        np.clip(
            planning_preferences.get("camp_default_accent_limit", 2),
            1,
            len(COMPONENTS),
        )
    )
    base_pattern = parameters.get(
        "mesocycle_pattern",
        [0.96, 1.04, 1.10, 0.78],
    )

    prescriptions = normalize_camp_prescriptions(camp_prescriptions)
    prescriptions = prescriptions.loc[
        prescriptions["athlete_id"].astype(str) == str(athlete_id)
    ].copy()
    prescription_map = {
        str(row["event_id"]): row.to_dict()
        for _, row in prescriptions.iterrows()
    }

    required_calendar_columns = {
        "event_id",
        "athlete_id",
        "type",
        "start_date",
        "end_date",
    }
    if calendar.empty or not required_calendar_columns.issubset(calendar.columns):
        camp_data = pd.DataFrame(columns=list(required_calendar_columns))
    else:
        camp_data = calendar.loc[
            (calendar["athlete_id"].astype(str) == str(athlete_id))
            & (calendar["type"].astype(str) == "CAMP")
        ].copy()
    if not camp_data.empty:
        camp_data["start_date"] = pd.to_datetime(
            camp_data["start_date"], errors="coerce"
        ).dt.normalize()
        camp_data["end_date"] = pd.to_datetime(
            camp_data["end_date"], errors="coerce"
        ).dt.normalize()
        camp_data = (
            camp_data.dropna(subset=["start_date", "end_date"])
            .drop_duplicates(
                subset=["athlete_id", "event_id"],
                keep="last",
            )
            .reset_index(drop=True)
        )

    # The mesocycle position is anchored, but CAMP overlap must use the exact
    # rolling interval requested by planning.  Start a same-offset historical
    # grid so earlier camps can still carry recovery debt into the horizon.
    earliest = min(requested)
    latest = max(requested)
    offset_days = int((earliest - anchor).days % 7)
    grid_start = anchor + pd.Timedelta(days=offset_days)
    history_start = min(anchor, earliest)
    if not camp_data.empty:
        historical_camp_starts = camp_data.loc[
            camp_data["start_date"] <= latest,
            "start_date",
        ]
        if not historical_camp_starts.empty:
            history_start = min(
                history_start,
                pd.Timestamp(historical_camp_starts.min()).normalize(),
            )
    if grid_start > history_start:
        lookback_weeks = int(
            np.ceil((grid_start - history_start).days / 7.0)
        )
        grid_start -= pd.Timedelta(days=7 * lookback_weeks)
    grid = pd.date_range(grid_start, latest, freq="7D")

    current_length = default_length
    current_pattern = mesocycle_pattern_for_length(
        base_pattern,
        current_length,
    )
    absolute_week = int(np.floor((grid_start - anchor).days / 7.0))
    position = absolute_week % current_length
    cycle_serial = int(np.floor(absolute_week / current_length))

    recovery_debt = 0
    recovery_origin = ""
    previous_active_id = ""
    previous_config: dict[str, Any] | None = None
    complement_remaining = 0
    length_override_event_id = ""
    reset_length_after_recovery = False
    grid_rows: dict[pd.Timestamp, dict[str, Any]] = {}

    for rolling_start in grid:
        rolling_start = pd.Timestamp(rolling_start).normalize()
        active = pd.DataFrame()
        if not camp_data.empty:
            rolling_end = rolling_start + pd.Timedelta(days=6)
            active = camp_data.loc[
                (camp_data["start_date"] <= rolling_end)
                & (camp_data["end_date"] >= rolling_start)
            ].copy()
            if not active.empty:
                active["_priority"] = active.get(
                    "priority",
                    pd.Series("B", index=active.index),
                ).map(_priority_rank)
                active["_overlap"] = active.apply(
                    lambda event: _overlap_days(event, rolling_start),
                    axis=1,
                )
                active = active.sort_values(
                    ["_priority", "_overlap", "start_date", "event_id"],
                    ascending=[True, False, True, True],
                    kind="stable",
                )

        dominant: pd.Series | None = active.iloc[0] if not active.empty else None
        active_id = str(dominant["event_id"]) if dominant is not None else ""
        has_prescription = active_id in prescription_map
        config = (
            prescription_map[active_id]
            if has_prescription
            else default_camp_prescription(
                active_id,
                str(athlete_id),
                accent_limit=default_camp_accent_limit,
            )
            if active_id
            else None
        )
        taper = _taper_locked(calendar, athlete_id, rolling_start)

        camp_ended = bool(
            not taper
            and dominant is None
            and previous_active_id
            and previous_config is not None
        )
        if camp_ended:
            post = str(previous_config["post_camp_behavior"])
            if post == "RECOVERY":
                recovery_debt = max(
                    recovery_debt,
                    int(previous_config["post_camp_recovery_weeks"]),
                )
                recovery_origin = previous_active_id
            if post in {"AUTO", "COMPLEMENT"}:
                complement_remaining = max(
                    complement_remaining,
                    current_length,
                )
            if length_override_event_id:
                reset_length_after_recovery = True
                if post == "RECOVERY" and recovery_debt == 0:
                    current_length = default_length
                    current_pattern = mesocycle_pattern_for_length(
                        base_pattern,
                        current_length,
                    )
                    position = 0
                    length_override_event_id = ""
                    reset_length_after_recovery = False

        camp_started = bool(
            not taper
            and active_id
            and active_id != previous_active_id
        )
        if camp_started and config is not None:
            override_length = int(config["mesocycle_length_weeks"])
            if override_length:
                if position == current_length - 1:
                    recovery_debt = max(recovery_debt, 1)
                    recovery_origin = active_id
                current_length = override_length
                current_pattern = mesocycle_pattern_for_length(
                    base_pattern,
                    current_length,
                )
                position = min(position, current_length - 1)
                length_override_event_id = active_id

        base_position = position
        base_type = "RECOVERY" if position == current_length - 1 else "BUILD"
        base_factor = float(current_pattern[position])
        effective_type = base_type
        mesocycle_factor = base_factor
        recovery_displaced = False
        override_reason = "Стабилен базов мезоцикъл."
        source_config: dict[str, Any] | None = None
        accent_strategy = "AUTO"
        advance_cycle_after_row = False
        reset_length_after_row = False

        if taper:
            # Taper absorbs pending camp recovery and must not allow a camp to
            # mutate the following mesocycle state invisibly.
            effective_type = "TAPER"
            mesocycle_factor = 1.0
            source_config = config
            accent_strategy = "CAMP" if config is not None else "AUTO"
            recovery_debt = 0
            complement_remaining = 0
            recovery_origin = ""
            length_override_event_id = ""
            reset_length_after_recovery = False
            if base_type == "RECOVERY":
                position = 0
                advance_cycle_after_row = True
            else:
                position += 1
            override_reason = (
                "Тейпърът и основният старт имат приоритет пред лагерното натоварване."
            )
        elif dominant is not None and config is not None:
            source_config = config
            mode = str(config["mesocycle_type"])
            mode = "MAINTAIN" if mode == "AUTO" else mode
            overlap_days = _overlap_days(dominant, rolling_start)
            if mode == "RECOVERY":
                effective_type = "RECOVERY"
                mesocycle_factor = float(current_pattern[-1])
                position = 0
                recovery_debt = 0
                reset_length_after_recovery = bool(length_override_event_id)
                override_reason = (
                    f"Лагерът {active_id} е изрично зададен като възстановителен."
                )
            else:
                if base_type == "RECOVERY":
                    recovery_debt = max(recovery_debt, 1)
                    recovery_origin = active_id
                    recovery_displaced = True
                effective_type = mode
                if mode == "BUILD":
                    loading_reference = (
                        float(current_pattern[position])
                        if position < current_length - 1
                        else 1.04
                    )
                    mesocycle_factor = max(1.04, loading_reference)
                else:
                    mesocycle_factor = 1.0
                if position < current_length - 1:
                    position += 1
                override_reason = (
                    f"Лагерно задание {active_id}: "
                    f"{effective_type.lower()}, {overlap_days}/7 дни."
                )
            accent_strategy = "CAMP"
        elif recovery_debt > 0:
            effective_type = "RECOVERY"
            mesocycle_factor = float(current_pattern[-1])
            recovery_displaced = True
            source_config = previous_config
            accent_strategy = (
                "COMPLEMENT"
                if previous_config is not None
                and str(previous_config["post_camp_behavior"])
                in {"AUTO", "COMPLEMENT"}
                else "AUTO"
            )
            override_reason = (
                "Възстановяването е преместено след лагер "
                f"{recovery_origin or previous_active_id}."
            )
            recovery_debt -= 1
            position = 0
            if recovery_debt == 0:
                advance_cycle_after_row = True
                reset_length_after_row = reset_length_after_recovery
        else:
            if base_type == "RECOVERY":
                position = 0
                advance_cycle_after_row = True
                reset_length_after_row = reset_length_after_recovery
            else:
                position += 1
            if complement_remaining > 0 and previous_config is not None:
                source_config = previous_config
                accent_strategy = "COMPLEMENT"

        if source_config is None:
            source_config = default_camp_prescription("", str(athlete_id))
        camp_ids = (
            ", ".join(active["event_id"].astype(str).tolist())
            if not active.empty
            else ""
        )
        overlap_days = (
            _overlap_days(dominant, rolling_start)
            if dominant is not None
            else 0
        )
        row: dict[str, Any] = {
            "aligned_week_start": rolling_start,
            "mesocycle_id": f"{anchor.date().isoformat()}-{cycle_serial + 1}",
            "base_mesocycle_week": base_position + 1,
            "mesocycle_week": (
                current_length
                if effective_type == "RECOVERY" and recovery_displaced
                else base_position + 1
            ),
            "base_mesocycle_type": base_type,
            "base_mesocycle_factor": base_factor,
            "mesocycle_type": effective_type,
            "mesocycle_length_weeks": current_length,
            "mesocycle_factor": mesocycle_factor,
            "camp_event_id": active_id,
            "camp_ids": camp_ids,
            "camp_overlap_days": overlap_days,
            "camp_has_prescription": has_prescription,
            "recovery_displaced": recovery_displaced,
            "recovery_origin": recovery_origin if recovery_displaced else "",
            "override_reason": override_reason,
            "taper_locked": taper,
            "accent_strategy": accent_strategy,
            "accent_mode": str(source_config["accent_mode"]),
            "accent_limit": int(source_config["accent_limit"]),
            "volume_factor": float(source_config["volume_factor"]),
            "stress_factor": float(source_config["stress_factor"]),
            "maintenance_factor": float(source_config["maintenance_factor"]),
            "camp_directive_note": str(source_config.get("note", "") or ""),
        }
        for component in COMPONENTS:
            row[f"accent_{component}"] = float(
                source_config.get(f"accent_{component}", 0.0)
            )
        grid_rows[rolling_start] = row

        if complement_remaining > 0 and dominant is None and not taper:
            complement_remaining -= 1
        if advance_cycle_after_row:
            cycle_serial += 1
        if reset_length_after_row:
            current_length = default_length
            current_pattern = mesocycle_pattern_for_length(
                base_pattern,
                current_length,
            )
            position = 0
            length_override_event_id = ""
            reset_length_after_recovery = False

        if taper:
            previous_active_id = ""
            previous_config = None
        else:
            previous_active_id = active_id
            if config is not None:
                previous_config = config

    output = []
    for requested_start in requested:
        row = dict(grid_rows[requested_start])
        row["week_start"] = requested_start
        output.append(row)
    return pd.DataFrame(output).reset_index(drop=True)
