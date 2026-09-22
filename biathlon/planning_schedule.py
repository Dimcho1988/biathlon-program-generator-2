"""Calendar horizon and session slots, separate from dose permission."""
from copy import deepcopy
from datetime import date, timedelta

VERSION = "planning-schedule-v1"

def horizon(profile, events):
    result = deepcopy(profile)
    start = date.fromisoformat(profile["program_start"])
    requested = date.fromisoformat(profile["program_end"])
    races = [date.fromisoformat(str(e["end_date"])) for e in events
             if e["event_type"] == "MAIN_RACE" and str(e["end_date"]) >= start.isoformat()]
    automatic = profile.get("horizon_mode", "AUTO_CALENDAR") == "AUTO_CALENDAR"
    end = max(races) + timedelta(days=profile.get("transition_days", 0)) if automatic and races else requested
    end = min(end, start + timedelta(days=365))
    result["program_end"] = end.isoformat()
    return result, {"mode": "AUTO_CALENDAR" if automatic else "MANUAL", "requested_end": requested.isoformat(),
                    "effective_end": end.isoformat(), "version": VERSION,
                    "outside_main_races": [deepcopy(e) for e in events if e["event_type"] == "MAIN_RACE" and str(e["end_date"]) > end.isoformat()]}

def slots(profile, available, start, end):
    """Allocate maxima, never force sessions or multiply a load budget."""
    controls = profile.get("planning_controls") or {}
    limit = controls.get("sessions_per_week", 7)
    counts = controls.get("sessions_by_day")
    days = [start + timedelta(days=i) for i in range((end-start).days+1)]
    maxima = {d: (counts[d.weekday()] if counts is not None else 3) if available[d.weekday()] > 0 else 0 for d in days}
    allocated = {d: 0 for d in days}
    for d in days:
        if d.weekday() in controls.get("double_threshold_days", []) and maxima[d] >= 2 and limit >= 2:
            allocated[d] = 2
            limit -= 2
    preferred = set(controls.get("intensity_days", []) + controls.get("threshold_days", []) + controls.get("strength_days", []))
    if controls.get("long_session_day") is not None:
        preferred.add(controls["long_session_day"])
    for level in range(1, 4):
        ordered = sorted(days, key=lambda d: (d.weekday() not in preferred, d)) if level == 1 else days
        for d in ordered:
            if limit and maxima[d] >= level and allocated[d] < level:
                allocated[d] += 1
                limit -= 1
    return [(d, index, allocated[d]) for d in days for index in range(max(1, allocated[d]))]

def day_sessions(day):
    return day["sessions"] if "sessions" in day else [day["session"]] if day.get("session") else []

def day_totals(day):
    sessions = day_sessions(day)
    return {"title": " + ".join(s["title"] for s in sessions) or None,
            "total_minutes": sum(s["total_minutes"] for s in sessions),
            "sports": list(dict.fromkeys(s["sport"] for s in sessions)),
            "canonical_effective_load": {z: sum((s.get("canonical_effective_load") or {}).get(z, 0.) for s in sessions)
                                         for z in ("Z1", "Z2", "Z3", "Z4", "Z5", "STR")}}
