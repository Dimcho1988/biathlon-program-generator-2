"""Exploratory stage-derived model; protein priors are not individual measurements.
Clauss & Jensen 2025 doi:10.1111/sms.70038; Witard et al. 2025 doi:10.1007/s40279-025-02203-8.
"""

from math import isfinite
from biathlon.unified_load import integrate

VERSION = "energy-constrained-cubic-v1"
CALCULATION_VERSION = "energy-accounting-v3-complete-activity"
COEFFICIENTS = {
    "MALE": (
        0.5966452238309221,
        0.3559872899390167,
        0.06930439199917234,
        -0.02193690576911104,
    ),
    "FEMALE": (
        0.3418807794179927,
        0.28642903908268263,
        0.24247127488145717,
        0.09579414397959929,
    ),
}
MACROS = ("carbohydrate", "fat", "protein")
FACTORS = {"carbohydrate": 4.0, "fat": 9.0, "protein": 4.0}


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and isfinite(x)


def cho_fraction(hr_fraction, sex):
    if (
        sex not in COEFFICIENTS
        or not number(hr_fraction)
        or not 0.5 <= hr_fraction <= 1
    ):
        return None
    z = (100 * hr_fraction - 80) / 20
    a, b, c, d = COEFFICIENTS[sex]
    return min(1.0, max(0.0, a + z * (b + z * (c + z * d))))


def empty():
    return {"kcal": 0.0, **{m + "_kcal": 0.0 for m in MACROS}}


def add(total, other):
    for k in empty():
        total[k] += other[k]


def grams(total):
    return {**total, **{m + "_g": total[m + "_kcal"] / FACTORS[m] for m in MACROS}}


def allocate(kcal, cho, protein_kcal):
    protein = min(kcal, max(0.0, protein_kcal))
    rest = kcal - protein
    return {
        "kcal": kcal,
        "carbohydrate_kcal": rest * cho,
        "fat_kcal": rest * (1 - cho),
        "protein_kcal": protein,
    }


def activity_met(sport):
    """Typical session priors, not measurements of this person's intensity.

    2024 Adult Compendium: jogging 12020, cycling 01014, cross-country
    skiing 19090 (also a proxy for roller skiing), walking 17190, hiking
    17080, recreational swimming 18310. Other sports: explicit app prior.
    """
    from vflat_b65.sports import is_running, is_nordic
    from biathlon.sport_heart_rate import is_cycling, sport_key

    key = sport_key(sport)
    if is_running(sport):
        return 7.5
    if is_nordic(sport):
        return 8.5
    if is_cycling(sport):
        return 7.0
    if key in {"walk", "walking"}:
        return 3.8
    if key in {"hike", "hiking", "swim", "swimming", "openwaterswim"}:
        return 6.0
    return 3.5


def pace_met(sport, speed):
    """Coarse speed bands, used only without an individual speed/HR estimate.

    Compendium level running/walking/cycling bands; applying these to hills,
    wind or missing grade is an explicit fallback, never a calibrated value.
    """
    from vflat_b65.sports import is_running
    from biathlon.sport_heart_rate import is_cycling, sport_key

    if not number(speed) or speed <= 0:
        return None
    if is_running(sport):
        bands = [(6.4, 3.3), (6.9, 6.5), (8.0, 7.8), (8.8, 8.5),
                 (9.7, 9.0), (10.8, 9.3), (11.3, 10.5), (12.1, 11.0),
                 (12.9, 11.8), (13.8, 12.0), (14.5, 12.5), (15.0, 13.0),
                 (17.7, 14.8), (19.3, 16.8), (20.9, 18.5), (22.5, 19.8)]
        return next((met for edge, met in bands if speed < edge), 23.0)
    if is_cycling(sport):
        bands = [(16.1, 4.0), (19.3, 6.8), (22.5, 8.0), (25.7, 10.0), (32.2, 12.0)]
        return next((met for edge, met in bands if speed < edge), 16.8)
    if sport_key(sport) in {"walk", "walking", "hike", "hiking"}:
        bands = [(3.2, 2.3), (4.0, 2.8), (4.5, 3.0), (5.6, 3.8), (6.4, 4.8), (7.2, 5.5)]
        return next((met for edge, met in bands if speed < edge), 7.0)
    return None


