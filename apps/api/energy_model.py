"""Exploratory stage-derived model; protein priors are not individual measurements.
Clauss & Jensen 2025 doi:10.1111/sms.70038; Witard et al. 2025 doi:10.1007/s40279-025-02203-8.
"""

from math import isfinite
from biathlon.unified_load import integrate

VERSION = "energy-constrained-cubic-v1"
CALCULATION_VERSION = "energy-accounting-v2-strength-met"
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


def activity(shadow, *, sex, weight, hrmax, bounds, sport, parameters, duration_s):
    total = empty()
    bins = {}
    sources = {"measured_hr": 0.0, "relative_intensity": 0.0, "unknown": 0.0}
    uncovered = {"missing_signal": 0.0, "outside_hr_range": 0.0}

    def observe(left, right, chosen):
        dt = right - left
        if not chosen:
            sources["unknown"] += dt
            uncovered["missing_signal"] += dt
            return
        kind, value = chosen
        hr = value.get("hr_bpm") if kind == "HEART_RATE" else None
        if kind == "SPEED" and value.get("status") == "AVAILABLE":
            i = ("Z1", "Z2", "Z3", "Z4", "Z5").index(value["zone"])
            p = value.get("relative_position")
            if number(p) and 0 <= p <= 1:
                from biathlon.sport_heart_rate import reference_offset

                hr = (
                    bounds[i]
                    + p * (bounds[i + 1] - bounds[i])
                    - reference_offset(sport)
                )
        fraction = hr / hrmax if number(hr) and number(hrmax) and hrmax > 0 else None
        cho = cho_fraction(fraction, sex)
        if cho is None:
            sources["unknown"] += dt
            uncovered["outside_hr_range" if number(fraction) else "missing_signal"] += dt
            return
        rate = weight * (
            -0.09354289849186385
            + 0.3984908361869844 * fraction
            - 0.04590881586125497 * (sex == "FEMALE")
        )
        if rate <= 0:
            sources["unknown"] += dt
            return
        sources["measured_hr" if kind == "HEART_RATE" else "relative_intensity"] += dt
        part = allocate(rate * dt / 60, cho, rate * dt / 60 * 0.0328)
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
        segment_observer=observe,
    )
    known = sources["measured_hr"] + sources["relative_intensity"]
    return {
        **grams(total),
        "kcal": total["kcal"] if known > 0 else None,
        "covered_seconds": known,
        "accounted_seconds": known,
        "estimated_seconds": 0.0,
        "duration_s": duration_s,
        "unknown_seconds": max(0.0, duration_s - known),
        "source_seconds": sources,
        "uncovered_reasons_seconds": uncovered,
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
