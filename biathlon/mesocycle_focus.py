"""Deterministic coaching policy, not a physiological readiness model.

Keep loading focus for a whole anchored mesocycle. Recovery support is a
relative priority within a smaller budget, never permission for a new block.
"""
from datetime import date, timedelta
from functools import lru_cache

from .constants import COMPONENTS, fresh_parameters
from .hr_speed import TMAX_RANGES_S

VERSION = "mesocycle-focus-v3-race-band"
PREPARATION = {"GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"}
RECOVERY_TOTAL_FACTOR = .78
RECOVERY_LOADED_FACTOR = .65
RECOVERY_SUPPORT_FACTOR = .90
REGULAR_INDICES = (1.6, 1.5, 1.2)
SHOCK_INDICES = (2., 1.8, 1.6)


def race_component(profile):
    """Classify against continuous Tmax at upper zone edges, never load Tref.

    Between the Z3 and Z4 upper-edge anchors lies Z4. Durations below the
    Z4 boundary map to Z5 without inventing a Z5 continuous capacity.
    """
    duration = profile.get("race_duration_min")
    if not duration:
        return None
    for z, limits in TMAX_RANGES_S.items():
        if duration >= sum(limits)/120:
            return z
    return "Z5"


def race_band(component):
    """Nearest three existing zones, ordered by the coach's dose priorities."""
    if component is None:
        return []
    n = int(component[1])
    if n == 1:
        return ["Z1", "Z2", "Z3"]
    if n == 5:
        return ["Z5", "Z4", "Z3"]
    return [component, f"Z{n-1}", f"Z{n+1}"]


def general_rotation(profile):
    """Cover enabled components before repeating, including with 1–2 roles.

    Disabling strength does not add an extra aerobic focus to the first triple.
    """
    groups = [["Z1", "Z3", "STR"], ["Z2", "Z4", "Z5"]]
    groups = [[z for z in group if z != "STR" or profile.get("strength_enabled")] for group in groups]
    count = profile["planning_controls"].get("automatic_focus_count", 3)
    if count == 3:
        return groups
    queue = [z for group in groups for z in group]
    return [queue[i:i+count] for i in range(0, len(queue), count)]


def ordered(profile, period, serial, fallback, *, race_specific=False):
    """Initial coach templates; duration bands are not metabolic measurements."""
    race = race_component(profile)
    n = int(race[1]) if race else 3
    below, above = f"Z{max(1, n-1)}", f"Z{min(5, n+1)}"
    automatic = profile["planning_controls"]["accent_mode"] == "AUTO"
    if period == "GENERAL_PREPARATION":
        templates = general_rotation(profile) if automatic else [["Z1", "Z3", "STR"], ["Z2", "Z3", "STR"], ["Z1", "Z2", "Z4"]]
    elif period == "SPECIAL_PREPARATION":
        templates = [race_band(race or "Z3")] if automatic and race_specific else [
                     [f"Z{n}", below if n > 1 else "Z2", "STR"],
                     ["Z5", "Z4", "Z3"] if n == 5 else [f"Z{n}", above, "STR"]]
    elif period == "PRECOMPETITION" and automatic:
        templates = [race_band(race or "Z3")]
    elif period in {"PRECOMPETITION", "COMPETITION"}:
        templates = [[f"Z{n}"]]
    elif period == "RE_ENTRY":
        templates = [list(COMPONENTS)]
    else:
        templates = [list(fallback)]
    return list(dict.fromkeys(z for z in templates[serial % len(templates)]
                             if z != "STR" or profile.get("strength_enabled")))


def _cycle_owner(start_key, end_key, phases):
    owner = next((p for p in phases if p[1] <= start_key <= p[2]), None)
    if owner and owner[0] in PREPARATION:
        return owner
    # Entry/transition can end inside an anchor. Its first preparation phase
    # owns the remainder even if another preparation phase starts before it ends.
    return next((p for p in sorted(phases, key=lambda p: p[1])
                 if p[0] in PREPARATION and p[1] <= end_key and p[2] >= start_key), None)


