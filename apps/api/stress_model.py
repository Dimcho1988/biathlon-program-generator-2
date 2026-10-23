"""Transparent pilot stress estimator. Pure reads, no training control or learning.

Nominal indicator weights sum to 100. Missing indicators retain no contribution;
the observed weights are normalized once, across the whole model. Coverage is a
data-completeness measure, never a probability or confidence interval.
"""
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from .response_monitoring import number, relative_score

# Reviewed expert starting values, not a clinically validated scale.
CHANNELS = {
    "fatigue": ("subjective", 8), "sleep_quality": ("subjective", 4),
    "sleep_duration": ("subjective", 4), "stress": ("subjective", 6),
    "soreness": ("subjective", 5), "motivation": ("subjective", 5),
    "competition_motivation": ("subjective", 3),
    "performance": ("functional", 12), "rpe": ("functional", 7),
    "volume": ("functional", 2), "control_test": ("functional", 4),
    "hrv": ("physiology", 8), "resting_hr": ("physiology", 7),
    "morning_weight": ("weight", 12), "session_weight": ("weight", 3),
    "CK": ("biochemistry", 2), "UREA": ("biochemistry", 2),
    "TC_RATIO": ("biochemistry", 2), "HEMOGLOBIN": ("biochemistry", 1),
    "FERRITIN": ("biochemistry", 1), "TSAT": ("biochemistry", 1),
    "CRP": ("biochemistry", 1),
}
MIN_COVERAGE, MIN_GROUPS = 40, 2


def clipped(value):
    return round(max(0., min(100., value)), 3)


def baseline(values, floor, minimum=14):
    values = [v for v in values if number(v) is not None]
    if len(values) < minimum:
        return None
    center = median(values)
    return {"median": center, "spread": max(floor, 1.4826 * median(abs(v-center) for v in values)), "count": len(values)}


def prior_dates(anchor, days=28):
    return [(anchor-timedelta(days=i)).isoformat() for i in range(1, days+1)]


def sleep_hours(report, metrics):
    manual = number((report or {}).get("sleep_hours"))
    if manual is not None:
        return manual
    sleep = metrics.get("sleep_duration", {})
    value = number(sleep.get("value"))
    return value / 3600 if value is not None and sleep.get("unit") == "s" and 0 <= value <= 86400 else None


def freeze_baseline(selected, devices, anchor, trainability=None):
    """Persist numeric references, so later edits cannot move a started block."""
    daily = {k: e["payload"] for (kind, k), e in selected.items() if kind == "DAILY"}
    weights = {k: e["payload"] for (kind, k), e in selected.items() if kind == "WEIGHT"}
    dates = prior_dates(anchor)
    mornings = [p["morning_kg"] for d in dates if (p := weights.get(d, {})).get("morning_standardized") and p.get("morning_kg")]
    center = median(mornings) if mornings else 0
    ti_groups = defaultdict(lambda: defaultdict(list))
    for row in trainability or []:
        idx = row.get("index") or {}
        general = idx.get("general") or {}
        value = number(general.get("index"))
        if row["local_date"] in dates and idx.get("admission", {}).get("status") == "ACCEPTED" and general.get("valid") and value and value > 0:
            ti_groups[(row["sport"], idx["comparison_key"])][row["local_date"]].append(value)
    ti = []
    for (sport, comparison), observations in ti_groups.items():
        values = [median(v) for v in observations.values()]
        ti.append({"sport": sport, "comparison_key": comparison, "baseline": baseline(values, .03*median(values))})
    return {
        "sleep_duration": baseline([sleep_hours(daily.get(d), devices.get(d, {})) for d in dates], .5),
        "morning_weight": baseline(mornings, max(.2, .005*center)),
        "trainability": ti,
    }


