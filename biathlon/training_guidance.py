"""Lactate references and explicit NMS exposure; neither invents measured load."""
VERSION = "lactate-nms-guidance-v1"
# Current OLT guidance deliberately has no population lactate ranges above I-3.
# These are contextual references, not a conversion of the athlete's HR zones.
GENERAL_LACTATE = {"Z1": (None, 1.5), "Z2": (1., 2.), "Z3": (1.5, 3.5)}
GENERAL_SOURCE = "https://olt-skala.nif.no/en"


def _interpolate(stages, hr):
    for a, b in zip(stages, stages[1:]):
        if a["hr_bpm"] <= hr <= b["hr_bpm"]:
            weight = (hr-a["hr_bpm"])/(b["hr_bpm"]-a["hr_bpm"])
            return round(a["lactate_mmol"] + weight*(b["lactate_mmol"]-a["lactate_mmol"]), 2)
    return None


def lactate_reference(profile, sport, zone, bounds, as_of):
    """Manual interpretation overrides test interpolation and population context.

    No extrapolation, threshold detection, normative clamp or sport transfer.
    Test interpolation is an estimate inside existing HR zones, not measured
    lactate for the planned session. Its date/protocol travel with the plan.
    """
    if zone not in {"Z1", "Z2", "Z3", "Z4", "Z5"} or not profile.get("lactate_guidance_enabled", True):
        return None
    personal = next((p for p in profile.get("lactate_profiles", []) if p["sport"] == sport
                     and p["assessed_on"] <= as_of.isoformat()), None)
    result = {"unit": "mmol/L", "zone": zone, "measured": False,
              "control_role": "CONTEXT", "source_url": None, "assessed_on": None,
              "low_mmol": None, "high_mmol": None}
    if personal:
        individual = {**result, "assessed_on": personal["assessed_on"], "protocol": personal.get("protocol", ""),
                      "device": personal.get("device", "")}
        if zone in personal.get("zone_ranges", {}):
            return {**individual, **personal["zone_ranges"][zone], "source": "INDIVIDUAL_TEST" if personal["source"] == "TEST" else "INDIVIDUAL_MANUAL",
                    "label": "Индивидуален ориентир", "control_role": "INDIVIDUAL_REFERENCE"}
        if personal["source"] == "TEST":
            index = int(zone[1])-1
            low, high = (_interpolate(personal["stages"], hr) for hr in bounds[index:index+2])
            # One-sided or inverted test coverage cannot establish a zone range.
            if low is not None and high is not None and low < high:
                return {**individual, "low_mmol": low, "high_mmol": high, "source": "TEST_INTERPOLATION",
                        "label": "Оценка по лактатен тест", "control_role": "TEST_ESTIMATE"}
    if zone in GENERAL_LACTATE:
        low, high = GENERAL_LACTATE[zone]
        return {**result, "low_mmol": low, "high_mmol": high, "source": "GENERAL_OLT_2024",
                "source_url": GENERAL_SOURCE, "source_zone": "I-"+zone[1],
                "label": "Общ ориентир · Olympiatoppen 2024",
                "note": "Популационен ориентир за съответното ниво на усилие; не калибрира личните пулсови зони."}
    return {**result, "source": "INDIVIDUAL_REQUIRED", "label": "Лактат според индивидуалния метод",
            "note": "За Z4/Z5 няма универсален числов ориентир в актуалната обща скала. Запиши измерванията и конкретния метод."}


def annotate_lactate(blocks, profile, sport, settings, day):
    bounds = list(settings.zone_bounds_bpm)
    if settings.hrmax_bpm:
        bounds[-1] = settings.hrmax_bpm
    for block in blocks:
        if block["kind"] == "WORK" and block["zone"] != "STR":
            ref = lactate_reference(profile, sport, block["zone"], bounds, day)
            if ref is not None:
                block["lactate_reference"] = ref
    return blocks