@lru_cache(maxsize=128)
def _phase_cycles(anchor_key, length, program_start, program_end, phase, phases, blocked):
    """Calendar-only candidates; never change a block in response to readiness.

    A fragment following entry may own the first preparation block. A block
    already owned by another preparation phase keeps that phase until its end.
    Cache keys contain only calendar configuration, no observed load/readiness.
    """
    anchor = date.fromisoformat(anchor_key)
    days = 7*length
    _, start_key, end_key = phase
    start, end = date.fromisoformat(start_key), date.fromisoformat(end_key)
    left, right = max(start, date.fromisoformat(program_start)), min(end, date.fromisoformat(program_end))
    if left > right:
        return ()
    first = (left-anchor).days//days
    first_start = anchor+timedelta(days=first*days)
    owner = _cycle_owner(first_start.isoformat(), (first_start+timedelta(days=days-1)).isoformat(), phases)
    if owner and owner[0] in PREPARATION and owner != phase:
        first += 1
    last = (right-anchor).days//days
    result = []
    for serial in range(first, last+1):
        cycle_start = anchor+timedelta(days=serial*days)
        day, until = max(left, cycle_start), min(right, cycle_start+timedelta(days=days-1))
        run = longest = 0
        while day <= until:
            eligible = ((day-anchor).days//7 % length != length-1 and
                        not any(a <= day.isoformat() <= b for a,b in blocked))
            run = run+1 if eligible else 0
            longest = max(longest, run)
            day += timedelta(days=1)
        if longest:
            result.append((serial, longest))
    return tuple(result)


def phase_cycles(profile, phase, periodization):
    controls = profile["planning_controls"]
    blocked = [(t["start_date"], t["end_date"]) for t in periodization.get("taper_windows", [])]
    blocked += [(e["start_date"], e["end_date"]) for e in periodization.get("calendar_context", []) if e["event_type"] == "UNAVAILABLE"]
    blocked += [(c["start_date"], (date.fromisoformat(c["end_date"])+timedelta(days=c.get("recovery_days", 0) if c["kind"] == "STRESS" else 0)).isoformat()) for c in controls["cycles"]]
    return _phase_cycles(controls.get("mesocycle_anchor") or profile["program_start"], len(controls["wave"]),
                         profile["program_start"], profile["program_end"],
                         (phase["kind"], phase["start_date"], phase["end_date"]),
                         tuple((p["kind"], p["start_date"], p["end_date"]) for p in periodization["phases"]),
                         tuple(sorted(blocked)))


