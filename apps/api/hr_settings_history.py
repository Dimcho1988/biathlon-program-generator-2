"""Date-effective HR calibration. Recorded sensor measurements are immutable."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_DAYS = 90
MAX_DAYS = 180


def effective_date(body, settings, *, today=None):
    today = today or datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
    mode = body.hrmax_apply_mode
    selected = body.hrmax_effective_from
    if mode == "FROM_DATE":
        if selected is None:
            raise ValueError("Изберете начална дата за новия максимален пулс.")
    else:
        if selected is not None:
            raise ValueError("Начална дата се задава само при избор „От избрана дата“.")
        selected = today-timedelta(days=DEFAULT_DAYS-1)
    if not today-timedelta(days=MAX_DAYS-1) <= selected <= today:
        raise ValueError("Началната дата трябва да е в последните 180 дни, включително днес.")
    return selected.isoformat()


def at(settings, day):
    """Resolve a full HR calibration; never interpolate HRmax or zone boundaries."""
    history = getattr(settings, "hr_history", ())
    if not history:
        return settings
    key = day.isoformat() if isinstance(day, date) else str(day)[:10]
    selected = next((row for row in reversed(history) if row["from"] <= key), None)
    if selected is None:
        raise ValueError("HR calibration history has no baseline")
    return replace(settings, hrmax_bpm=selected["hrmax_bpm"],
        zone_bounds_bpm=tuple(selected["zone_bounds_bpm"]),
        hr_zone_source=selected["hr_zone_source"],
        hr_zone_percentages=tuple(selected["hr_zone_percentages"]) if selected.get("hr_zone_percentages") else None,
        hr_history=(), hrmax_apply_from=None).validate()


def validate(settings):
    previous = None
    for row in settings.hr_history:
        day = date.fromisoformat(row["from"])
        if previous is None and day != date.min or previous is not None and day <= previous:
            raise ValueError("Invalid HR calibration timeline")
        previous = day
        at(replace(settings, hr_history=(dict(row, **{"from": "0001-01-01"}),)), day)


def history_days(settings, end, default):
    """Keep a requested older correction inside the normal bounded import lane."""
    start = getattr(settings, "hr_recompute_from", None)
    return max(default, min(MAX_DAYS+39, (end-date.fromisoformat(start)).days+40)) if start else default
