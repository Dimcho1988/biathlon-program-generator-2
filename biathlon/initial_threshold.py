"""Unvalidated initial threshold estimate from observed training history.

This is a fallback estimate, never a test, paired index, or calibration target.
Its speed frame is raw distance / moving time; it must not be compared to Vflat
without an explicit conversion policy. No V6, V50 or zone edges are inferred.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
import math


VERSION = "heuristic_v0.1_unvalidated"
MINIMUM_DAYS = 7
PREFERRED_DAYS = 28


def _number(value, *, zero_allowed=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and (value >= 0 if zero_allowed else value > 0)
    except OverflowError:
        return False


def _result(reason=None, **values):
    return {"model_version": VERSION, "status": "UNAVAILABLE" if reason else "AVAILABLE",
            "reason": reason, "source": "INITIAL_HEURISTIC_ESTIMATE", "is_estimated": True,
            "calibration_eligible": False, "speed_role": "RAW_MOVING_SPEED",
            "label": "Начална моделна оценка", "p_percent": None,
            "v_threshold_estimated_kmh": None, **values}


def estimate(H, v, S):
    """Apply the approved formula to already verified aggregate inputs.

    H is total weekly training hours (all sports, including strength); v is
    raw mean moving speed for one sport in km/h; S is known training years.
    Use ``from_history`` when calendar coverage has not yet been verified.
    """
    for value, name, zero_allowed in ((H, "WEEKLY_HOURS", False),
                                       (v, "MEAN_SPEED", False), (S, "TRAINING_YEARS", True)):
        if value is None:
            return _result(f"{name}_REQUIRED")
        if not _number(value, zero_allowed=zero_allowed):
            return _result(f"INVALID_{name}")
    try:
        p = 90 * (1 + H)**(-.04) * (v / 10)**.05 * (1 + S)**(-.02)
        threshold = v / (p / 100)
    except ArithmeticError:
        return _result("NUMERIC_RANGE_ERROR")
    if not _number(p) or not _number(threshold):
        return _result("NUMERIC_RANGE_ERROR")
    return _result(p_percent=p, v_threshold_estimated_kmh=threshold,
                   inputs={"H": float(H), "v": float(v), "S": float(S)})


def _date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def from_history(activities: Iterable[Mapping], *, sport: str, training_years,
                 window_start, window_end, as_of_date, reliable_dates: Iterable):
    """Aggregate normalized completed records in [window_start, window_end).

    Dates are local calendar dates (date objects or ISO strings). ``as_of_date``
    is the athlete's current local date: that day and future days cannot enter
    the window. ``reliable_dates`` explicitly confirms complete imported
    history, including observed rest days; absence of activities is not proof
    of coverage. Prefer a 28-day window; 21-day and any other >=7-day window
    use the same formula and report their exact denominator.

    Each included row requires id, sport, local_date, completed=True and
    training_duration_s. Planned rows and completed=False rows are excluded.
    Rows of the selected sport additionally require distance_m and
    moving_time_s, from matching records. No elapsed-time fallback is allowed.
    The adapter must supply active recovery as movement and remove passive
    pauses from moving_time_s. Session totals for H retain training duration.
    """
    start, end, today = map(_date, (window_start, window_end, as_of_date))
    if not all((start, end, today)) or start >= end:
        return _result("INVALID_WINDOW")
    if end > today:
        return _result("INCOMPLETE_CALENDAR_DAYS")
    days = (end-start).days
    window = {"start": start.isoformat(), "end_exclusive": end.isoformat(), "days": days}
    if days < MINIMUM_DAYS:
        return _result("INSUFFICIENT_HISTORY", window=window)
    if not isinstance(sport, str) or not sport.strip():
        return _result("SPORT_REQUIRED", window=window)
    if training_years is None:
        return _result("TRAINING_YEARS_REQUIRED", window=window)
    if not _number(training_years, zero_allowed=True):
        return _result("INVALID_TRAINING_YEARS", window=window)
    if reliable_dates is None:
        return _result("INCOMPLETE_HISTORY_COVERAGE", window=window)
    covered = {_date(d) for d in reliable_dates}
    if any(start+timedelta(days=i) not in covered for i in range(days)):
        return _result("INCOMPLETE_HISTORY_COVERAGE", window=window)

    unique = {}
    duplicates = 0
    for row in activities:
        if not isinstance(row, Mapping):
            return _result("INVALID_ACTIVITY", window=window)
        if row.get("planned") is True or row.get("completed") is False:
            continue
        day = _date(row.get("local_date"))
        if day is None:
            return _result("ACTIVITY_DATE_REQUIRED", window=window)
        if not start <= day < end:
            continue
        if row.get("completed") is not True:
            return _result("ACTIVITY_COMPLETION_REQUIRED", window=window)
        identity = row.get("id")
        if (not isinstance(identity, (str, int)) or isinstance(identity, bool)
                or not str(identity).strip()):
            return _result("ACTIVITY_ID_REQUIRED", window=window)
        # IDs must be canonicalized/namespaced by the adapter across providers.
        identity = str(identity)
        row_sport = row.get("sport")
        if not isinstance(row_sport, str) or not row_sport.strip():
            return _result("ACTIVITY_SPORT_REQUIRED", window=window)
        duration = row.get("training_duration_s")
        if not _number(duration):
            return _result("ACTIVITY_DURATION_REQUIRED", window=window)
        moving, distance = None, None
        if row_sport == sport:
            moving, distance = row.get("moving_time_s"), row.get("distance_m")
            if not _number(moving):
                return _result("MOVING_TIME_REQUIRED", window=window)
            if not _number(distance):
                return _result("DISTANCE_REQUIRED", window=window)
            if moving > duration:
                return _result("MOVING_TIME_EXCEEDS_DURATION", window=window)
        signature = (day, row_sport, duration, moving, distance)
        if identity in unique:
            if unique[identity] != signature:
                return _result("CONFLICTING_DUPLICATE_ACTIVITY", window=window)
            duplicates += 1
        unique[identity] = signature

    selected = [row for row in unique.values() if row[1] == sport]
    if not selected:
        return _result("SPORT_HISTORY_REQUIRED", window=window)
    try:
        total_s = math.fsum(row[2] for row in unique.values())
        moving_s = math.fsum(row[3] for row in selected)
        distance_m = math.fsum(row[4] for row in selected)
        result = estimate(total_s / 3600 / days * 7, distance_m / moving_s * 3.6, training_years)
    except ArithmeticError:
        return _result("NUMERIC_RANGE_ERROR", window=window)
    return {**result, "sport": sport, "window": window,
            "history": {"activity_count": len(unique), "sport_activity_count": len(selected),
                        "duplicates_removed": duplicates, "total_training_duration_s": total_s,
                        "sport_moving_time_s": moving_s, "sport_distance_m": distance_m}}
