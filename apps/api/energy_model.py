"""Exploratory stage-derived model; protein priors are not individual measurements.
Clauss & Jensen 2025 doi:10.1111/sms.70038; Witard et al. 2025 doi:10.1007/s40279-025-02203-8.
"""

from math import isfinite
from biathlon.unified_load import integrate

VERSION = "energy-constrained-cubic-v1"
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

    def observe(left, right, chosen):
        dt = right - left
        if not chosen:
            sources["unknown"] += dt
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
        "covered_seconds": known,
        "duration_s": duration_s,
        "unknown_seconds": max(0.0, duration_s - known),
        "source_seconds": sources,
        "coverage_percent": min(100.0, 100 * known / duration_s) if duration_s else 0.0,
        "timeline": [grams(b) for _, b in sorted(bins.items())],
        "paused_seconds": ledger["paused_seconds"],
        "speed_initial_heuristic": bool((parameters or {}).get("initial_heuristic")),
    }


def outside_energy(seconds, weight, resting_kcal, factor, cho):
    # Factor includes ordinary movement/TEF; do not add steps/device kcal again.
    kcal = resting_kcal * seconds / 86400 * factor
    return grams(allocate(kcal, cho, 0.00081 * weight * seconds / 60 * 4))


def dietary_targets(total, weight, training_hours, adult):
    if not adult or total is None or total <= 0 or not number(weight) or weight <= 0:
        return None
    protein = weight * (2.0 if training_hours < 0.25 else 1.8)
    if total <= protein * 4:
        return None
    carbohydrate = (total - 4 * protein) * 0.65 / 4
    fat = (total - 4 * protein - 4 * carbohydrate) / 9
    return {
        "kcal": total,
        "protein_g": protein,
        "carbohydrate_g": carbohydrate,
        "fat_g": fat,
        "purpose": "MAINTENANCE_STARTING_ESTIMATE",
        "protein_reference": "Witard, Hearris & Morgan 2025; doi:10.1007/s40279-025-02203-8",
        "allocation_note": "Ориентир за поддържане: белтъчини 1,8 g/kg в тренировъчен и 2,0 g/kg в почивен ден; 65% от останалата енергия за въглехидрати. Последното е работно допускане за индивидуален преглед, а не валидирана оптимална диета.",
    }