def shock_schedule(profile, periodization):
    """One full loading week followed by a full unloading week per phase.

    Do not shift calendar anchors or overlap directives, races or taper.
    Short phases expose a reason instead of compressing recovery.
    """
    if not periodization or profile["planning_controls"]["accent_mode"] != "AUTO":
        return []
    controls = profile["planning_controls"]
    anchor = date.fromisoformat(controls.get("mesocycle_anchor") or profile["program_start"])
    length = len(controls["wave"])
    result = []
    for phase in periodization["phases"]:
        if phase["kind"] not in {"SPECIAL_PREPARATION", "PRECOMPETITION"}:
            continue
        left = max(date.fromisoformat(phase["start_date"]), date.fromisoformat(profile["program_start"]))
        right = min(date.fromisoformat(phase["end_date"]), date.fromisoformat(profile["program_end"]))
        explicit = [c for c in controls["cycles"] if c["kind"] == "STRESS"
                    and left <= date.fromisoformat(c["start_date"]) <= date.fromisoformat(c["end_date"]) <= right]
        item = {"period": phase["kind"], "phase_start": phase["start_date"], "phase_end": phase["end_date"]}
        if explicit:
            result.append({**item, "status": "MANUAL", "start_date": explicit[0]["start_date"], "end_date": explicit[0]["end_date"]})
            continue
        tapers = [(date.fromisoformat(t["start_date"]), date.fromisoformat(t["end_date"])) for t in periodization.get("taper_windows", [])]
        blocked = [(date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"])+timedelta(days=c.get("recovery_days", 0) if c["kind"] == "STRESS" else 0)) for c in controls["cycles"]]
        blocked += [(date.fromisoformat(e["start_date"]), date.fromisoformat(e["end_date"])) for e in periodization.get("calendar_context", []) if e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"}]
        candidates = []
        day = left
        while day + timedelta(days=13) <= right:
            serial = (day-anchor).days//7
            if (day-anchor).days % 7 == 0 and serial % length == length-2 and controls["wave"][length-2] >= 1:
                shock_end = day+timedelta(days=6)
                recovery_end = day+timedelta(days=13)
                # Recovery may coincide with taper, loading may not.
                if not any(a <= shock_end and b >= day for a,b in tapers) and not any(a <= recovery_end and b >= day for a,b in blocked):
                    candidates.append(day)
            day += timedelta(days=1)
        if candidates:
            start = candidates[-1]
            result.append({**item, "status": "PLANNED", "start_date": start.isoformat(), "end_date": (start+timedelta(days=6)).isoformat(),
                           "recovery_end": (start+timedelta(days=13)).isoformat()})
        else:
            result.append({**item, "status": "UNAVAILABLE", "reason": "Няма цяла натоварваща седмица с последващо разтоварване преди старта, свободна от календарни ограничения."})
    return result


def growth_weight(state, zone):
    """Third rank receives one third of the main annual progression rate."""
    indices = state.get("component_indices")
    if not indices:
        return float(zone in state.get("mesocycle_accents", state["accents"]))
    if zone in indices:
        return min(1., max(0., (indices[zone]-1)/.6))
    return 1/6 if state["focus_period"] == "GENERAL_PREPARATION" and zone != "STR" else 0.


def calendar_focus(profile, day, period, fallback, periodization=None):
    controls = profile["planning_controls"]
    anchor = date.fromisoformat(controls.get("mesocycle_anchor") or profile["program_start"])
    days = 7 * len(controls["wave"])
    serial = (day-anchor).days // days
    start = anchor + timedelta(days=serial*days)
    phase = period
    phase_start = anchor
    selected = None
    if periodization and period in PREPARATION:
        phase_keys = tuple((p["kind"], p["start_date"], p["end_date"]) for p in periodization["phases"])
        owner = _cycle_owner(start.isoformat(), (start+timedelta(days=days-1)).isoformat(), phase_keys)
        at_start = dict(zip(("kind", "start_date", "end_date"), owner)) if owner else None
        current = next((p for p in periodization["phases"] if p["start_date"] <= day.isoformat() <= p["end_date"]), None)
        selected = (current if period == "COMPETITION" and controls["accent_mode"] == "AUTO" else
                    at_start if at_start and at_start["kind"] in PREPARATION else current)
        if selected:
            phase, phase_start = selected["kind"], date.fromisoformat(selected["start_date"])
    first_cycle = max(0, ((phase_start-anchor).days + days-1)//days)
    position = max(0, serial-first_cycle)
    automatic = controls["accent_mode"] == "AUTO"
    candidates = phase_cycles(profile, selected, periodization) if selected and automatic else None
    stage = {"GENERAL_PREPARATION":"GENERAL_COVERAGE", "SPECIAL_PREPARATION":"SPECIAL_FOUNDATION",
             "PRECOMPETITION":"PRECOMPETITION_RACE_BAND", "COMPETITION":"COMPETITION"}.get(phase)
    specific = False
    metadata = {}
    if candidates is not None:
        serials = [n for n,_ in candidates]
        position = sum(n < serial for n in serials)
        metadata.update(phase_mesocycle_number=serials.index(serial)+1 if serial in serials else None,
                        phase_mesocycle_count=len(serials))
        if phase == "SPECIAL_PREPARATION" and candidates:
            complete_weeks = [n for n, run in candidates if run >= 7]
            terminal = (complete_weeks or serials)[-1]
            specific = serial >= terminal
            if specific:
                stage = "SPECIAL_RACE_BAND"
        if phase == "GENERAL_PREPARATION":
            rotation = general_rotation(profile)
            covered = {z for i in range(len(serials)) for z in rotation[i % len(rotation)]}
            metadata["focus_coverage_missing"] = [z for z in COMPONENTS if z not in covered and (z != "STR" or profile.get("strength_enabled"))]
    order = ordered(profile, phase, position, fallback, race_specific=specific)
    return order, {"mesocycle_id": start.isoformat(), "mesocycle_start": start.isoformat(),
                   "mesocycle_end": (start+timedelta(days=days-1)).isoformat(),
                   "focus_period": phase, "focus_version": VERSION,
                   "focus_stage": stage if automatic else None, "race_band": race_band(race_component(profile)), **metadata,
                   "race_component": race_component(profile), "race_component_basis": "EXPERT_CONTINUOUS_UPPER_EDGE_MIDPOINTS",
                   "race_reference_minutes": {z: sum(v)/120 for z,v in TMAX_RANGES_S.items()},
                   "shock_schedule": shock_schedule(profile, periodization)}


def recovery_support(state, profile, rows, today, *, limited=False, taper=False, readiness=None):
    """Use only observed 7/40; future support remains explicitly conditional."""
    if state is None or state["kind"] != "RECOVERY":
        return state
    state = dict(state)
    loaded = state["mesocycle_accents"]
    candidates = []
    if not limited and not taper and profile["planning_controls"]["accent_mode"] != "MANUAL":
        for z in state["support_candidates"]:
            if readiness is not None and (readiness.get(z) is None or readiness[z] < 90):
                continue
            recent = [r for r in rows if r["zone"] == z and
                      (today-timedelta(days=40)).isoformat() <= r["date"] < today.isoformat()]
            week = [r for r in recent if r["date"] >= (today-timedelta(days=7)).isoformat()]
            mean = sum(r["effective_load"] for r in recent)/len(recent) if recent else 0.
            short = sum(r["effective_load"] for r in week)/7
            if len(recent) >= 10 and len({r["date"] for r in week}) == 7 and mean > 0 and short < mean:
                history = [r["effective_load"] for r in rows if r["zone"] == z and
                           (today-timedelta(days=50)).isoformat() <= r["date"] < today.isoformat()]
                base = max(fresh_parameters()["base_loads"][z], .5*sum(history)/len(history))
                candidates.append(((base+short)/(base+mean), z))
    candidates.sort(key=lambda pair: (pair[0], state["support_candidates"].index(pair[1])))
    state.update(accents=[z for _, z in candidates[:1]], recovering_components=list(loaded),
                 focus_role="RECOVERY_SUPPORT", support_basis="OBSERVED_7_40_BELOW_MAINTENANCE",
                 support_as_of=today.isoformat(), support_requires_daily_readiness=True,
                 reason="Разтоварване на водещите компоненти; най-много един по-слабо натоварен компонент с поддържаща доза. Общият товар остава намален.")
    return state


def cap_recovery(goals, profile, state, actual_base):
    """Final cap after progression/manual targets, in canonical E including spill.

    Aerobic E is capped as a sum; STR retains a separate cap. The policy does
    not promise an immediate fall in observed 7/40, nor assume independent zones.
    """
    if state is None or state["kind"] != "RECOVERY":
        return goals
    total_factor = min(RECOVERY_TOTAL_FACTOR, state["volume_factor"])
    for z, goal in goals.items():
        factor = (RECOVERY_SUPPORT_FACTOR if z in state["accents"] else
                  RECOVERY_LOADED_FACTOR if z in state["mesocycle_accents"] else total_factor)
        factor = min(factor, total_factor) if z not in state["accents"] or total_factor < RECOVERY_TOTAL_FACTOR else factor
        base = actual_base[z]
        ceiling = 7*base["c40"]*factor
        goal["target"] = min(goal["target"], ceiling)
        goal["recovery_ceiling_effective"] = ceiling
        goal["development"] = False
    aerobic = [z for z in COMPONENTS if z != "STR"]
    ceiling = total_factor * sum(7*actual_base[z]["c40"] for z in aerobic)
    total = sum(goals[z]["target"] for z in aerobic)
    scale = min(1., ceiling/total) if total > 0 else 1.
    for z, goal in goals.items():
        if z != "STR":
            goal["target"] *= scale
        base = actual_base[z]
        goal.update(factor=goal["target"]/max(1e-9, goal["reference"]),
                    target_index=(base["b50"]+goal["target"]/7)/(base["b50"]+base["c40"]),
                    recovery_total_factor=total_factor, recovery_policy=VERSION)
    return goals
