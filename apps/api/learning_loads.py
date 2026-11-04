"""Recorded Q for learning: partial observations carry coverage, never rest.

This adapter is deliberately separate from the planner's complete-week policy.
Trailing means use calendar days, no future samples and no missing-as-zero fill.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta

from biathlon.constants import COMPONENTS
from biathlon import load_evidence
from biathlon.learning_provenance import initial_heuristic_load
from .response_monitoring import number


def valid(value):
    value = number(value)
    return value if value is not None and value >= 0 else None


def ledger(source, rows):
    counts = Counter((r.get("date"), r.get("zone")) for r in rows)
    effective = {(r.get("date"), r.get("zone")): valid(r.get("effective_load")) for r in rows
                 if counts[(r.get("date"), r.get("zone"))] == 1}
    activities = defaultdict(list)
    for a in source.get("activities", []):
        if day := a.get("date") or a.get("local_date"):
            activities[day].append(a)
    strength = {r["date"]: r for r in (source.get("strength") or {}).get("daily", [])}
    excluded = {e.get("date") or e.get("local_date") for e in (source.get("quality") or {}).get("exclusions", []) if isinstance(e, dict)}
    quality = source.get("quality") or {}
    counted = sum(int(quality.get(k) or 0) for k in ("excluded_activities", "limited_activities"))
    identified = sum(a.get("quality_status", "valid") != "valid" or not load_evidence.assess(a, unified=bool(source.get("load_selection")))["complete"] for records in activities.values() for a in records)
    unidentified = max(0, counted-identified)
    result = {}
    for day in sorted({d for d, _ in counts if d}):
        recorded = activities[day]
        refs = [a["activity_ref"] for a in recorded if a.get("activity_ref")]
        duplicate = len(refs) != len(set(refs))
        partial = bool(unidentified) or day in excluded or any(a.get("quality_status", "valid") != "valid" or a.get("planning_estimated") or not load_evidence.assess(a, unified=bool(source.get("load_selection")))["complete"] for a in recorded)
        modelled = any(initial_heuristic_load(a) for a in recorded)
        q, minutes, e = {}, {}, {}
        for z in COMPONENTS:
            e[z] = effective.get((day, z))
            if e[z] is None or duplicate or (not recorded and (unidentified or day in excluded)):
                q[z] = minutes[z] = None
            elif z == "STR":
                s = strength.get(day, {})
                q[z], minutes[z] = valid(s.get("equivalent_time_min")), valid(s.get("real_time_min"))
            else:
                aerobic = [a for a in recorded if not load_evidence.is_strength(a)]
                values = [next((r for r in a.get("zones", []) if r.get("zone") == z), {}) for a in aerobic]
                qs = [valid(r.get("equivalent_time_min")) for r in values]
                ts = [valid(r.get("raw_time_min")) for r in values]
                # A positive ledger with no source activity cannot prove Q.
                unknown = not aerobic and (e[z] > 0 or day in excluded)
                q[z] = sum(qs) if not unknown and all(v is not None for v in qs) else None
                minutes[z] = sum(ts) if not unknown and all(v is not None for v in ts) else None
        status = "PARTIAL" if partial or any(v is None for v in q.values()) else "MODELLED" if modelled else "COMPLETE" if recorded else "REST"
        # Initial mappings and partial intensity remain usable observations,
        # explicitly lower precision rather than whole-day exclusions.
        precision = .5 if status == "PARTIAL" else .75 if modelled else 1.
        result[day] = {"q": q, "minutes": minutes, "effective": e, "status": status, "precision": precision}
    return result


def window(daily, start, end, *, smooth=False):
    keys = [(start+timedelta(days=i)).isoformat() for i in range(max(0, (end-start).days))]
    if smooth:
        transformed = {}
        for d in keys:
            day = date.fromisoformat(d)
            neighbours = [daily[k] for i in range(3) if (k := (day-timedelta(days=i)).isoformat()) in daily]
            entry = {"q": {}, "minutes": {}, "effective": {}, "precision": sum(r["precision"] for r in neighbours)/3}
            for z in COMPONENTS:
                known = [r for r in neighbours if r["q"][z] is not None and r["minutes"][z] is not None]
                for field in ("q", "minutes", "effective"):
                    entry[field][z] = sum(r[field][z] for r in known)/len(known) if known else None
            transformed[d] = entry
        daily = transformed
    components = {}
    for z in COMPONENTS:
        paired = [daily[d] for d in keys if d in daily and daily[d]["q"][z] is not None and daily[d]["minutes"][z] is not None]
        n = len(paired)
        components[z] = {"weekly_q": sum(r["q"][z] for r in paired)*7/n if n else None,
                         "weekly_minutes": sum(r["minutes"][z] for r in paired)*7/n if n else None,
                         "weekly_effective": sum(r["effective"][z] for r in paired)*7/n if n else None,
                         "observed_days": n, "expected_days": len(keys),
                         "precision": sum(r["precision"] for r in paired)/len(keys) if keys else 0.}
    return {"components": components, "complete": bool(keys) and all(c["observed_days"] == len(keys) for c in components.values())}


def rolling(daily, day, component=None):
    keys = [(day-timedelta(days=i)).isoformat() for i in range(3)]
    values = []
    for key in keys:
        qs = (daily.get(key) or {}).get("q", {})
        v = qs.get(component) if component else sum(qs.values()) if len(qs) == len(COMPONENTS) and all(v is not None for v in qs.values()) else None
        if v is not None:
            values.append(v)
    return (sum(values)/len(values) if values else None), len(values)
