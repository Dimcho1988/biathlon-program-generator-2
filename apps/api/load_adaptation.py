"""Deterministic learning from persisted observations, independent of Recovery.

Replaying completed, non-overlapping blocks makes repeated plan generation
idempotent. A general fatigue report never attributes a response to a zone.
"""
from datetime import date, timedelta
from .response_monitoring import latest_entries, block_summary, subjective_score
from biathlon.constants import COMPONENTS

VERSION = "observed-load-adaptation-v1"


def load_observations(entries, rows, test_day):
    """Freeze observed load with the outcome test so learning survives imports.

    These are actual-data sums, not fitted parameters or synthetic daily loads.
    Saving the report and its observation uses the existing single revision.
    """
    loads = {(r["date"],r["zone"]):r["effective_load"] for r in rows}
    selected = latest_entries(entries)
    archived = {}
    for entry in sorted(selected.values(), key=lambda e: (e["payload"].get("day", ""), e["revision"])):
        if entry["kind"] != "TEST":
            continue
        for observation in entry["payload"].get("observed_load_windows", []):
            if observation.get("basis") == "COMPLETE_ACTUAL_CANONICAL_E":
                archived[observation["block"]] = {
                    **observation,
                    "source": observation.get("source") or entry["payload"].get("load_source"),
                    "retained_from": {"entry_key": entry["entry_key"], "revision": entry["revision"]},
                }
    result = []
    for (kind,key),entry in selected.items():
        b = entry["payload"]
        if kind != "BLOCK" or not b["recovery_end"] < test_day.isoformat() <= (date.fromisoformat(b["recovery_end"])+timedelta(days=14)).isoformat():
            continue
        start, end = date.fromisoformat(b["start"]), date.fromisoformat(b["recovery_end"])
        length = (end-start).days+1
        current = [(start+timedelta(days=n)).isoformat() for n in range(length)]
        previous = [(start-timedelta(days=n)).isoformat() for n in range(1,length+1)]
        if all((d,z) in loads for d in current+previous for z in COMPONENTS):
            result.append({"block":key, "start":b["start"], "recovery_end":b["recovery_end"],
                           "basis":"COMPLETE_ACTUAL_CANONICAL_E", "days_per_window":length,
                           "previous":{z:sum(loads[(d,z)] for d in previous) for z in COMPONENTS},
                           "current":{z:sum(loads[(d,z)] for d in current) for z in COMPONENTS}})
        elif key in archived:
            frozen = archived[key]
            if frozen["start"] == b["start"] and frozen["recovery_end"] == b["recovery_end"]:
                result.append(frozen)
    return result


