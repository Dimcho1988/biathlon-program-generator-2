"""Causal, disjoint observations for the individualized planning controller.

Fourteen-day calendar episodes describe associations, not controlled trials.
The first seven days are exposure and the next seven are response/outcome.
Direct Q identifies component dose; canonical E remains separate context.
The cutoffs/coverage floors below are explicit conservative pilot parameters.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from math import log
from statistics import median

from biathlon.constants import COMPONENTS
from biathlon.load_progression import observed_window
from biathlon.individual_learning import dose_changes
from .response_monitoring import latest_entries, number, relative_score
from .stress_model import CHANNELS
from .trainability import MODEL_VERSION, SCHEMA_VERSION
from .body_observations import normalize_result

VERSION = "learning-evidence-v2"
ANCHOR = date(2020, 1, 6)
MAX_EPISODES = 78
MIN_RESPONSE_COVERAGE = .20
FUNCTIONAL = {k for k, (g, _) in CHANNELS.items() if g == "functional"}
ALTERED_EXECUTION = {"TIME", "CONDITIONS", "COACH", "OTHER"}


def _dates(start, end):
    return [(start + timedelta(days=i)).isoformat() for i in range((end-start).days+1)]


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _record_day(entry):
    value = entry.get("recorded_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return "9999-12-31"  # Invalid provenance is never treated as earlier.


def _available_entries(entries, today):
    key = today.isoformat()
    return latest_entries([e for e in entries if (_record_day(e) or "") <= key
                           and str(e.get("observed_date") or e.get("payload", {}).get("day") or "") <= key])


def _response_days(history, selected, entries, today):
    """Do not consume a score built from an unavailable future revision.

    The normal service supplies history from the same available entry set.
    This defensive path also handles retrospective as-of evaluation: a future
    manual revision invalidates derived channels it could have influenced.
    """
    result = {}
    future = [e for e in entries if (_record_day(e) or "") > today.isoformat()]
    future_dates = [(e["kind"], str(e.get("observed_date") or e.get("payload", {}).get("day") or e.get("entry_key", ""))) for e in future]
    for item in history.get("days", []):
        day = item["day"]
        if day > today.isoformat():
            continue
        row = deepcopy(item)
        manual = selected.get(("DAILY", day))
        row["daily_report"] = manual["payload"] if manual else None
        future_block = any(kind == "BLOCK" and observed <= day for kind, observed in future_dates)
        if any(kind == "LAB" and observed == day for kind, observed in future_dates):
            actual_labs = [e["payload"] for (kind, _), e in selected.items() if kind == "LAB" and e["payload"]["day"] == day]
            flagged = any(normalize_result(r)["reference_status"] in {"LOW", "HIGH"} for report in actual_labs for r in report.get("results", []))
            row.setdefault("body_observations", {})["context_status"] = "REVIEW_LAB_REFERENCE" if flagged else "NO_OBSERVED_LAB_REFERENCE"
        for channel in row.get("channels", []):
            key = channel["key"]
            if key in {"fatigue", "sleep_quality", "stress", "soreness", "motivation", "competition_motivation"}:
                value = number((row["daily_report"] or {}).get(key))
                channel["score"] = (value-1)*25 if value is not None else None
            affected = future_block or any(observed <= day and (
                (kind == "DAILY" and key == "sleep_duration") or
                (kind == "WEIGHT" and key in {"morning_weight", "session_weight"}) or
                (kind == "LAB" and channel.get("group") == "biochemistry") or
                (kind == "SESSION" and key in FUNCTIONAL) or
                (kind == "TEST" and key == "control_test")) for kind, observed in future_dates)
            if affected:
                channel["score"] = None
        result[day] = row
    return result


def _scores(row, references=None):
    result = {}
    for channel in row.get("channels", []):
        key, value = channel["key"], number(channel.get("score"))
        if key in FUNCTIONAL or value is None:
            continue
        reference = (references or {}).get(key)
        raw = number(channel.get("raw"))
        if reference and raw is not None and (key != "hrv" or raw > 0):
            value = relative_score(log(raw) if key == "hrv" else raw, reference, 1 if key == "resting_hr" else -1)
            if key == "hrv":
                value = 50+abs(value-50)
        result[key] = value
    return result


def _response(days, start, load_end, end):
    baseline_keys = _dates(start-timedelta(days=7), start-timedelta(days=1))
    follow_keys = _dates(load_end+timedelta(days=1), end)
    references = {}
    for day in baseline_keys:
        for channel in days.get(day, {}).get("channels", []):
            reference = channel.get("baseline") or {}
            if (channel["key"] in {"resting_hr", "hrv", "sleep_duration"}
                    and number(reference.get("median")) is not None and number(reference.get("spread")) is not None and reference["spread"] > 0):
                references[channel["key"]] = {k: reference[k] for k in ("median", "spread")}
    baseline = {d: _scores(days.get(d, {}), references) for d in baseline_keys}
    follow = {d: _scores(days.get(d, {}), references) for d in follow_keys}
    channels = sorted(k for k in CHANNELS if k not in FUNCTIONAL
                      and sum(k in r for r in baseline.values()) >= 3
                      and sum(k in r for r in follow.values()) >= 4)
    weight = sum(CHANNELS[k][1] for k in channels)/100
    if weight < MIN_RESPONSE_COVERAGE-1e-9:
        return None
    def score(row):
        return sum(row[k]*CHANNELS[k][1]/100 for k in channels)/weight if all(k in row for k in channels) else None
    before = [s for row in baseline.values() if (s := score(row)) is not None]
    after = {d: score(row) for d, row in follow.items()}
    if len(before) < 3 or sum(v is not None for v in after.values()) < 4:
        return None
    center = median(before)
    returned, consecutive = None, 0
    for day in follow_keys:
        value = after[day]
        if value is None:
            consecutive = 0
        elif value > center+5:
            returned, consecutive = None, 0
        else:
            consecutive += 1
            if consecutive == 2:
                returned = day
    burden = [score(_scores(days.get(d, {}), references)) for d in _dates(start, end)]
    burden = [v for v in burden if v is not None]
    return {"burden_delta": round(sum(burden)/len(burden)-center, 6),
            "baseline_score": round(center, 6), "recovery_days": (date.fromisoformat(returned)-load_end).days if returned else None,
            "recovered": returned is not None, "returned_on": returned,
            "coverage": round(weight, 6), "channels": channels, "references": references,
            "baseline_days": len(before), "followup_days": sum(v is not None for v in after.values()),
            "observed_days": len(burden), "tracked_days": 14}


def _ti_outcomes(trainability, start, returned, end):
    if returned is None:
        return []
    baseline_start = (start-timedelta(days=14)).isoformat()
    grouped = defaultdict(lambda: {"before": defaultdict(list), "after": defaultdict(list)})
    for row in trainability:
        index = row.get("index") or {}
        if (index.get("model_version") != MODEL_VERSION or index.get("schema_version") != SCHEMA_VERSION
                or index.get("admission", {}).get("status") != "ACCEPTED" or not index.get("comparison_key")):
            continue
        day = row.get("local_date", "")
        side = "before" if baseline_start <= day < start.isoformat() else "after" if returned < day <= end.isoformat() else None
        if side is None:
            continue
        for band in [index.get("general") or {}, *index.get("zones", [])]:
            value = number(band.get("index"))
            name = band.get("name")
            if not band.get("valid") or not value or value <= 0 or name not in ("GENERAL", *COMPONENTS) or name == "STR":
                continue
            key = (row.get("sport") or "Unknown", index["comparison_key"], "GLOBAL" if name == "GENERAL" else name)
            grouped[key][side][day].append(value)
    result = []
    for (sport, comparison, scope), group in sorted(grouped.items()):
        if min(len(group["before"]), len(group["after"])) < 2:
            continue
        before = median(median(v) for v in group["before"].values())
        after = median(median(v) for v in group["after"].values())
        result.append({"scope": scope, "change_percent": round(100*(before-after)/before, 6),
                       "source": "TI", "weight": .65, "comparison_key": f"{sport}:{comparison}:{scope}",
                       "before_days": len(group["before"]), "after_days": len(group["after"]),
                       "before_value": before, "after_value": after, "outcome_day": max(group["after"]),
                       "meaningful_change_percent": 1.})
    return result


def _test_outcomes(selected, start, returned, end):
    if returned is None:
        return []
    signature = ("protocol", "protocol_version", "unit", "direction", "conditions")
    tests = [e["payload"] for (kind, _), e in selected.items() if kind == "TEST"
             and e["payload"].get("comparable") and number(e["payload"].get("value")) is not None and e["payload"]["value"] > 0]
    grouped = defaultdict(lambda: {"before": defaultdict(list), "after": defaultdict(list)})
    for test in tests:
        side = "before" if (start-timedelta(days=28)).isoformat() <= test["day"] < start.isoformat() else "after" if returned < test["day"] <= end.isoformat() else None
        if side is None or any(not test.get(k) for k in signature):
            continue
        # Multiple components define a group outcome, not independent zone wins.
        components = test.get("components") or []
        scope = components[0] if len(components) == 1 else "GLOBAL"
        key = (*[test[k] for k in signature], scope, tuple(sorted(components)))
        grouped[key][side][test["day"]].append(test)
    outcomes = []
    for signature_key, group in sorted(grouped.items()):
        if not group["before"] or not group["after"]:
            continue
        before = median(median(t["value"] for t in v) for v in group["before"].values())
        after = median(median(t["value"] for t in v) for v in group["after"].values())
        threshold = max(t.get("meaningful_change_percent", 1.) for side in group.values() for values in side.values() for t in values)
        outcomes.append({"scope": signature_key[-2], "change_percent": round(100*(after/before-1)*(1 if signature_key[3] == "HIGHER" else -1), 6),
                         "source": "TEST", "weight": 1., "comparison_key": _hash(signature_key),
                         "before_days": len(group["before"]), "after_days": len(group["after"]),
                         "before_value": before, "after_value": after, "outcome_day": max(group["after"]),
                         "meaningful_change_percent": threshold})
    return outcomes


def _confounds(days, selected, history, start, end, periodization, events):
    first, last = start.isoformat(), end.isoformat()
    reasons = set()
    for day, row in days.items():
        if first <= day <= last:
            if (row.get("daily_report") or {}).get("pain_or_illness"):
                reasons.add("ILLNESS_OR_PAIN")
            if row.get("body_observations", {}).get("context_status") == "REVIEW_LAB_REFERENCE":
                reasons.add("LAB_REFERENCE_REVIEW")
    for session in history.get("sessions", []):
        if first <= session.get("day", "") <= last:
            report = selected.get(("SESSION", session.get("activity_ref")))
            execution = report["payload"] if report else {}
            if execution.get("execution_reason") in ALTERED_EXECUTION:
                reasons.add("ALTERED_EXECUTION_CONTEXT")
    for window in (periodization or {}).get("taper_windows", []):
        if window["start_date"] <= last and window["end_date"] >= first:
            reasons.add("TAPER_CONTEXT")
    for phase in (periodization or {}).get("phases", []):
        if phase.get("kind") in {"RE_ENTRY", "TRANSITION", "COMPETITION"} and phase["start_date"] <= last and phase["end_date"] >= first:
            reasons.add("NON_BUILDING_PHASE")
    for event in events:
        if event.get("event_type") in {"MAIN_RACE", "CONTROL_RACE", "CAMP", "UNAVAILABLE"} and str(event.get("start_date", "9999")) <= last and str(event.get("end_date") or event.get("start_date", "")) >= first:
            reasons.add("CHANGED_EVENT_CONTEXT")
    return sorted(reasons)


def _dose(source, rows, start, load_end, end):
    windows = [observed_window(source, rows, a, b) for a, b in (
        (start-timedelta(days=7), start), (start, load_end+timedelta(days=1)), (load_end+timedelta(days=1), end+timedelta(days=1)))]
    if not all(w["complete"] for w in windows):
        return None, "INCOMPLETE_ACTUAL_LOAD"
    if any(number(c[k]) is None or c[k] < 0 for w in windows for c in w["components"].values()
           for k in ("weekly_q", "weekly_minutes", "weekly_effective")):
        return None, "UNKNOWN_DIRECT_DOSE"
    totals = [sum(w["components"][z]["weekly_q"] for z in COMPONENTS) for w in windows]
    # A near-zero individual zone is common in real training. Only a wholesale
    # discontinuity is outside this local model; all zone changes remain in x.
    if min(totals[:2]) <= 0 or not .25 <= totals[1]/totals[0] <= 4:
        return None, "EXTREME_TOTAL_DOSE_CHANGE"
    dose, changes, intensity = {}, {}, {}
    for z in COMPONENTS:
        before, after, follow = [w["components"][z] for w in windows]
        if any(number(c[k]) is None or c[k] < 0 for c in (before, after, follow) for k in ("weekly_q", "weekly_minutes", "weekly_effective")):
            return None, "UNKNOWN_DIRECT_DOSE"
        bq, aq = before["weekly_q"], after["weekly_q"]
        bt, at = before["weekly_minutes"], after["weekly_minutes"]
        if any((c["weekly_q"] == 0) != (c["weekly_minutes"] == 0) for c in (before, after, follow)):
            return None, "INCONSISTENT_Q_TIME"
        changes[z], effort = dose_changes(bq, aq, bt, at)
        if z != "STR":
            intensity[z] = effort
        dose[z] = {"baseline_q": bq, "actual_q": aq, "baseline_minutes": bt, "actual_minutes": at,
                   "baseline_e": before["weekly_effective"], "actual_e": after["weekly_effective"],
                   "followup_q": follow["weekly_q"], "followup_minutes": follow["weekly_minutes"], "followup_e": follow["weekly_effective"]}
    return {"dose": dose, "dose_change": changes, "intensity_change": intensity}, None


def _lab_review(selected, today):
    """Retain an unresolved reference finding, never a carried stress score.

    A newer exact comparable result for the same analyte/specimen may resolve
    its own finding. Missing limits, censored values and different collection
    conditions cannot silently supply evidence of normalization.
    """
    pending = {}
    reports = sorted([e for (kind, _), e in selected.items() if kind == "LAB" and e["payload"]["day"] <= today.isoformat()],
                     key=lambda e: (e["payload"]["day"], e["payload"].get("collection_time") or "", e["entry_key"]))
    for entry in reports:
        report = entry["payload"]
        stamp = (report["day"], report.get("collection_time") or "")
        for raw in report.get("results", []):
            result = normalize_result(raw)
            analyte, status = result["analyte"], result["reference_status"]
            signature = (str(report.get("laboratory", "")).casefold(), str(report.get("protocol", "")).casefold(),
                         report.get("fasting"), (report.get("collection_time") or "")[:2], result["sample"], result["normalized_unit"])
            if status in {"LOW", "HIGH"}:
                pending[analyte] = {"analyte": analyte, "observed_on": report["day"], "status": status,
                                    "age_days": (today-date.fromisoformat(report["day"])).days,
                                    "signature": signature, "stamp": stamp}
                continue
            previous = pending.get(analyte)
            if (previous and result.get("qualifier", "EQ") == "EQ" and status == "WITHIN_PROVIDED_LIMITS"
                    and report.get("comparable") and report.get("collection_time")
                    and signature == previous["signature"] and stamp > previous["stamp"]
                    and raw.get("reference_high" if previous["status"] == "HIGH" else "reference_low") is not None):
                del pending[analyte]
    return [{k: value[k] for k in ("analyte", "observed_on", "status", "age_days")}
            for _, value in sorted(pending.items())]


def _current(days, selected, source, rows, history, today, usable):
    observed = [(d, r) for d, r in sorted(days.items()) if any(number(c.get("score")) is not None for c in r.get("channels", []))]
    latest, row = observed[-1] if observed else (None, {})
    channels = [c for c in row.get("channels", []) if number(c.get("score")) is not None]
    weight = sum(CHANNELS[c["key"]][1] for c in channels if c["key"] in CHANNELS)
    reports = [(d, e["payload"]) for (kind, d), e in selected.items() if kind == "DAILY" and d <= today.isoformat()]
    recent_report = max(reports, default=(None, {}), key=lambda v: v[0])
    recent = (today-timedelta(days=2)).isoformat()
    findings = _lab_review(selected, today)
    # Adapters without raw laboratory records cannot prove an old reference
    # finding was resolved. Keep a dated signal until that context is available.
    unresolved_context = not any(kind == "LAB" for kind, _ in selected) and any(
        r.get("body_observations", {}).get("context_status") == "REVIEW_LAB_REFERENCE" for r in days.values())
    review = bool(findings) or unresolved_context
    execution = any(s.get("day", "") >= recent and (selected.get(("SESSION", s.get("activity_ref")), {}).get("payload") or {}).get("execution_reason") in ALTERED_EXECUTION for s in history.get("sessions", []))
    last_completed = (today-timedelta(days=1)).isoformat()
    current_coverage = {r["zone"] for r in rows if r["date"] == last_completed}
    families = sorted({CHANNELS[c["key"]][0] for c in channels})
    return {"stress_score": round(sum(c["score"]*CHANNELS[c["key"]][1] for c in channels)/weight, 6) if weight else None,
            "coverage": weight, "families": len(families), "family_keys": families, "latest_day": latest,
            "illness_hold": bool(recent_report[1].get("pain_or_illness")), "lab_review": review, "lab_review_findings": findings,
            "execution_caution": execution, "recovered": None, "history_usable": usable and current_coverage == set(COMPONENTS),
            "latest_outcome_day": None, "observation_age_days": (today-date.fromisoformat(latest)).days if latest else None}


def build_evidence(*, source, rows, entries, history, trainability, today, retained=None,
                   context_key="", periodization=None, events=()):
    """Build replayable evidence; never mutate an observation or persist a plan.

    All dates are athlete-local. ``today`` is an as-of date; incomplete current
    days are excluded from training. Caller must provide one pinned generation.
    Newly discovered historical outcomes become available now, not in the past.
    """
    selected = _available_entries(entries, today)
    days = _response_days(history, selected, entries, today)
    rows = [r for r in rows if r["date"] < today.isoformat()]
    quality = source.get("quality") or {}
    usable = not (quality.get("limited_activities") or quality.get("excluded_activities")) and bool(rows)
    valid_rows = all(number(r.get("effective_load")) is not None and r["effective_load"] >= 0 for r in rows)
    unique = len({(r["date"], r["zone"]) for r in rows}) == len(rows)
    usable = usable and valid_rows and unique
    retained_list = (retained or {}).get("episodes", []) if isinstance(retained, dict) else retained or []
    previous = {e["id"]: deepcopy(e) for e in retained_list if e.get("context_key") == context_key
                and e.get("end", "9999") < today.isoformat() and e.get("observed_on", "9999") <= today.isoformat()
                and e.get("provenance", {}).get("evidence_version") == VERSION}
    episodes, exclusions = {}, []
    first = str(source.get("period_start") or min((r["date"] for r in rows), default=today.isoformat()))
    # Preserve only genuinely aged-out observations. Recent episodes are rebuilt
    # or removed, so missing/revised data cannot keep an obsolete positive win.
    for key, episode in previous.items():
        if (date.fromisoformat(episode["start"])-timedelta(days=7)).isoformat() < first:
            episodes[key] = episode
    if not usable:
        exclusions.append({"id": None, "reason": "UNUSABLE_LOAD_HISTORY"})
    if rows and usable:
        earliest = date.fromisoformat(first)+timedelta(days=7)
        offset = max(0, (earliest-ANCHOR).days)
        start = ANCHOR+timedelta(days=((offset+13)//14)*14)
        while start+timedelta(days=13) < today:
            load_end, end = start+timedelta(days=6), start+timedelta(days=13)
            identifier = f"auto14:{start.isoformat()}"
            dose, reason = _dose(source, rows, start, load_end, end)
            response = _response(days, start, load_end, end) if dose else None
            if reason or response is None:
                exclusions.append({"id": identifier, "start": start.isoformat(), "end": end.isoformat(),
                                   "reason": reason or "INSUFFICIENT_COMMON_RESPONSE"})
                start += timedelta(days=14)
                continue
            reasons = _confounds(days, selected, history, start-timedelta(days=7), end, periodization, events)
            outcomes = _ti_outcomes(trainability, start, response["returned_on"], end)+_test_outcomes(selected, start, response["returned_on"], end)
            # Contradictory comparable protocols cannot become selective reward.
            for scope in {o["scope"] for o in outcomes}:
                values = [o for o in outcomes if o["scope"] == scope]
                if any(o["change_percent"] >= o["meaningful_change_percent"] for o in values) and any(o["change_percent"] <= -o["meaningful_change_percent"] for o in values):
                    reasons.append("CONFLICTING_OUTCOMES")
                    outcomes = [o for o in outcomes if o["scope"] != scope]
            reasons = sorted(set(reasons))
            if reasons:
                exclusions.append({"id": identifier, "start": start.isoformat(), "end": end.isoformat(), "reason": "CONFOUNDED_EPISODE", "details": reasons})
            if not outcomes:
                exclusions.append({"id": identifier, "reason": "INDEPENDENT_OUTCOME_UNAVAILABLE"})
            revisions = sorted((kind, key, e["revision"]) for (kind, key), e in selected.items()
                               if (start-timedelta(days=28)).isoformat() <= str(e.get("observed_date") or e["payload"].get("day") or key) <= end.isoformat())
            provenance = {"evidence_version": VERSION, "generation_id": history.get("generation_id"),
                          "analysis_revision": history.get("revision"), "equivalence_version": source.get("equivalence_version"),
                          "response_revision_fingerprint": _hash(revisions), "response_entry_count": len(revisions),
                          "outcome_day": max((o["outcome_day"] for o in outcomes), default=None),
                          "response_basis": "COMMON_NON_FUNCTIONAL_CHANNELS", "dose_basis": "ACTUAL_DIRECT_Q_WITH_CANONICAL_E_CONTEXT",
                          "independent_outcome": bool(outcomes), "confounds": reasons}
            episode = {"id": identifier, "start": start.isoformat(), "load_end": load_end.isoformat(), "end": end.isoformat(),
                       "observed_on": today.isoformat(), "context_key": context_key, **dose, "response": response,
                       "outcomes": outcomes, "quality": round(min(1., response["coverage"]/.4)*response["observed_days"]/14, 6),
                       "provenance": provenance, "confounded": bool(reasons)}
            # Source generation IDs may change during an otherwise identical
            # import. Scientific evidence, not refresh count, defines identity.
            fingerprint = _hash({k: episode[k] for k in ("dose", "dose_change", "intensity_change", "response", "outcomes", "confounded")})
            episode["provenance"]["evidence_fingerprint"] = fingerprint
            old = previous.get(identifier)
            if old and old.get("provenance", {}).get("evidence_fingerprint") == fingerprint:
                episode["observed_on"] = old["observed_on"]
            episodes[identifier] = episode
            start += timedelta(days=14)
    ordered = sorted(episodes.values(), key=lambda e: (e["start"], e["id"]))[-MAX_EPISODES:]
    current = _current(days, selected, source, rows, history, today, usable)
    if ordered:
        latest = ordered[-1]
        # A return measured last week cannot authorize an increase when today's
        # same observed panel is elevated again. Require a recent actual pair.
        panel = latest["response"]["channels"]
        weight = sum(CHANNELS[k][1] for k in panel)
        last = current["latest_day"]
        if last and weight and (today-date.fromisoformat(last)).days <= 1 and (today-date.fromisoformat(latest["end"])).days <= 14:
            pair = _dates(date.fromisoformat(last)-timedelta(days=1), date.fromisoformat(last))
            values = [_scores(days.get(d, {}), latest["response"].get("references")) for d in pair]
            if all(all(k in row for k in panel) for row in values):
                current["recovered"] = all(sum(row[k]*CHANNELS[k][1] for k in panel)/weight <= latest["response"]["baseline_score"]+5 for row in values)
        if current["illness_hold"] or current["lab_review"]:
            current["recovered"] = False
        current["latest_outcome_day"] = max((o["outcome_day"] for e in ordered for o in e["outcomes"]), default=None)
    return {"episodes": ordered, "current": current, "exclusions": exclusions}