def lactate_comparisons(samples):
    results = []
    for sample in samples:
        low, high = sample.get("planned_low_mmol"), sample.get("planned_high_mmol")
        status, delta = "NOT_COMPARED", None
        if sample.get("comparison_confirmed") and sample.get("delay_seconds") is not None and (low is not None or high is not None):
            value = sample["value_mmol"]
            status, delta = ("BELOW", round(value-low, 2)) if low is not None and value < low else (
                ("ABOVE", round(value-high, 2)) if high is not None and value > high else ("WITHIN", 0.))
        results.append({**sample, "comparison": status, "difference_mmol": delta,
                        "reference_source": "USER_ENTERED_PLAN_REFERENCE", "automatic_load_weight": 0})
    return results


def annotate_double_threshold(blocks, profile):
    """A method ceiling, never a predicted concentration or universal LT."""
    if not profile.get("lactate_guidance_enabled", True):
        return blocks
    ceiling = (profile.get("planning_controls") or {}).get("double_threshold_lactate_ceiling", 3.5)
    for b in blocks:
        if b["kind"] != "WORK":
            continue
        ref = b.get("lactate_reference") or {}
        if ref.get("source") not in {"INDIVIDUAL_TEST", "INDIVIDUAL_MANUAL", "TEST_INTERPOLATION"}:
            b["lactate_reference"] = {"unit": "mmol/L", "zone": b["zone"], "low_mmol": None,
                "high_mmol": ceiling, "source": "COACH_DOUBLE_THRESHOLD_METHOD", "measured": False,
                "control_role": "METHOD_CEILING", "label": "Треньорски ориентир за двойния праг",
                "note": "Горен ориентир за контрол на усилието, не цел за достигане или индивидуален лактатен праг. Пробите са сравними само при един и същ протокол и момент на вземане."}
        b["instructions"] += " Лактатът е ориентир за намаляване на усилието, а не число, което трябва да достигнеш."
    return blocks


def neuromuscular_blocks(config, easy_hr):
    blocks = []
    sprint = config["mode"] == "SHORT_SPRINT"
    instructions = ("Кратък спринт след плавно набиране на скорост. Максимално качествено усилие; без загуба на техника." if sprint else
        "Плавно ускорявай; последните 2–3 секунди са с максимално качествено усилие и запазена техника.")
    instructions += " Използвай подходящ равен участък. Спри серията при болка или влошаване на техниката. Пулсът не е цел за тази отсечка."
    for i in range(config["repetitions"]):
        blocks.append({"kind": "NEUROMUSCULAR", "zone": "NMS", "label": f"{'Кратък спринт' if sprint else 'Ускорение'} {i+1}/{config['repetitions']}",
                       "duration_s": config["work_seconds"], "duration_min": config["work_seconds"]/60,
                       "target_hr_bpm": None, "target_speed_kmh": None, "repetition": i+1,
                       "primary_control": "TECHNIQUE_AND_SPEED", "instructions": instructions,
                       "metabolic_load_status": "NOT_MODELLED", "mechanical_load_status": "NOT_MODELLED"})
        # Full recovery after the final repetition precedes the remaining aerobic work.
        blocks.append({"kind": "RECOVERY", "zone": "Z1", "label": "Пълна почивка след ускорението",
                       "duration_s": config["recovery_seconds"], "duration_min": config["recovery_seconds"]/60,
                       "target_hr_bpm": easy_hr, "target_speed_kmh": None, "repetition": None,
                       "instructions": "Много леко движение. Запази цялата почивка; следващото ускорение е само при възстановени ритъм и техника."})
    return blocks


def nms_exposure(blocks):
    work = [b for b in blocks if b.get("zone") == "NMS"]
    if not work:
        return None
    return {"repetitions": len(work), "work_seconds": round(sum(b["duration_min"]*60 for b in work), 3),
            "metabolic_load_status": "NOT_MODELLED", "mechanical_load_status": "NOT_MODELLED",
            "note": "NMS е отчетено като време и повторения. Прогнозата Q/E и Recovery не оценява пълния товар на ускоренията; реалният пулсов отчет се запазва."}
