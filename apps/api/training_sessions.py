"""Count recorded training occasions without merging their scientific loads.

The two-hour end-to-start gap is an explicit coaching convention, not a
physiological recovery threshold. Unknown timing retains separate records.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo

VERSION = "recorded-training-sessions-v1-gap-120m"
MAX_GAP_MINUTES = 120


def _positive(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value) and value > 0:
            return float(value)
    return None


def _start(value, tz):
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # A repeated local clock time cannot establish the real gap.
            if parsed.replace(tzinfo=tz, fold=0).utcoffset() != parsed.replace(tzinfo=tz, fold=1).utcoffset():
                return None
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


@dataclass
class RecordedSession:
    date: str
    activities: tuple[dict, ...]
    start: datetime | None = None
    end: datetime | None = None

    @property
    def is_key(self):
        # Short interval files together may exceed the established five-minute
        # zone threshold, even when no individual file reaches it.
        totals = {z: 0. for z in ("Z3", "Z4", "Z5")}
        for activity in self.activities:
            for zone in activity.get("zones", []):
                if zone.get("zone") in totals:
                    totals[zone["zone"]] += _positive(zone.get("raw_time_min")) or 0.
        return any(v >= 5 for v in totals.values())


def group_recordings(activities, calendar, timezone_name):
    """Return counting groups, keeping every input activity and load untouched.

    Only unique, matched metadata on the same source calendar day may join.
    Different sports may form one combined occasion. A >120-minute gap starts
    another group; there is no maximum duration for a continuous occasion.
    """
    tz = ZoneInfo(timezone_name)
    catalog = {}
    for row in calendar:
        ref = row.get("activity_ref")
        if ref:
            catalog.setdefault(ref, []).append(row)
    ref_counts = {}
    for activity in activities:
        ref = activity.get("activity_ref")
        if ref:
            ref_counts[ref] = ref_counts.get(ref, 0) + 1
    timed, unknown = [], []
    for activity in activities:
        day, ref = activity.get("date"), activity.get("activity_ref")
        matches = catalog.get(ref, [])
        start = end = None
        if ref_counts.get(ref) == 1 and len(matches) == 1:
            row = matches[0]
            if row.get("local_date", day) == day:
                start = _start(row.get("start_at_utc"), timezone.utc)
                if start is None:
                    start = _start(row.get("start_local"), tz)
                seconds = (_positive(row.get("elapsed_time_s"))
                           or _positive(row.get("recording_time_s"))
                           or (_positive(activity.get("duration_min")) or 0.) * 60)
                if start is not None and seconds:
                    try:
                        end = start + timedelta(seconds=seconds)
                    except OverflowError:
                        start = None
        group = RecordedSession(day, (activity,), start, end)
        (timed if start is not None and end is not None else unknown).append(group)
    timed.sort(key=lambda g: (g.date, g.start, g.activities[0]["activity_ref"]))
    result = []
    for group in timed:
        previous = result[-1] if result else None
        if (previous and previous.date == group.date
                and group.start - previous.end <= timedelta(minutes=MAX_GAP_MINUTES)):
            previous.activities += group.activities
            previous.end = max(previous.end, group.end)
        else:
            result.append(group)
    return sorted(result + unknown, key=lambda g: (g.date, g.start.isoformat() if g.start else ""))
