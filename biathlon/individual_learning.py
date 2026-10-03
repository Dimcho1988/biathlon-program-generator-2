"""Small, replayable Bayesian response models and a bounded decision policy.

Actual direct Q, actual within-zone Q/time and independent later outcomes are
the observations. Forecasts, Recovery predictions and planner scores are never
training labels. Full posterior covariance preserves uncertainty when component
changes are correlated. These are observational associations, not causal effects.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from functools import lru_cache
from hashlib import sha256
import json
import math

import numpy as np

from .constants import COMPONENTS

VERSION = "individual-response-bayes-v2"
FEATURES = tuple([f"volume:{z}" for z in COMPONENTS] + [f"intensity:{z}" for z in COMPONENTS if z != "STR"])
VOLUME_SCALE = math.log(1.05)
INTENSITY_SCALE = math.log(1.02)
MIN_FIT = 4
MIN_VALIDATION = 4
PREPARATION = {"GENERAL_PREPARATION", "SPECIAL_PREPARATION"}
DOSE_OFFSET = 1.  # One equivalent minute; numerical scale, not imputed exposure.
TIME_OFFSET = 1.  # One clock minute; keeps known zero exposure finite.


def dose_changes(bq, aq, bt, at):
    """Finite dose/time coordinates, including observed starts and stops.

    Both coordinates are zero at Q=time=0. Their difference approaches
    log(Q/time) for substantial exposure, without inventing an effort at zero.
    Missing measurements must be rejected before calling this function.
    """
    volume = math.log((aq+DOSE_OFFSET)/(bq+DOSE_OFFSET))
    effort = volume-math.log((at+TIME_OFFSET)/(bt+TIME_OFFSET))
    return volume, effort


def action_contrast(component, kind, step, reference, intensity_scale=None):
    """Transform the proposed physical Q/time change exactly like observations.

    Volume scales Q and time together. Intensity preserves prescribed Q and
    changes the time needed at the new Q/time, as the canonical planner does.
    """
    dose = (reference or {}).get(component) or {}
    q, minutes = dose.get("weekly_q"), dose.get("weekly_minutes")
    if not _number(q) or not _number(minutes) or q <= 0 or minutes <= 0:
        return None
    if kind == "volume":
        aq, at = q*(1+step), minutes*(1+step)
    else:
        scale = (intensity_scale or {}).get(component)
        if not _number(scale) or scale <= 0 or 1+step*scale <= 0:
            return None
        aq, at = q, minutes/(1+step*scale)
    volume, effort = dose_changes(q, aq, minutes, at)
    contrast = [0.]*(len(FEATURES)+1)
    contrast[FEATURES.index(f"volume:{component}")+1] = volume/VOLUME_SCALE
    if component != "STR":
        contrast[FEATURES.index(f"intensity:{component}")+1] = effort/INTENSITY_SCALE
    return contrast


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def features(episode):
    return [1.] + [float(episode.get("dose_change", {}).get(z, 0.)) / VOLUME_SCALE for z in COMPONENTS] + [
        float(episode.get("intensity_change", {}).get(z, 0.)) / INTENSITY_SCALE for z in COMPONENTS if z != "STR"]


def _samples(episodes, outcome, scope="GLOBAL"):
    result = []
    for e in episodes:
        if e.get("confounded") or not _number(e.get("quality")) or e["quality"] <= 0:
            continue
        y, weight = None, e["quality"]
        if outcome == "performance":
            values = [o for o in e.get("outcomes", []) if o.get("scope") == scope and _number(o.get("change_percent"))]
            if values:
                # Multiple protocols in one episode are ONE observation. Opposing
                # meaningful signs are inconclusive, never cherry-picked.
                ys = [float(v["change_percent"]) for v in values]
                if min(ys) < -.5 and max(ys) > .5:
                    continue
                weights = [max(.01, min(1., float(v.get("weight", .5)))) for v in values]
                y = sum(a*b for a, b in zip(ys, weights)) / sum(weights)
                weight *= max(weights)
        elif outcome == "burden":
            y = (e.get("response") or {}).get("burden_delta")
        else:
            y = (e.get("response") or {}).get("recovery_days")
        x = features(e)
        if _number(y) and all(_number(v) for v in x):
            result.append({"id": e["id"], "end": e["end"], "prediction_date": e.get("start", e["end"]),
                           "available_on": e.get("observed_on", e["end"]),
                           "x": x, "y": float(y), "weight": min(1., weight)})
    return sorted(result, key=lambda s: (s["end"], s["id"]))


def posterior(samples, *, outcome="performance", as_of=None):
    """Gaussian linear posterior with fixed conservative noise floors.

    Quality and age lower precision; no target-derived reweighting. The prior
    predicts zero effect of changing a dose, not guaranteed benefit from more.
    """
    noise, slope_sd, intercept_sd = {"performance": (2., 2., 5.), "burden": (10., 12., 20.),
                                    "recovery": (2., 2., 4.)}[outcome]
    dimension = len(FEATURES)+1
    precision = np.diag([1/intercept_sd**2] + [1/slope_sd**2]*(dimension-1))
    rhs = np.zeros(dimension)
    for s in samples:
        x = np.asarray(s["x"], dtype=float)
        age = max(0, (as_of-date.fromisoformat(s["end"])).days) if as_of else 0
        w = s["weight"] * 2**(-age/180.) / noise**2
        precision += w*np.outer(x, x)
        rhs += w*x*s["y"]
    covariance = np.linalg.solve(precision, np.eye(dimension))
    mean = covariance @ rhs
    return {"mean": mean.tolist(), "covariance": covariance.tolist(), "noise": noise,
            "count": len(samples), "features": list(FEATURES)}


def predict(model, x, *, observation=True):
    vector = np.asarray(x, dtype=float)
    center = float(vector @ np.asarray(model["mean"]))
    variance = max(0., float(vector @ np.asarray(model["covariance"]) @ vector))
    if observation:
        variance += model["noise"]**2
    spread = math.sqrt(variance)
    return {"mean": round(center, 5), "low": round(center-1.96*spread, 5),
            "high": round(center+1.96*spread, 5), "sd": round(spread, 5)}


def validate(samples, *, outcome="performance"):
    """Expanding chronological evaluation, before each held-out label is seen.

    Comparators predict no improvement and the prior observed weighted mean.
    Beating this gate does not prove a counterfactual policy or clinical safety.
    """
    errors, neutral_errors, mean_errors, covered = [], [], [], []
    for i in range(MIN_FIT, len(samples)):
        previous, current = samples[:i], samples[i]
        # Do not train on an outcome sharing the held-out outcome window.
        previous = [s for s in previous if s["end"] < current["prediction_date"]
                    and s["available_on"] <= current["prediction_date"]]
        if len(previous) < MIN_FIT:
            continue
        p = predict(posterior(previous, outcome=outcome, as_of=date.fromisoformat(current["end"])), current["x"])
        baseline = sum(s["y"]*s["weight"] for s in previous)/sum(s["weight"] for s in previous)
        errors.append(abs(p["mean"]-current["y"]))
        neutral_errors.append(abs(current["y"]))
        mean_errors.append(abs(baseline-current["y"]))
        covered.append(p["low"] <= current["y"] <= p["high"])
    n = len(errors)
    mae = sum(errors)/n if n else None
    baseline = min(sum(neutral_errors)/n, sum(mean_errors)/n) if n else None
    status = "WARMUP" if n < MIN_VALIDATION else "PASSED" if mae <= baseline*.95 and sum(covered)/n >= .75 else "FAILED"
    return {"status": status, "evaluated": n, "model_mae": round(mae, 4) if n else None,
            "baseline_mae": round(baseline, 4) if n else None,
            "interval_coverage": round(sum(covered)/n, 3) if n else None,
            "basis": "CHRONOLOGICAL_OUTCOME_PREDICTION_NOT_POLICY_CAUSAL_VALIDATION"}


@lru_cache(maxsize=128)
def _fit_cached(serialized, day):
    episodes = json.loads(serialized)
    today = date.fromisoformat(day)
    models = {}
    for scope in ("GLOBAL", *COMPONENTS):
        s = _samples(episodes, "performance", scope)
        models[scope] = {"posterior": posterior(s, as_of=today), "validation": validate(s), "samples": s}
    for outcome in ("burden", "recovery"):
        s = _samples(episodes, outcome)
        models[outcome] = {"posterior": posterior(s, outcome=outcome, as_of=today),
                           "validation": validate(s, outcome=outcome), "samples": s}
    return models


def fit(episodes, today):
    # No reference to athlete names/raw measurements in cached fitting inputs.
    compact = [{k: e[k] for k in ("id", "start", "end", "observed_on", "quality", "dose_change", "intensity_change", "response", "outcomes", "confounded") if k in e}
               for e in sorted(episodes, key=lambda e: (e["end"], e["id"]))
               if e["end"] < today.isoformat() and e.get("observed_on", e["end"]) <= today.isoformat()]
    return deepcopy(_fit_cached(json.dumps(compact, sort_keys=True, separators=(",", ":"), allow_nan=False), today.isoformat()))


def _identified(samples, feature_index):
    """Count materially isolated changes, preserving mixed-accent uncertainty."""
    return sum(abs(s["x"][feature_index]) >= .4 and
               all(abs(x) <= .35 for j, x in enumerate(s["x"][1:], 1) if j != feature_index) for s in samples)


def _admissible(model, index, step, current, burden, recovery, base_burden, base_recovery):
    """The same evidence and response ceilings govern new and retained choices."""
    return (model["validation"]["status"] == "PASSED" and _identified(model["samples"], index) >= 2
            and model["posterior"]["count"] >= 8 and
            (step < 0 or (current.get("recovered") and base_burden["mean"] + burden["high"] <= 10
                         and base_recovery["mean"] + recovery["high"] <= 7)))


def _neutral_components(reason):
    return {z: {"volume_factor": 1., "proposed_volume_factor": 1., "intensity_delta": 0.,
                "proposed_intensity_delta": 0., "reason": reason, "confidence": "LOW",
                "action": "HOLD", "evidence_count": 0} for z in COMPONENTS}


def decide(*, models, episodes, current, today, config, allowed_components, phase, taper=False,
           retained=None, intensity_scale=None, dose_reference=None):
    """Choose at most one bounded volume OR intensity experiment per 14 days.

    Generator feasibility is still authoritative. Zero is always a candidate.
    Intensity effects use measured Q/time; the caller supplies the conversion
    from a position change to Q/time so there is no invented speed multiplier.
    """
    mode = config.get("mode", "SHADOW")
    common_reason = "Нужни са още съпоставими наблюдения за индивидуална промяна."
    components = _neutral_components(common_reason)
    report = {"version": VERSION, "mode": mode, "status": "COLLECTING", "as_of": today.isoformat(),
              "effective_from": today.isoformat(), "expires_on": (today+timedelta(days=13)).isoformat(),
              "summary": common_reason, "evidence_count": len(episodes), "confidence": "LOW",
              "components": components, "predictions": {}, "validation": models["GLOBAL"]["validation"],
              "method_preferences": [], "candidate_count": 1, "causal_claim": False}
    if mode == "OFF":
        report.update(status="OFF", summary="Индивидуалното обучение е изключено.")
        return report
    latest = current.get("latest_day")
    fresh = bool(latest and 0 <= (today-date.fromisoformat(latest)).days <= 2)
    sufficient = current.get("coverage", 0) >= 40 and current.get("families", 0) >= 2
    uncertain = not fresh or not sufficient or not current.get("history_usable", False)
    caution = (current.get("illness_hold") or current.get("lab_review") or current.get("execution_caution")
               or current.get("unresolved_recovery")
               or (_number(current.get("stress_score")) and current["stress_score"] >= 75))
    if caution:
        report.update(status="CAUTION", summary="Има сигнал за преглед на състоянието. Самообучението не предлага промяна."
                      + (" Недостатъчни са и актуалните данни." if uncertain else ""))
        return report
    if uncertain:
        report["summary"] = "Събираме актуални съпоставими данни; запазени са обичайните правила за плана."
        return report
    if phase not in PREPARATION or taper:
        report.update(status="SHADOW" if mode == "SHADOW" else "READY",
                      summary="Наученото се запазва; текущият период не допуска нов експеримент.")
        return report
    eligible = [z for z in COMPONENTS if z in allowed_components
                and action_contrast(z, "volume", 0., dose_reference) is not None]
    if not eligible:
        report["summary"] = "Ръчните цели и текущите акценти не допускат самостоятелна промяна."
        return report
    burden_model = models["burden"]["posterior"]
    recovery_model = models["recovery"]["posterior"]
    zero = [1.] + [0.]*len(FEATURES)
    base_burden = predict(burden_model, zero)
    base_recovery = predict(recovery_model, zero)
    # Retain a proposal through its observation window. This is not evidence
    # that it was executed: only later actual Q and reports can establish that.
    previous = (retained or {}).get("decision") or {}
    if (previous.get("mode") == "CONTROL" and previous.get("expires_on", "") >= today.isoformat()
            and (previous.get("component") not in eligible or previous.get("config") != config)):
        report.update(status="SHADOW" if mode == "SHADOW" else "READY",
                      summary="Изчакваме наблюдението на предходната промяна; новият акцент не започва втори експеримент.")
        return report
    if (not previous and (retained or {}).get("cooldown_mode") == mode and
            (retained or {}).get("cooldown_until", "") >= today.isoformat()):
        report.update(status="SHADOW" if mode == "SHADOW" else "READY",
                      summary="Предходната пробна промяна е задържана; изчакваме края на наблюдението.")
        return report
    if (previous.get("mode") == mode and previous.get("config") == config and
            previous.get("effective_from", "9999") <= today.isoformat() <= previous.get("expires_on", "") and
            previous.get("component") in eligible):
        choice = deepcopy(previous)
        choice["retained"] = True
        if not choice.get("experimental"):
            model = models.get(choice.get("scope", "GLOBAL"), models["GLOBAL"])
            index = FEATURES.index(f"{choice['kind']}:{choice['component']}")+1
            contrast = action_contrast(choice["component"], choice["kind"], choice["step"], dose_reference, intensity_scale)
            if contrast is not None:
                gain = predict(model["posterior"], contrast, observation=False)
                burden = predict(models["burden"]["posterior"], contrast, observation=False)
                recovery = predict(models["recovery"]["posterior"], contrast, observation=False)
                supported = gain["low"]-.04*max(0., burden["high"])-.15*max(0., recovery["high"]) > .05
                supported = supported and _admissible(model, index, choice["step"], current,
                                                       burden, recovery, base_burden, base_recovery)
            else:
                supported = False
            if not supported:
                report.update(status="CAUTION", summary="Новите наблюдения вече не подкрепят предходната промяна; тя е задържана.")
                return report
            choice.update(gain=gain, burden=burden, recovery=recovery, validation=model["validation"])
    else:
        candidates = []
        maximum = min(.10, max(0., config.get("max_volume_step_percent", 5)/100))
        intensity_maximum = min(.05, max(0., config.get("max_intensity_step", .02)))
        for z in eligible:
            scoped = models.get(z, models["GLOBAL"])
            model = scoped if scoped["validation"]["status"] == "PASSED" else models["GLOBAL"]
            for kind, steps in (("volume", (-maximum, maximum)), ("intensity", (-intensity_maximum, intensity_maximum))):
                if z == "STR" and kind == "intensity":
                    continue
                scale = (intensity_scale or {}).get(z)
                if kind == "intensity" and (not _number(scale) or scale <= 0):
                    continue
                index = FEATURES.index(f"{kind}:{z}")+1
                count = _identified(model["samples"], index)
                for step in steps:
                    if not step:
                        continue
                    contrast = action_contrast(z, kind, step, dose_reference, intensity_scale)
                    if contrast is None:
                        continue
                    gain = predict(model["posterior"], contrast, observation=False)
                    burden = predict(burden_model, contrast, observation=False)
                    recovery = predict(recovery_model, contrast, observation=False)
                    effect = gain["low"] - .04*max(0., burden["high"]) - .15*max(0., recovery["high"])
                    # No increase without observed current recovery. Negative
                    # steps need efficacy too, not an assumption less is better.
                    admissible = _admissible(model, index, step, current, burden, recovery, base_burden, base_recovery)
                    candidates.append({"component": z, "kind": kind, "step": step, "utility": effect,
                                       "gain": gain, "burden": burden, "recovery": recovery, "count": count, "admissible": admissible,
                                       "validation": model["validation"], "scope": z if model is scoped else "GLOBAL", "experimental": False})
        report["candidate_count"] += len(candidates)
        positive = [c for c in candidates if c["admissible"] and c["utility"] > .05]
        choice = max(positive, key=lambda c: (c["utility"], c["component"], c["kind"])) if positive else None
        # A small explicitly marked probe acquires information. It is not sold
        # as a learned optimum; no failed validation or absent outcome permits it.
        recovered_outcomes = [e for e in episodes if (e.get("response") or {}).get("recovered") and e.get("outcomes") and not e.get("confounded")]
        if (choice is None and config.get("exploration_enabled", True) and maximum > 0 and len(recovered_outcomes) >= 2
                and current.get("recovered") and models["GLOBAL"]["validation"]["status"] != "FAILED"):
            # Explore dose first. Intensity requires a validated response; this
            # avoids changing volume, speed and shape in the same experiment.
            counts = {z: _identified(models["GLOBAL"]["samples"], FEATURES.index(f"volume:{z}")+1) for z in eligible}
            z = min(eligible, key=lambda z: (counts[z], list(COMPONENTS).index(z)))
            choice = {"component": z, "kind": "volume", "step": min(.025, maximum), "experimental": True,
                      "count": counts[z], "utility": None, "validation": models["GLOBAL"]["validation"]}
        if choice:
            choice.update(mode=mode, config=deepcopy(config), effective_from=today.isoformat(),
                          expires_on=(today+timedelta(days=13)).isoformat())
    if not choice:
        report.update(status="SHADOW" if mode == "SHADOW" else "READY",
                      summary="Моделът учи от резултатите; засега няма достатъчно основание да промени дозата.")
        return report
    z, step, kind = choice["component"], choice["step"], choice["kind"]
    # Fresh symptoms/missingness above always cancel retained experiments.
    if step > 0 and not current.get("recovered"):
        report.update(status="CAUTION", summary="Възстановяването още не е потвърдено; пробното увеличение е задържано.")
        return report
    c = components[z]
    experimental = choice.get("experimental", False)
    c.update(action="INCREASE" if step > 0 else "DECREASE", evidence_count=choice.get("count", 0),
             confidence="LOW" if experimental else "MEDIUM",
             reason="Малка пробна промяна само в този компонент; резултатът предстои да се проследи." if experimental else
                    "Съпоставимите наблюдения и проверката върху по-късни резултати подкрепят тази ограничена промяна.")
    if kind == "volume":
        c["proposed_volume_factor"] = round(1+step, 6)
        c["volume_factor"] = c["proposed_volume_factor"] if mode == "CONTROL" else 1.
    else:
        c["proposed_intensity_delta"] = round(step, 6)
        c["intensity_delta"] = c["proposed_intensity_delta"] if mode == "CONTROL" else 0.
    report.update(status="SHADOW" if mode == "SHADOW" else "EXPERIMENT" if experimental else "READY",
                  summary=("Предложение за наблюдение: " if mode == "SHADOW" else "Предложена промяна: ") + z +
                          (f" {step*100:+.1f}% обем." if kind == "volume" else f" {step*100:+.1f} п.п. в зоната."),
                  confidence=c["confidence"], effective_from=choice["effective_from"], expires_on=choice["expires_on"],
                  validation=choice["validation"], decision=choice)
    if choice.get("gain"):
        report["predictions"] = {"performance": choice["gain"], "burden": choice["burden"],
                                 "recovery": choice.get("recovery"), "recovery_unit": "days",
                                 "basis": "CHANGE_VS_UNCHANGED_DOSE", "performance_unit": "percent", "burden_unit": "stress_points"}
    identity = {k: choice.get(k) for k in ("component", "kind", "step", "mode", "config", "effective_from", "expires_on")}
    report["decision_id"] = sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return report
