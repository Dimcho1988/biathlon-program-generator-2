"""Explicit, replayable return to training; Q is not a fitness measurement.

Missing watch records never confirm a break. Only a dated athlete/coach
declaration can supply zero-load days. Forecast weeks never become execution.
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from math import ceil
from statistics import median
import json

from .constants import COMPONENTS

VERSION = "linear-training-return-v1"
DURATION_RATIO = 1.3
PAUSE_CAP_DAYS = 60
LOWER_FRACTION = .9
HISTORY_FRACTION = .5
LONG_PAUSE_TARGET = .9


def declaration(profile):
    value = profile.get("training_return") or {}
    return value if value.get("mode") == "CONFIRMED_BREAK" else None


def confirmed_source(source, profile, today, calendar=()):
    """Temporary planning coverage, backed by an explicit dated declaration.

    Do not overwrite any recorded exercise, or insert zeros after resumption.
    The measured ledger and learning input remain untouched by the caller.
    """
    config = declaration(profile)
    if not config:
        return source
    left, resume = map(date.fromisoformat, (str(config["pause_start"]), str(config["resume_date"])))
    end = min(resume, today)
    conflict = any(left.isoformat() <= a.get("date", a.get("local_date", "")) < resume.isoformat()
                   and float(a.get("duration_min") or 0) > 0
                   for a in [*source.get("activities", []), *calendar])
    conflict = conflict or any(left.isoformat() <= r["date"] < resume.isoformat() and r.get("effective_load", 0) > 0
                              for r in [*source.get("daily", []), *source.get("strength", {}).get("daily", [])])
    result = deepcopy(source)
    result["training_return_declaration"] = {"status": "CONFLICT" if conflict else "CONFIRMED",
        "pause_start": left.isoformat(), "resume_date": resume.isoformat(), "source": "ATHLETE_OR_COACH"}
    if conflict:
        return result
    daily = result.setdefault("daily", [])
    strength = result.setdefault("strength", {}).setdefault("daily", [])
    seen = {(r["date"], r["zone"]) for r in daily}
    strength_seen = {r["date"] for r in strength}
    d = max(left, today-timedelta(days=90))
    while d < end:
        key = d.isoformat()
        for z in COMPONENTS[:5]:
            if (key,z) not in seen:
                daily.append({"date": key, "zone": z, "effective_load": 0., "source": "DECLARED_REST"})
        if key not in strength_seen:
            strength.append({"date": key, "effective_load": 0., "real_time_min": 0.,
                             "equivalent_time_min": 0., "source": "DECLARED_REST"})
        d += timedelta(days=1)
    dates = [r["date"] for r in daily]
    if dates:
        result["period_start"] = min(dates)
        result["period_end"] = max(dates)
    return result


def _window(source, start, end):
    """Complete actual Q and sport exposure; never samples planned sessions."""
    keys = {(start+timedelta(days=i)).isoformat() for i in range((end-start).days)}
    covered = [{r["date"] for r in source.get("daily", []) if r["zone"] == z} for z in COMPONENTS[:5]]
    if not keys or not keys <= set.intersection(*covered):
        return None
    activities = [a for a in source.get("activities", []) if a["date"] in keys and a.get("sport") != "WeightTraining"]
    q = {z: 0. for z in COMPONENTS[:5]}
    by_sport = {}
    for a in activities:
        if a.get("quality_status", "valid") != "valid" or a.get("planning_estimated"):
            return None
        for z in q:
            row = next((v for v in a.get("zones", []) if v["zone"] == z), {})
            value = row.get("equivalent_time_min")
            if value is None or row.get("planning_estimated_q", 0) > 0:
                return None
            q[z] += float(value)
        by_sport[a.get("sport", "Unknown")] = by_sport.get(a.get("sport", "Unknown"), 0.) + float(a.get("duration_min") or 0)
    return {"q": {z:v*7/len(keys) for z,v in q.items()},
            "by_sport": {s:v*7/len(keys) for s,v in by_sport.items()},
            "start_date": start.isoformat(), "end_date": (end-timedelta(days=1)).isoformat()}


def attach(ctx, profile, source, today, bounds, *, historical_anchor=None):
    config = declaration(profile)
    if not config:
        return
    declared = source.get("training_return_declaration") or {}
    if declared.get("status") != "CONFIRMED":
        ctx["training_return"] = {"version": VERSION, "status": "CONFLICT", "supported": False}
        return
    pause, resume = map(date.fromisoformat, (str(config["pause_start"]), str(config["resume_date"])))
    key = sha256(json.dumps({"declaration": config, "reference": ctx["anchor"]["key"],
                              "version": VERSION}, sort_keys=True).encode()).hexdigest()
    saved = (historical_anchor or {}).get("training_return") or {}
    if saved.get("key") == key:
        episode = deepcopy(saved)
    else:
        days = 7*len(profile["planning_controls"]["wave"])
        windows = [_window(source, pause-timedelta(days=days*(i+1)), pause-timedelta(days=days*i)) for i in range(3)]
        windows = [w for w in windows if w is not None and sum(w["q"].values()) > 0]
        if not windows:
            fallback = _window(source, pause-timedelta(days=40), pause)
            windows = [fallback] if fallback and sum(fallback["q"].values()) > 0 else []
        manual = config.get("historical_weekly_q") or {}
        retained = historical_anchor if historical_anchor and historical_anchor.get("created_on", "~") <= pause.isoformat() else {}
        components = {}
        gap = (resume-pause).days
        target_fraction = LONG_PAUSE_TARGET if gap > PAUSE_CAP_DAYS else 1.
        for z,(lo,hi) in bounds.items():
            h = manual.get(z)
            origin = "DECLARED_HISTORY" if h is not None else None
            if h is None and windows:
                h = median(w["q"][z] for w in windows); origin = "PRE_BREAK_COMPLETE_CYCLES"
            if h is None:
                h = (retained.get("components", {}).get(z) or {}).get("weekly_q")
                if h is not None: origin = "RETAINED_PRE_BREAK_HISTORY"
            target = h*target_fraction if h is not None else LOWER_FRACTION*lo
            initial = min(target, max(LOWER_FRACTION*lo, HISTORY_FRACTION*h)) if h is not None else target
            components[z] = {"historical_q": h, "initial_q": initial, "target_q": target,
                             "source": origin or "EXPERT_START", "completed_weeks": 0, "completed_on": None}
        by_sport = {s:median(w["by_sport"].get(s,0.) for w in windows)
                    for s in {s for w in windows for s in w["by_sport"]}}
        # A declaration refers only to the selected sport; it never transfers
        # capacity/exposure to other sports in a mixed programme.
        known_sports = list(by_sport)
        if manual or retained:
            known_sports = list(dict.fromkeys([*known_sports, profile.get("actual_sport") or profile["sport"]]))
        nominal_days = DURATION_RATIO*min(gap,PAUSE_CAP_DAYS)
        episode = {"version": VERSION, "key": key, "pause_start": pause.isoformat(), "resume_date": resume.isoformat(),
            "pause_days": gap, "nominal_days": nominal_days, "weeks": max(2,ceil(nominal_days/7)),
            "duration_ratio": DURATION_RATIO, "lower_fraction": LOWER_FRACTION,
            "history_fraction": HISTORY_FRACTION, "target_fraction": target_fraction,
            "evaluated_until": resume.isoformat(), "components": components, "observed_weeks": {},
            "known_sports": known_sports, "by_sport_weekly_minutes": by_sport,
            "observed_windows": [{k:w[k] for k in ("start_date","end_date")} for w in windows],
            "historical_scope": "CURRENT_EQUIVALENCE_AND_SPORT", "curve": "LINEAR"}
    # Retain completed observations, not forecasts. Re-read available windows
    # so a corrected activity can remove previously awarded progress. Older
    # verified windows survive the source's rolling retention horizon.
    observations = episode["observed_weeks"]
    cursor = resume
    for c in episode["components"].values():
        c.update(completed_weeks=0, completed_on=None)
    while cursor+timedelta(days=7) <= today:
        key = cursor.isoformat()
        if key >= (source.get("period_start") or key) or key not in observations:
            window = _window(source,cursor,cursor+timedelta(days=7))
            if window is None:
                observations.pop(key,None)
            else:
                observations[key] = window["q"]
        actual = observations.get(key)
        if actual is None:
            break
        for z,c in episode["components"].items():
            if c["completed_on"]:
                continue
            step = min(c["completed_weeks"],episode["weeks"]-1)
            expected = c["initial_q"] + (c["target_q"]-c["initial_q"])*step/(episode["weeks"]-1)
            # Half a Q-minute is only the ledger's numerical rounding margin.
            if actual[z]+.5 >= expected:
                c["completed_weeks"] += 1
                if c["completed_weeks"] >= episode["weeks"]:
                    c["completed_on"] = (cursor+timedelta(days=7)).isoformat()
        cursor += timedelta(days=7)
    episode["evaluated_until"] = cursor.isoformat()
    # Completed zero-load weeks may be consumed, never used as growth credit.
    status = "PAUSED" if today < resume else "COMPLETED" if all(c["completed_on"] for c in episode["components"].values()) else "RETURNING"
    ctx["anchor"] = deepcopy(ctx["anchor"])
    ctx["anchor"]["training_return"] = episode
    ctx["reference"] = ctx["anchor"]
    support = today >= resume and (source.get("period_end") or "") >= (today-timedelta(days=1)).isoformat()
    ctx["training_return"] = {**deepcopy(episode), "status": status, "supported": support,
                              "future_is_conditional": True, "missed_weeks_do_not_accrue": True}
    for z,c in episode["components"].items():
        row = ctx["components"][z]
        row.update(reference_q=c["target_q"], return_initial_q=c["initial_q"],
                   return_historical_q=c["historical_q"], return_source=c["source"])


def point(ctx, zone, day):
    episode = (ctx or {}).get("training_return") or {}
    c = episode.get("components", {}).get(zone)
    if c is None or episode.get("status") == "CONFLICT":
        return None
    resume = date.fromisoformat(episode["resume_date"])
    as_of = date.fromisoformat(ctx["as_of"])
    current_week = as_of-timedelta(days=max(0,(as_of-resume).days)%7)
    checkpoint = max(resume,date.fromisoformat(episode["evaluated_until"]),current_week)
    # Forecast continuation begins at the current uncompleted observation week.
    forecast_weeks = max(0,(day-checkpoint).days//7)
    step = min(episode["weeks"]-1,c["completed_weeks"]+forecast_weeks)
    q = c["initial_q"] + (c["target_q"]-c["initial_q"])*step/(episode["weeks"]-1)
    completion = (date.fromisoformat(c["completed_on"]) if c["completed_on"] else
                  checkpoint+timedelta(days=7*(episode["weeks"]-c["completed_weeks"])))
    return {"weekly_q": 0. if day < resume else q, "returning": day < completion,
            "development_start": completion.isoformat(), "week": step+1,
            "weeks": episode["weeks"], "target_fraction": episode["target_fraction"],
            "conditional": day >= as_of and c["completed_on"] is None,
            "source": c["source"], "historical_q": c["historical_q"]}


def supported(ctx):
    return bool((ctx or {}).get("training_return", {}).get("supported"))