def test_observation(tests, day):
    observed = []
    for current in tests:
        if current.get("day") != day or not current.get("comparable"):
            continue
        signature = ("protocol", "protocol_version", "unit", "direction", "conditions")
        earliest = (date.fromisoformat(day)-timedelta(days=365)).isoformat()
        per_day = defaultdict(list)
        for p in tests:
            if earliest <= p["day"] < day and p.get("comparable") and all(p.get(k) == current.get(k) for k in signature):
                per_day[p["day"]].append(p["value"])
        values = [median(v) for v in per_day.values()]
        floor = current["value"] * current.get("meaningful_change_percent", 1) / 100
        base = baseline(values, max(floor, 1e-6), 3)
        score = relative_score(current["value"], base, -1 if current["direction"] == "HIGHER" else 1)
        if score is not None:
            observed.append((score, current, base))
    if not observed:
        return None
    # Each comparable protocol gets one vote, regardless of repeated trials.
    protocols = defaultdict(list)
    for score, p, base in observed:
        protocols[(p["protocol"], p["protocol_version"])].append(score)
    return {"score": median(median(v) for v in protocols.values()), "raw": observed[-1][1]["value"],
            "unit": observed[-1][1]["unit"], "baseline": observed[-1][2], "source": "ONFLOWS", "observed_on": day}


def ti_observation(rows, day, references):
    estimates = []
    for current in rows:
        idx = current.get("index") or {}
        general = idx.get("general") or {}
        if current["local_date"] != day or idx.get("admission", {}).get("status") != "ACCEPTED" or not general.get("valid"):
            continue
        value = number(general.get("index"))
        if value is None or value <= 0:
            continue
        base = next((r["baseline"] for r in references if r["sport"] == current["sport"] and r["comparison_key"] == idx.get("comparison_key")), None)
        score = relative_score(value, base)  # HR / speed: higher is a worse response.
        if score is not None:
            estimates.append({"score": score, "raw": value, "unit": "TI", "baseline": base, "source": "HRMOD_VFLAT", "observed_on": day})
    if not estimates:
        return None
    return {**estimates[-1], "score": median(v["score"] for v in estimates)}


def lab_signature(report, analyte):
    p = report["payload"]
    if not p.get("comparable") or not p.get("collection_time"):
        return None
    results = {r["analyte"]: r for r in report["results"]}
    if analyte == "TC_RATIO":
        if report["testosterone_cortisol_ratio"] is None:
            return None
        sample = results["TESTOSTERONE"]["sample"]
        value = report["testosterone_cortisol_ratio"]
    else:
        r = results.get(analyte)
        if r is None or r["qualifier"] != "EQ":
            return None
        sample, value = r["sample"], r["normalized_value"]
    signature = (p["laboratory"].casefold(), p["protocol"].casefold(), p["fasting"], p["collection_time"][:2], sample)
    return signature, value


def laboratory_observation(reports, day, analyte):
    scores = []
    for current in reports:
        if current["payload"]["day"] != day or (measurement := lab_signature(current, analyte)) is None:
            continue
        signature, value = measurement
        earliest = (date.fromisoformat(day)-timedelta(days=365)).isoformat()
        values = defaultdict(list)
        for p in reports:
            previous = lab_signature(p, analyte)
            if earliest <= p["payload"]["day"] < day and previous is not None and previous[0] == signature:
                values[p["payload"]["day"]].append(previous[1])
        daily = [median(v) for v in values.values()]
        floor = max(1e-6, (median(daily) if daily else value) * (.03 if analyte == "HEMOGLOBIN" else .10))
        base = baseline(daily, floor, 3)
        direction = 1 if analyte in ("CK", "UREA", "CRP") else -1
        score = relative_score(value, base, direction)
        if score is not None:
            unit = None if analyte == "TC_RATIO" else next(r["normalized_unit"] for r in current["results"] if r["analyte"] == analyte)
            scores.append({"score": score, "raw": value, "unit": unit, "baseline": base, "source": "LAB", "observed_on": day})
    return {**scores[-1], "score": median(v["score"] for v in scores)} if scores else None


def common_comparison(current, previous):
    """Compare identical inputs only; disappearance cannot masquerade as recovery."""
    before = {c["key"]: c for c in previous["channels"] if c["score"] is not None}
    pairs = [(c, before[c["key"]]) for c in current["channels"] if c["score"] is not None and c["key"] in before]
    weight = sum(c["weight"] for c, _ in pairs)
    groups = {c["group"] for c, _ in pairs}
    return {"coverage": round(weight*100, 2), "delta": round(sum(c["weight"]*(c["score"]-p["score"]) for c, p in pairs)/weight, 2) if weight >= .4-1e-9 and len(groups) >= 2 else None}


