"""Execution ranges bounded by the already checked planning prescription."""
import math

VERSION = "execution-ranges-v1-upper-dose-budget"


def distance_range(time_seconds, speed_kmh):
    if (len(time_seconds) != 2 or len(speed_kmh) != 2
        or not all(math.isfinite(v) and v > 0 for v in (*time_seconds, *speed_kmh))
        or time_seconds[0] > time_seconds[1] or speed_kmh[0] > speed_kmh[1]):
        raise ValueError("Ordered positive time and speed ranges are required")
    return [time_seconds[0]*speed_kmh[0]/3.6, time_seconds[1]*speed_kmh[1]/3.6]


def annotate(session):
    evidence = session.get("dose_evidence") or {}
    works = [b for b in session["blocks"] if b["kind"] == "WORK"]
    total = sum(b["duration_min"] for b in works)
    # Mixed strength and technical structures retain their own exact recipe.
    if not total or any(b["zone"] in {"STR", "NMS"} for b in session["blocks"]):
        return
    minimum = evidence.get("minimum_structure_work_minutes", evidence.get("minimum_primary_work_minutes", 0.)) or 0.
    if all(b.get("race_specific") and b.get("duration_range_seconds") for b in works):
        # The selection minimum is quantized at whole upper-bound repetitions.
        # The same repetitions may use their explicitly approved lower times;
        # do not turn a legal three-repeat range back into three fixed times.
        # The independent continuous-capacity minimum below still applies.
        minimum = sum(b["duration_range_seconds"][0] for b in works)/60
    minimum_fraction = evidence.get("min_dose_fraction") or 0.
    applied = evidence.get("applied_minimum_capacity_fraction") or 0.
    floor = max(.9, min(1., minimum/total), min(1., minimum_fraction/applied) if applied else 0.)
    for block in session["blocks"]:
        seconds = block["duration_min"]*60
        if seconds <= 0:
            continue
        if block["kind"] == "WORK":
            supplied = block.get("duration_range_seconds")
            low = max(seconds*floor, supplied[0] if supplied else 0.)
            block["duration_range_seconds"] = [round(min(seconds, low), 3), round(seconds, 3)]
        else:
            # Active pauses are part of the checked load. Do not invent a shorter
            # recovery than the method prescribes or a longer unbudgeted pause.
            continue
        speed = block.get("target_speed_kmh")
        if not speed or not math.isfinite(speed) or speed <= 0:
            continue
        supplied = block.get("target_speed_range_kmh")
        low = max(speed*.95, supplied[0]) if supplied else speed*.95
        block["target_speed_range_kmh"] = [round(min(low, speed), 5), speed]
        block["target_pace_range_seconds_km"] = [3600/speed, 3600/block["target_speed_range_kmh"][0]]
        block["distance_range_m"] = distance_range(block["duration_range_seconds"], block["target_speed_range_kmh"])
        block["distance_basis"] = "FLAT_EQUIVALENT" if block.get("speed_basis") == "FLAT_EQUIVALENT" or block.get("primary_control") == "EFFORT_ON_TERRAIN" else "SPEED_REFERENCE"
    session["execution_range_policy"] = {"version": VERSION, "dose_basis": "UPPER_BOUND",
        "note": "Времето е водещо. Горните граници са проверената доза; дистанцията е ориентир. Минималната методна доза и предписаните паузи се запазват. При достигнат минимум времевият диапазон може да се стесни до една стойност."}
