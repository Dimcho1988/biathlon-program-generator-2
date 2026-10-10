"""Versioned coach policies, not population limits or validated physiology.

These transformations preserve the capacity source and the canonical Q/E
accounting. Recovery remains a daily model; no intraday exponent is inferred.
"""
from copy import deepcopy

VERSION = "adaptive-methods-v3-method-role-readiness"


def developmental(profile):
    return (profile.get("age_years") is None or profile["age_years"] < 18
            or profile.get("training_experience_years") is None
            or profile["training_experience_years"] < 1)


def short_variant(method):
    """An introductory dose at the same supported effort, with longer rests."""
    result = deepcopy(method)
    key = "interval_template" if method["structure"] == "MODEL_INTERVALS" else "interval_profile"
    p = result[key]
    work = min(p["work_seconds"], 30 if p["zone"] == "Z4" else 15)
    if p.get("speed_time_duration_ratio") is not None:
        p["speed_time_duration_ratio"] *= p["work_seconds"] / work
    p.update(work_seconds=work, recovery_seconds=max(p["recovery_seconds"], 3*work),
             min_repetitions=4, max_repetitions=12, reserve_repetitions=max(2, p["reserve_repetitions"]))
    result.update(id=method["id"]+"-SHORT", title=method["title"]+" · кратък въвеждащ вариант",
                  min_work_min=4*work/60, max_work_min=12*work/60,
                  developmental_variant=True, minimum_fraction=.05)
    result["instructions"] = ("Контролирани кратки повторения, без отказ; поне 2 в резерв. Пълна почивка. "
        "Спри при болка или загуба на техника. Не гони пулса. "
        "Кратките отсечки не гарантират нисък лактат.")
    result["adaptation"] = "Възрастта, опитът или малката експозиция променят продължителността и почивката; капацитетът и бюджетите се проверяват отделно. Начално треньорско правило."
    return result


def mixed_variants(methods):
    """Use reviewed executable parents, never arbitrary numeric library examples."""
    result = []
    for m in methods:
        if m["zone"] not in {"Z2", "Z3", "Z4", "Z5"}:
            continue
        if m["structure"] not in {"CONTINUOUS", "THRESHOLD_REPETITIONS", "MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
            continue
        if m["purpose"] not in {"BUILDING", "MAINTENANCE"}:
            continue
        for variant in ("STEADY", "REPETITIONS") if m["zone"] in {"Z2", "Z3"} else ("REPETITIONS",):
            child = deepcopy(m)
            child.update(id=m["id"]+"-MIX-"+variant, title=f"Лека аеробна работа + {m['zone']} · "+("равномерна част" if variant == "STEADY" else "отсечки"),
                         purpose="SUPPORTING", mixed_component=True, mixed_variant=variant,
                         warmup_min=15., cooldown_min=5., minimum_fraction=.05,
                         periods=tuple(p for p in m["periods"] if p not in {"RE_ENTRY", "TRANSITION"}))
            if m["zone"] in {"Z2", "Z3"}:
                child.update(structure="MIXED_AEROBIC", min_work_min=2., max_work_min=20.)
            else:
                child = short_variant(child)
                key = "interval_template" if child["structure"] == "MODEL_INTERVALS" else "interval_profile"
                # A supplement is embedded in a full easy session, so two
                # quality repetitions can be complete without being a new workout.
                child[key]["min_repetitions"] = 2
                child["min_work_min"] = 2*child[key]["work_seconds"]/60
            child["adaptation"] = "Допълващ блок от оставащия Q/7–40 бюджет, след загрявка и упражнения, преди довършване на леката работа. Намалена доза според готовността; не е втора пълна развиваща сесия."
            result.append(child)
    return result


def threshold_pairs(candidates, zones):
    pairs = []
    for sport in dict.fromkeys(s for s, _ in candidates):
        bases = {}
        for z in zones:
            bases[z] = next((m for s, m in candidates if s == sport and m["zone"] == z
                and not m.get("mixed_component") and not m.get("developmental_variant")
                and (z == "Z3" and m["structure"] == "THRESHOLD_REPETITIONS"
                     or z == "Z4" and m.get("interval_profile", {}).get("goal") == "THRESHOLD")), None)
        if any(m is None for m in bases.values()):
            continue
        parts = []
        for i, z in enumerate((zones[0], zones[-1])):
            base = bases[z]
            part = {**deepcopy(base), "id": base["id"]+("-DT-LONG" if i == 0 else "-DT-SHORT"),
                    "structure": "THRESHOLD_LONG" if i == 0 else "THRESHOLD_SHORT",
                    "capacity_method": deepcopy(base), "warmup_min": 15., "cooldown_min": 10.,
                    "min_work_min": 12. if i == 0 else 6., "max_work_min": 90.,
                    "recovery_min": 1. if i == 0 else .5,
                    "instructions": "Контролирана прагова работа с резерв. Следи лактата при сравним протокол; намали усилието или прекрати при нарастване над личния ориентир, болка или загуба на техника. Пулсът не е цел за кратките отсечки."}
            parts.append(part)
        first, second = parts
        pairs.append((sport, {**first, "id": first["id"]+"-DOUBLE-"+second["id"],
            "title": "Двоен праг · дълги + кратки интервали", "double_threshold": True,
            "paired_method": second, "single_min_work_min": first["min_work_min"],
            "min_work_min": 2*first["min_work_min"], "max_work_min": 2*first["max_work_min"]}))
    return pairs


def readiness_policy(method, profile, readiness):
    z = method["zone"]
    required = {z}
    required.update((method.get("dose_evidence", {}).get("readiness_policy", {}).get("required_components") or {}).keys())
    if "Z1" in readiness:
        required.add("Z1")
    if method.get("paired_method"):
        required.add(method["paired_method"]["zone"])
    if method.get("structure") == "THRESHOLD_HIGH":
        required.add(method["interval_profile"]["zone"])
    if method.get("structure") == "AEROBIC_STRENGTH":
        required.add("STR")
    observed = {component: readiness.get(component) for component in sorted(required)}
    # One common scale preserves the approved combination. Unknown Recovery is
    # never replaced with full readiness, and percentages are not compounded.
    factor = (min(min(100., max(0., value)) for value in observed.values()) / 100
              if all(value is not None for value in observed.values()) else 0.)
    return {"version": VERSION, "component": z, "minimum_percent": 0.,
            "observed_percent": readiness.get(z), "required_components": observed,
            "mixed_component": bool(method.get("mixed_component")),
            "dose_factor": factor, "diagnostic_reference_percent": 90.,
            "policy": "PROPORTIONAL_DURATION_AT_SUPPORTED_EFFORT",
            "intraday_recheck": False if method.get("double_threshold") else None,
            "basis": "COACH_POLICY_NOT_PHYSIOLOGICAL_THRESHOLD"}
