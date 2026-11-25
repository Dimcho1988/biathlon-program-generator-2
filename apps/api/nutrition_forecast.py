"""Seven-day forecasts from the published schedule; never recorded expenditure.

The existing HR energy fit is applied to prescribed HR, then zone midpoints,
then pace/sport MET priors. These are explicit estimates, not measured signals.
"""
from . import energy_model as model
from .management_plan_store import PlanStore
from biathlon.planning_schedule import day_sessions
from biathlon.sport_heart_rate import reference_offset

HORIZON_DAYS = 7


def active_plan(repository, alias):
    # File/offline repositories can lack a published schedule store.
    if not callable(getattr(repository, "_json", None)):
        return None
    record = PlanStore(repository).current(alias, schedule_only=True)
    if not record or record["payload"].get("status") != "ACTIVE":
        return None
    plan = record["payload"].get("plan")
    return {"revision": record["revision"], "days": plan.get("days", [])} if plan else None


def _blocks(blocks, multiplier=1, depth=0):
    if depth > 8 or multiplier > 10000:
        raise ValueError("Invalid planned repetition structure")
    for block in blocks:
        if block.get("sequence") is not None:
            repeats = block.get("repeat_count", 1)
            if type(repeats) is not int or not 1 <= repeats <= 1000:
                raise ValueError("Invalid planned repetitions")
            yield from _blocks(block["sequence"], multiplier * repeats, depth + 1)
        else:
            seconds = block.get("duration_s")
            if seconds is None and model.number(block.get("duration_min")):
                seconds = block["duration_min"] * 60
            if not model.number(seconds) or seconds < 0:
                raise ValueError("Missing planned duration")
            if seconds:
                yield block, seconds * multiplier


def estimate(plan, day, profile, settings, resting_kcal, day_seconds=86400):
    """Return None for missing/invalid plans; only explicit REST means zero work."""
    if not plan or not model.number(resting_kcal) or resting_kcal <= 0 or not profile.get("weight_kg"):
        return None
    selected = next((d for d in plan["days"] if d.get("date") == str(day)), None)
    if selected is None:
        return None
    sessions = day_sessions(selected)
    if not sessions and selected.get("status") != "REST":
        return None
    weight, sex = profile["weight_kg"], profile.get("sex")
    resting_rate = resting_kcal / 1440
    summaries, total, duration = [], 0., 0.
    for session in sessions:
        sport, zone = session.get("sport"), session.get("zone")
        try:
            blocks = list(_blocks(session.get("blocks") or []))
        except (ValueError, TypeError, AttributeError):
            return None
        minutes = session.get("total_minutes")
        if not model.number(minutes) or not 0 < minutes <= 1440:
            return None
        seconds = sum(t for _, t in blocks)
        if blocks and abs(seconds - 60 * minutes) > 2:
            return None  # Never silently drop or double-count an unexplained duration.
        if not blocks:
            blocks = [({"zone": zone}, 60 * minutes)]
            seconds = 60 * minutes
        methods, kcal = set(), 0.
        strength = zone == "STR" or model.is_strength(sport)
        for block, dt in blocks:
            block_sport = block.get("load_sport") or sport
            hr = block.get("target_hr_bpm")
            method = "PLANNED_HR"
            # Strength MET is a session-average prior including its rests.
            if strength or block.get("zone") == "STR":
                rate, method = profile.get("strength_met", 3.5) * 3.5 * weight / 200, "STRENGTH_MET"
            elif block.get("passive_rest") is True:
                rate, method = resting_rate, "PASSIVE_REST"
            else:
                block_zone = block.get("zone") or zone
                if not model.number(hr) and block_zone in ("Z1", "Z2", "Z3", "Z4", "Z5"):
                    i = int(block_zone[-1]) - 1
                    bounds = settings.zone_bounds_bpm
                    hr = (bounds[i] + bounds[i+1]) / 2 - reference_offset(block_sport)
                    method = "ZONE_MIDPOINT"
                if model.number(hr) and hr > 0 and settings.hrmax_bpm and sex in model.COEFFICIENTS:
                    rate = model.hr_energy_rate(hr / settings.hrmax_bpm, sex, weight, resting_rate)
                else:
                    met = model.pace_met(block_sport, block.get("target_speed_kmh"))
                    method = "PACE_MET" if met is not None else "SPORT_MET"
                    rate = (met if met is not None else model.activity_met(block_sport)) * 3.5 * weight / 200
            kcal += max(resting_rate, rate) * dt / 60
            methods.add(method)
        total += kcal
        duration += seconds
        summaries.append({"title": session.get("title"), "sport": sport, "zone": zone,
                          "duration_min": seconds / 60, "kcal": kcal, "methods": sorted(methods)})
    if duration > day_seconds:
        return None
    outside = resting_kcal * (day_seconds - duration) / 86400 * profile.get("outside_factor", 1.2)
    return {"source": "PLANNED_TRAINING" if sessions else "PLANNED_REST",
            "plan_revision": plan["revision"], "activity_kcal": total,
            "outside_kcal": outside, "duration_s": duration, "day_seconds": day_seconds,
            "kcal": outside + total, "sessions": summaries,
            "assumption": "Планиран пулс/среда на зоната или резервна MET оценка. Покоят за времето на тренировките е включен само веднъж."}