def activity(shadow, *, sex, weight, hrmax, bounds, sport, parameters, duration_s,
             resting_kcal=None, fallback_cho=0.5):
    from biathlon.sport_heart_rate import reference_offset, sport_key
    from vflat_b65.sports import is_running, is_nordic, is_treadmill

    total = empty()
    bins = {}
    sources = {k: 0.0 for k in ("measured_hr", "relative_intensity", "low_intensity",
        "bounded_hr", "rest", "pause_rest", "pace_met", "sport_met", "unknown")}
    fallback_reasons = {"missing_signal": 0.0, "outside_hr_range": 0.0}
    # Entered/anthropometric resting rate preferred; 1 MET is the last resort.
    resting_rate = resting_kcal / 1440 if number(resting_kcal) and resting_kcal > 0 else 3.5 * weight / 200
    typical_rate = activity_met(sport) * 3.5 * weight / 200
    stationary_is_rest = ((is_running(sport) and not is_treadmill(sport)) or is_nordic(sport)
                          or sport_key(sport) in {"walk", "walking", "hike", "hiking"})

    def regression_rate(fraction):
        return weight * (-0.09354289849186385 + 0.3984908361869844 * fraction
                         - 0.04590881586125497 * (sex == "FEMALE"))

    def estimate(h, s, paused, dt):
        hr = h.get("hr_bpm") if h else None
        fraction = hr / hrmax if number(hr) and number(hrmax) and hrmax > 0 else None
        source = "measured_hr"
        if fraction is None and s and s.get("status") == "AVAILABLE" and number(hrmax) and hrmax > 0:
            i = ("Z1", "Z2", "Z3", "Z4", "Z5").index(s["zone"])
            p = s.get("relative_position")
            if number(p) and 0 <= p <= 1:
                fraction = (bounds[i] + p * (bounds[i + 1] - bounds[i]) - reference_offset(sport)) / hrmax
                source = "relative_intensity"
        if fraction is None and s and s.get("status") == "REST" and number(hrmax) and hrmax > 0:
            speed = s.get("_speed_kmh")
            first = next(iter((parameters or {}).get("zones") or []), {})
            floor = first.get("low_kmh")
            if number(speed) and speed > 0 and number(floor) and floor > 0:
                edge = max(.3, (bounds[0] - reference_offset(sport)) / hrmax)
                fraction = .3 + min(1.0, speed / floor) * (edge - .3)
                source = "low_intensity"
        if fraction is not None and sex in COEFFICIENTS:
            if 0.5 <= fraction <= 1:
                return regression_rate(fraction), cho_fraction(fraction, sex), source
            fallback_reasons["outside_hr_range"] += dt
            if fraction > 1:
                return regression_rate(1), cho_fraction(1, sex), "bounded_hr"
            # Working bridge from resting at <=30% HRmax to the 50% boundary.
            # It covers low effort without claiming the exercise fit is validated there.
            alpha = min(1.0, max(0.0, (fraction - 0.3) / 0.2))
            rate = resting_rate + alpha * (max(resting_rate, regression_rate(.5)) - resting_rate)
            cho = fallback_cho + alpha * (cho_fraction(.5, sex) - fallback_cho)
            return rate, cho, "low_intensity"
        if paused:
            return resting_rate, fallback_cho, "pause_rest"
        raw_speed = (s or {}).get("_raw_speed_kmh")
        model_speed = (s or {}).get("_speed_kmh")
        if stationary_is_rest and raw_speed == 0:
            return resting_rate, fallback_cho, "rest"
        if stationary_is_rest and s and s.get("status") == "REST" and model_speed == 0:
            return resting_rate, fallback_cho, "rest"
        # Low but nonzero speed is movement, even when below the training-zone floor.
        met = pace_met(sport, raw_speed if number(raw_speed) else model_speed)
        if met is not None:
            return met * 3.5 * weight / 200, fallback_cho, "pace_met"
        fallback_reasons["missing_signal"] += dt
        return typical_rate, fallback_cho, "sport_met"

    def observe(left, right, h, s, paused):
        dt = right - left
        rate, cho, source = estimate(h, s, paused, dt)
        sources[source] += dt
        kcal = max(resting_rate, rate) * dt / 60
        protein = (0.00081 * weight * dt / 60 * 4 if source in {"rest", "pause_rest"}
                   else kcal * 0.0328)
        part = allocate(kcal, cho, protein)
        add(total, part)
        # Display aggregation only; original intervals drive all integration.
        cursor = left
        while cursor < right:
            i = int(cursor // 300)
            end = min(right, (i + 1) * 300)
            b = bins.setdefault(i, {"start_s": i * 300, "seconds": 0.0, **empty()})
            b["seconds"] += end - cursor
            add(b, {k: v * (end - cursor) / dt for k, v in part.items()})
            cursor = end

    ledger = integrate(
        shadow,
        bounds=bounds,
        sport=sport,
        parameters=parameters,
        leading_indicator="HEART_RATE",
        duration_s=duration_s,
        hr_signal="hr_raw_bpm",
        signal_observer=observe,
    )
    known = sources["measured_hr"] + sources["relative_intensity"]
    estimated = sum(v for k, v in sources.items() if k not in {"measured_hr", "relative_intensity", "unknown"})
    return {
        **grams(total),
        "kcal": total["kcal"] if known + estimated > 0 else None,
        "covered_seconds": known,
        "accounted_seconds": known + estimated,
        "estimated_seconds": estimated,
        "duration_s": duration_s,
        "unknown_seconds": max(0.0, duration_s - known - estimated),
        "source_seconds": sources,
        "uncovered_reasons_seconds": {"missing_signal": 0.0, "outside_hr_range": 0.0},
        "fallback_reasons_seconds": fallback_reasons,
        "fallback_met": activity_met(sport),
        "fallback_carbohydrate_fraction": fallback_cho,
        "method": "HR_OR_SPEED_REGRESSION",
        "coverage_percent": min(100.0, 100 * known / duration_s) if duration_s else 0.0,
        "timeline": [grams(b) for _, b in sorted(bins.items())],
        "paused_seconds": ledger["paused_seconds"],
        "speed_initial_heuristic": bool((parameters or {}).get("initial_heuristic")),
    }


def is_strength(sport):
    from biathlon.sport_heart_rate import sport_key

    return sport_key(sport) in {"weighttraining", "strengthtraining", "strength", "weights"}


def strength_activity(*, weight, duration_s, met=3.5, cho=0.5, resting_kcal=None):
    """Gross session estimate, including rests; no endurance HR regression.

    2024 Adult Compendium, code 02054: 3.5 MET. Standard oxygen equivalent
    (3.5 ml/kg/min, 5 kcal/l); a session average, not measured set intensity.
    Substrates use explicit profile assumptions, not the Compendium.
    """
    kcal = met * 3.5 * weight / 200 * duration_s / 60
    part = allocate(kcal, cho, kcal * 0.0328)
    timeline = []
    cursor = 0.0
    while cursor < duration_s:
        seconds = min(300.0, duration_s - cursor)
        timeline.append({"start_s": cursor, "seconds": seconds,
                         **grams({k: v * seconds / duration_s for k, v in part.items()})})
        cursor += seconds
    return {
        **grams(part),
        "method": "STRENGTH_MET",
        "met": met,
        "carbohydrate_fraction_assumption": cho,
        "protein_fraction_assumption": 0.0328,
        "above_rest_kcal": (kcal - resting_kcal * duration_s / 86400
                            if resting_kcal is not None else None),
        "duration_s": duration_s,
        "covered_seconds": 0.0,
        "accounted_seconds": duration_s,
        "estimated_seconds": duration_s,
        "unknown_seconds": 0.0,
        "coverage_percent": 0.0,
        "source_seconds": {"measured_hr": 0.0, "relative_intensity": 0.0,
                           "strength_met": duration_s, "unknown": 0.0},
        "timeline": timeline,
    }


def outside_energy(seconds, weight, resting_kcal, factor, cho):
    # Factor includes ordinary movement/TEF; do not add steps/device kcal again.
    kcal = resting_kcal * seconds / 86400 * factor
    return grams(allocate(kcal, cho, 0.00081 * weight * seconds / 60 * 4))


def dietary_targets(total, weight, training_hours, adult, cho_fraction=0.65):
    """App policy, not a validated physiological kcal-to-protein regression.

    Retain training_hours for older callers; the policy deliberately ignores it.
    Partial expenditure must never determine the full-day protein coefficient.
    """
    if not adult or not number(weight) or weight <= 0:
        return None
    complete = number(total) and total > 0
    per_kg = total / weight if complete else None
    coefficient = min(1.9, max(1.6, 1.6 + 0.0075 * (per_kg - 40))) if complete else 1.8
    protein = weight * coefficient
    full = complete and total > protein * 4
    fraction = min(1.0, max(0.0, cho_fraction))
    remaining = total - protein * 4 if full else None
    return {
        "kcal": total if full else None,
        "protein_g": protein,
        "protein_g_per_kg": coefficient,
        "energy_kcal_per_kg": per_kg,
        "protein_basis": "DAILY_ENERGY_RULE" if complete else "INCOMPLETE_DAY_FALLBACK",
        "carbohydrate_g": remaining * fraction / 4 if full else None,
        "fat_g": remaining * (1 - fraction) / 9 if full else None,
        "purpose": "MAINTENANCE_STARTING_ESTIMATE",
        "allocation_note": (
            "Работно правило: 1,6–1,9 g/kg при 40–80 kcal/kg общ дневен разход, с ограничение до тези граници. "
            "Останалата енергия следва оцененото съотношение въглехидрати/мазнини. "
            "Това е ориентир за поддържане, не индивидуално валидирана хранителна норма."
            if complete else
            "Непълен ден: резервна цел 1,8 g/kg. Калориите, въглехидратите и мазнините за целия ден още не са определени."
        ),
    }