def assess(entries, today, *, rows=()):
    selected = latest_entries(entries)
    daily = {key:e["payload"] for (kind,key),e in selected.items() if kind == "DAILY" and key <= today.isoformat()}
    tests = [e["payload"] for (kind,_),e in selected.items() if kind == "TEST" and e["payload"].get("comparable")
             and e["payload"]["day"] <= today.isoformat()]
    blocks = sorted([e for (kind,_),e in selected.items() if kind == "BLOCK"], key=lambda e:e["payload"]["start"])
    global_state = {"growth_factor": 1., "load_factor": 1., "observed_blocks": 0}
    components, evidence = {}, []
    loads = {(r["date"],r["zone"]):r["effective_load"] for r in rows}
    archived = {o["block"]:o for t in sorted(tests,key=lambda t:t["day"]) for o in t.get("observed_load_windows", [])
                if o.get("basis") == "COMPLETE_ACTUAL_CANONICAL_E"}
    last_end = ""
    for entry in blocks:
        b = entry["payload"]
        if b["phase"] != "BUILD" or b["recovery_end"] >= today.isoformat():
            continue
        if b["start"] <= last_end:
            evidence.append({"block": entry["entry_key"], "status": "OVERLAPPING_BLOCK_EXCLUDED"})
            continue
        last_end = b["recovery_end"]
        summary = block_summary(b, daily, today)
        base = b.get("baseline", {}).get("subjective")
        final_days = [(date.fromisoformat(b["recovery_end"])-timedelta(days=n)).isoformat() for n in (1,0)]
        persistent_elevation = bool(base) and all(d > b["load_end"] and subjective_score(daily.get(d)) is not None
            and (subjective_score(daily[d])-base["median"])/base["spread"] > 1 for d in final_days)
        coverage = summary["observed_days"] / max(1, summary["tracked_days"])
        reports = [v for d,v in daily.items() if b["start"] <= d <= b["recovery_end"]]
        if coverage < .7 or any(r.get("pain_or_illness") for r in reports):
            evidence.append({"block":entry["entry_key"], "status":"INSUFFICIENT_OR_CONFOUNDED_OBSERVATIONS"})
            continue
        scopes = b.get("components") or ["GLOBAL"]
        for zone in scopes:
            start, end = date.fromisoformat(b["start"]), date.fromisoformat(b["recovery_end"])
            length = (end-start).days+1
            current_days = [(start+timedelta(days=n)).isoformat() for n in range(length)]
            previous_days = [(start-timedelta(days=n)).isoformat() for n in range(1,length+1)]
            zones = COMPONENTS if zone == "GLOBAL" else [zone]
            known_load = all((d,z) in loads for d in current_days+previous_days for z in zones)
            prior_load = sum(loads.get((d,z),0.) for d in previous_days for z in zones)
            current_load = sum(loads.get((d,z),0.) for d in current_days for z in zones)
            frozen = archived.get(entry["entry_key"])
            if not known_load and frozen and frozen["start"] == b["start"] and frozen["recovery_end"] == b["recovery_end"]:
                prior_load = sum(frozen["previous"][z] for z in zones)
                current_load = sum(frozen["current"][z] for z in zones)
                known_load = True
            if not known_load or prior_load <= 0 or current_load <= prior_load:
                evidence.append({"block":entry["entry_key"], "component":zone, "status":"OBSERVED_LOAD_INCREASE_REQUIRED"})
                continue
            pairs = []
            for after in tests:
                # The test must assess the outcome AFTER the planned recovery.
                if not b["recovery_end"] < after["day"] <= (date.fromisoformat(b["recovery_end"])+timedelta(days=14)).isoformat():
                    continue
                if zone != "GLOBAL" and zone not in after.get("components", []):
                    continue
                if zone == "GLOBAL" and after.get("components"):
                    continue
                before = [t for t in tests if (date.fromisoformat(b["start"])-timedelta(days=42)).isoformat() <= t["day"] < b["start"]
                          and all(t.get(k) == after.get(k) for k in ("protocol", "protocol_version", "unit", "direction", "conditions"))
                          and set(t.get("components", [])) == set(after.get("components", []))]
                if not before:
                    continue
                prior = max(before, key=lambda t:t["day"])
                change = 100*(after["value"]/prior["value"]-1)*(1 if after["direction"] == "HIGHER" else -1)
                threshold = max(prior.get("meaningful_change_percent", 1.), after.get("meaningful_change_percent", 1.))
                pairs.append((after["day"], change, threshold))
            if not pairs:
                evidence.append({"block":entry["entry_key"], "component":zone, "status":"COMPARABLE_OUTCOME_REQUIRED"})
                continue
            # Conflicting protocols do not prove adaptation or failure.
            positive = all(change >= threshold for _,change,threshold in pairs)
            negative = all(change <= -threshold for _,change,threshold in pairs)
            status = ("POSITIVE" if positive and summary["status"] == "OBSERVED_RETURN" else
                      "NEGATIVE" if negative and summary["status"] == "REVIEW" and persistent_elevation else "INCONCLUSIVE")
            state = global_state if zone == "GLOBAL" else components.setdefault(zone, {"growth_factor":1., "load_factor":1., "observed_blocks":0})
            if status != "INCONCLUSIVE":
                state["observed_blocks"] += 1
                state["growth_factor"] = round(max(.25, state["growth_factor"]*.75) if status == "NEGATIVE" else min(1., state["growth_factor"]+.05), 4)
                state["load_factor"] = .9 if status == "NEGATIVE" and (today-date.fromisoformat(b["recovery_end"])).days <= 28 else 1.
            evidence.append({"block":entry["entry_key"], "revision":entry["revision"], "component":zone,
                             "status":status, "test_changes_percent":[round(v,3) for _,v,_ in pairs],
                             "observed_effective_load_change_percent":round(100*(current_load/prior_load-1),3),
                             "subjective_status":summary["status"], "coverage":round(coverage,3)})
    # Silence does not establish recovery from an explicit illness/pain report.
    # A newer report clears it; the flag is not a learned capacity estimate.
    recent = daily[max(daily)] if daily else None
    hold = bool(recent and recent.get("pain_or_illness"))
    return {"version":VERSION, "as_of":today.isoformat(), "global":global_state,
            "latest_report_day":max(daily) if daily else None,
            "components":components, "evidence":evidence, "hold_for_reported_illness_or_pain":hold,
            "recovery_is_input":False, "missing_feedback_is_positive":False,
            "basis":"REPLAY_OF_PERSISTED_COMPLETED_BLOCKS_AND_COMPARABLE_TESTS",
            "settings":{"minimum_coverage":.7, "negative_growth_multiplier":.75,
                        "positive_growth_step":.05, "minimum_growth_factor":.25,
                        "temporary_load_factor":.9, "temporary_load_days":28}}