def attach_stress_model(history, selected, devices, trainability):
    daily = {k: e["payload"] for (kind, k), e in selected.items() if kind == "DAILY"}
    tests = [e["payload"] for (kind, _), e in selected.items() if kind == "TEST"]
    blocks = {e["entry_key"]: e["payload"] for e in history["blocks"]}
    labs = history.get("lab_reports", [])
    sessions = defaultdict(list)
    for s in history["sessions"]:
        sessions[s["day"]].append(s)
    baseline_cache = {}
    for day in history["days"]:
        key, report = day["day"], day["daily_report"] or {}
        anchor = date.fromisoformat(day["baseline_anchor"])
        block = blocks.get(day["block_key"], {})
        if day["baseline_anchor"] not in baseline_cache:
            baseline_cache[day["baseline_anchor"]] = freeze_baseline(selected, devices, anchor, trainability)
        frozen = block.get("stress_baseline", baseline_cache[day["baseline_anchor"]])
        channels = {k: {"key": k, "group": group, "weight": weight/100, "score": None,
                       "raw": None, "unit": None, "source": None, "observed_on": None,
                       "baseline": None, "status": "MISSING"} for k, (group, weight) in CHANNELS.items()}

        def put(name, score, raw=None, unit=None, source="ONFLOWS", observed_on=key, baseline=None, status=None):
            channels[name].update(score=None if score is None else clipped(score), raw=raw, unit=unit, source=source,
                                  observed_on=observed_on if raw is not None or score is not None else None, baseline=baseline,
                                  status=status or ("AVAILABLE" if score is not None else "NEEDS_BASELINE" if raw is not None else "MISSING"))

        for field in ("fatigue", "sleep_quality", "stress", "soreness", "motivation", "competition_motivation"):
            value = number(report.get(field))
            put(field, (value-1)*25 if value is not None else None, value, "1–5")
        hours = sleep_hours(report, day["device_metrics"])
        base = frozen.get("sleep_duration")
        put("sleep_duration", relative_score(hours, base, -1), hours, "h", "ONFLOWS" if report.get("sleep_hours") is not None else "INTERVALS", baseline=base)
        for field in ("resting_hr", "hrv"):
            p = day["physiology"][field]
            put(field, p["score"], p["raw"], "ms" if field == "hrv" else "bpm", "INTERVALS", baseline=p["baseline"])

        # Session responses belong to the activity's date, never an undated carry-forward.
        rs = [s for s in sessions[key] if s["deviation_score"] is not None and s["duration_minutes"]]
        if rs:
            put("rpe", sum(s["deviation_score"]*s["duration_minutes"] for s in rs)/sum(s["duration_minutes"] for s in rs), median(s["rpe"] for s in rs), "RPE")
        elif sessions[key]:
            raw = [s["rpe"] for s in sessions[key] if s["rpe"] is not None]
            put("rpe", None, median(raw) if raw else None, "RPE")

        ti = ti_observation(trainability, key, frozen.get("trainability", []))
        performance, volumes = [], []
        if ti:
            performance.append(ti["score"])
        for s in sessions[key]:
            ex = s.get("execution") or {}
            planned, actual = number(ex.get("planned_speed_kmh")), number(ex.get("executed_speed_kmh"))
            reason = ex.get("execution_reason")
            if ex.get("execution_comparable") and reason in ("AS_PLANNED", "FATIGUE") and planned and actual:
                performance.append(clipped(50 + 15*(1-actual/planned)/.03))
            minutes = number(ex.get("planned_duration_minutes"))
            if ex.get("execution_comparable") and minutes and s["duration_minutes"] and reason in ("AS_PLANNED", "FATIGUE"):
                volumes.append(clipped(50 + 50*max(0, 1-s["duration_minutes"]/minutes)/.30))
        if performance:
            put("performance", median(performance), ti["raw"] if ti else None, "TI" if ti else None,
                "HRMOD_VFLAT_AND_EXECUTION" if ti else "ONFLOWS", baseline=ti["baseline"] if ti else None)
        if volumes:
            put("volume", median(volumes))
        test = test_observation(tests, key)
        if test:
            put("control_test", **test)

        w = day["body_observations"]["weight"]
        raw = (w["report"] or {}).get("morning_kg")
        ratio = w["morning_ratio"]
        if ratio is not None:
            # The 7-day ratio remains the author's exact observation. A frozen
            # block reference additionally catches a slowly accumulating deficit.
            scores = [clipped(50 + 15*(1-ratio)/.005)]
            base = frozen.get("morning_weight")
            if base:
                scores.append(relative_score(raw, base, -1))
            put("morning_weight", max(scores), ratio, "ratio", baseline=base)
        else:
            put("morning_weight", None, raw, "kg", status="NEEDS_BASELINE" if raw else "MISSING")
        if w["session_ratio"] is not None:
            put("session_weight", 50 + 15*(1-w["session_ratio"])/.01, w["session_ratio"], "ratio")
        for analyte, (group, _) in CHANNELS.items():
            if group == "biochemistry":
                lab = laboratory_observation(labs, key, analyte)
                if lab:
                    put(analyte, **lab)
                elif any(lab_signature(r, analyte) is not None for r in labs if r["payload"]["day"] == key):
                    channels[analyte]["status"] = "NEEDS_BASELINE"
        available = sum(c["weight"] for c in channels.values() if c["score"] is not None)
        groups = []
        for group, nominal in history["weights"].items():
            observed = [c for c in channels.values() if c["group"] == group and c["score"] is not None]
            observed_weight = sum(c["weight"] for c in observed)
            subtotal = sum(c["weight"]*c["score"] for c in observed)
            groups.append({"key": group, "weight": nominal, "available_weight": round(observed_weight, 6),
                           "effective_weight": round(observed_weight/available, 6) if available else 0,
                           "score": round(subtotal/observed_weight, 3) if observed_weight else None,
                           "contribution": round(subtotal/available, 3) if observed_weight and available else None})
        for c in channels.values():
            c["effective_weight"] = round(c["weight"]/available, 6) if available and c["score"] is not None else 0
            c["contribution"] = round(c["score"]*c["weight"]/available, 3) if c["score"] is not None and available else None
        count = sum(g["score"] is not None for g in groups)
        partial = available < MIN_COVERAGE/100-1e-9 or count < MIN_GROUPS
        total = round(sum(c["score"]*c["weight"] for c in channels.values() if c["score"] is not None)/available, 3) if available else None
        day.update(total=total, coverage=round(available*100, 2), groups=groups, channels=list(channels.values()),
                   assessment_quality="NO_DATA" if not available else "PARTIAL" if partial else "SUFFICIENT",
                   assessment_basis="MULTICOMPONENT", subjective_state=day["state"], trend_3d=None,
                   comparison_previous=None, mix_changed=False)
        if day["body_observations"]["context_status"] == "REVIEW_LAB_REFERENCE" or report.get("pain_or_illness"):
            day["state"] = "REVIEW"
        elif partial:
            day["state"] = "PARTIAL" if available else "INSUFFICIENT_DATA"
        elif day["state"] not in ("EXPECTED_ELEVATION", "ELEVATED", "REVIEW_AFTER_RECOVERY"):
            elevated = any(c["score"] is not None and c["score"] >= 65 for c in channels.values())
            day["state"] = "EXPECTED_ELEVATION" if elevated and day["phase"] == "BUILD" else "ELEVATED" if elevated else "WITHIN_USUAL"
    for i, day in enumerate(history["days"]):
        if i:
            previous = history["days"][i-1]
            day["mix_changed"] = {c["key"] for c in day["channels"] if c["score"] is not None} != {c["key"] for c in previous["channels"] if c["score"] is not None}
            day["comparison_previous"] = common_comparison(day, previous)
        window = history["days"][max(0, i-2):i+1]
        if len(window) == 3:
            common = set.intersection(*[{c["key"] for c in d["channels"] if c["score"] is not None} for d in window])
            weight = sum(CHANNELS[k][1] for k in common)
            if weight >= MIN_COVERAGE and len({CHANNELS[k][0] for k in common}) >= MIN_GROUPS:
                day["trend_3d"] = round(sum(sum(c["weight"]*c["score"] for c in d["channels"] if c["key"] in common)/(weight/100) for d in window)/3, 3)
    history["settings"].update(minimum_coverage_percent=MIN_COVERAGE, minimum_groups=MIN_GROUPS, trend_days=3,
                               scoring_version="expert-stress-v2", indicator_weights={k: v[1]/100 for k, v in CHANNELS.items()},
                               missing_policy="AVAILABLE_WEIGHT_NORMALIZATION", lab_freshness="SAMPLE_DAY_ONLY",
                               normal_reference_score=50, points_per_personal_spread=15, validated=False)
    return history
