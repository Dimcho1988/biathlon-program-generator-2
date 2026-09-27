"""One pinned, bounded learning pass shared by the planner and its outlook.

No writes and no raw activity recomputation here. Cumulative evidence travels
with existing immutable plan/draft revisions; the existing worker publishes
only through its approval and optimistic-concurrency lifecycle.
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from statistics import median

from biathlon import individual_learning, load_progression, planning_controls
from biathlon.constants import COMPONENTS
from biathlon.equivalence import EQUIVALENCE_VERSION, equivalence_slope

from . import learning_evidence
from .management_store import ManagementStore
from .oauth_store import PersistentStoreFailure
from .response_service import ResponseStore
from .response_monitoring import build_history, VERSION as STRESS_VERSION
from .trainability import MODEL_VERSION as TI_VERSION
from .trainability_history import history_from_calendar
from .stress_model import CHANNELS

VERSION = individual_learning.VERSION
DEFAULTS = {"mode": "SHADOW", "exploration_enabled": True,
            "max_volume_step_percent": 5., "max_intensity_step": .02}
EPISODE_MEMORY_BYTES = 64*1024


def enabled(profile):
    progression = load_progression.settings(profile)
    return bool(progression and progression.get("feedback_enabled") and
                (profile.get("individual_learning") or DEFAULTS).get("mode", "SHADOW") != "OFF")


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()).hexdigest()


def bounded_episodes(episodes, budget=EPISODE_MEMORY_BYTES):
    """Newest complete observations in a deterministic bounded replay window.

    Older immutable plan revisions retain their historical audit evidence. The
    current fitting set and saved replay set are exactly the same.
    """
    selected, used = [], 2
    for episode in sorted(episodes, key=lambda e: (e["end"], e["id"]), reverse=True):
        size = len(json.dumps(episode, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())+1
        if size+2 > budget:
            continue
        if used+size > budget:
            break
        selected.append(episode)
        used += size
    return list(reversed(selected))


def _current_phase(periodization, today):
    p = periodization or {}
    key = today.isoformat()
    phase = next((v["kind"] for v in p.get("phases", []) if v["start_date"] <= key <= v["end_date"]), None)
    taper = any(v["start_date"] <= key <= v["end_date"] for v in p.get("taper_windows", []))
    return phase, taper


def local_evidence(episodes, recent, ti, today):
    """Retain support at comparable current dose/effort and observed fitness.

    A +5% association at 200 Q is not extrapolated to 800 Q with artificial
    certainty. This local-support policy is an explicit pilot setting.
    """
    index = {}
    cutoff = (today-timedelta(days=14)).isoformat()
    for row in ti:
        observation = row.get("index") or {}
        band = observation.get("general") or {}
        value = band.get("index")
        if (cutoff <= row.get("local_date", "") < today.isoformat() and
                observation.get("admission", {}).get("status") == "ACCEPTED" and band.get("valid") and
                isinstance(value, (int, float)) and value > 0):
            key = f"{row.get('sport')}:{observation.get('comparison_key')}:GLOBAL"
            index.setdefault(key, {}).setdefault(row["local_date"], []).append(value)
    selected = []
    for episode in episodes:
        supported = True
        for z in COMPONENTS:
            now = recent["components"][z]
            then = episode.get("dose", {}).get(z) or {}
            q, base = now.get("weekly_q"), then.get("baseline_q")
            if q is None or base is None or (q == 0) != (base == 0):
                supported = False
                break
            if q and not .67 <= q/base <= 1.5:
                supported = False
                break
            minutes, old_minutes = now.get("weekly_minutes"), then.get("baseline_minutes")
            if z != "STR" and q and minutes and old_minutes and not .85 <= (q/minutes)/(base/old_minutes) <= 1.15:
                supported = False
                break
        for o in episode.get("outcomes", []):
            if o.get("source") != "TI" or o.get("scope") != "GLOBAL":
                continue
            candidates = [median(v) for v in index.get(o.get("comparison_key"), {}).values()]
            prior = o.get("before_value")
            if candidates and prior and not .85 <= median(candidates)/prior <= 1.15:
                supported = False
        if supported:
            selected.append(episode)
    return selected


def context(repository, alias, profile, source, rows, today, *, periodization=None, envelope=None, entries=None):
    config = {**DEFAULTS, **(profile.get("individual_learning") or {})}
    if not enabled(profile):
        return None
    settings = repository.athlete_settings(alias)
    physiology = {"bounds": list(settings.zone_bounds_bpm), "hrmax": settings.hrmax_bpm} if settings else None
    context_key = _digest({"version": VERSION, "sport": profile["sport"], "physiology": physiology,
                           "equivalence": EQUIVALENCE_VERSION, "ti": TI_VERSION, "stress": STRESS_VERSION,
                           "channels": CHANNELS})
    memory = ManagementStore(repository).learning_memory(alias) or {}
    if memory.get("context_key") != context_key or memory.get("version") != VERSION:
        memory = {}
    entries = ResponseStore(repository).entries(alias) if entries is None else entries
    start = today-timedelta(days=89)
    if envelope is None or "activities" not in envelope:
        expected = envelope
        reader = getattr(repository, "active_planning_calendar", None) or repository.active_activity_calendar
        envelope = reader(alias, start, today) or {}
        if expected and any(envelope.get(k) != expected.get(k) for k in ("generation_id", "revision")):
            raise PersistentStoreFailure("Learning activity generation changed")
    snapshot = envelope.get("snapshot_payload") or {}
    # Never combine a fresh activity calendar with the caller's older loads.
    pinned_source = snapshot.get("load_history") or {}
    if _digest(pinned_source) != _digest(source):
        raise PersistentStoreFailure("Learning and planner load histories differ")
    unavailable = False
    try:
        ti = history_from_calendar(repository, alias, envelope) if envelope.get("activities") else []
    except (ValueError, PersistentStoreFailure):
        ti, unavailable = [], True
    history = build_history(entries=entries, wellness=snapshot.get("wellness_calendar") or [],
                            activities=envelope.get("activities") or [], start=start, end=today,
                            today=today, trainability=ti)
    history.update(generation_id=envelope.get("generation_id"), revision=envelope.get("revision"))
    calendar = repository.athlete_planning_calendar(alias)
    calendar = calendar.to_payload() if hasattr(calendar, "to_payload") else calendar or {}
    evidence = learning_evidence.build_evidence(source=source, rows=rows, entries=entries, history=history,
        trainability=ti, today=today, retained=memory, context_key=context_key,
        periodization=periodization, events=calendar.get("events", []))
    original_count = len(evidence["episodes"])
    evidence["episodes"] = bounded_episodes(evidence["episodes"])
    current = evidence["current"]
    if isinstance(current.get("families"), (list, tuple, set)):
        current["families"] = len(current["families"])
    quality = source.get("quality") or {}
    current["history_usable"] = bool(current.get("history_usable") and load_progression.history_matches(source, physiology)
        and not quality.get("limited_activities") and not quality.get("excluded_activities")
        and source.get("period_end") == today.isoformat())
    phase, taper = _current_phase(periodization, today)
    fallback = load_progression.accents(profile, phase, ["Z1"])
    state = planning_controls.resolve(profile, today, phase, fallback, periodization=periodization)
    recent = load_progression.observed_window(source, rows, today-timedelta(days=14), today)
    allowed = [z for z in (state or {}).get("accents", fallback)
               if z not in profile.get("component_targets_weekly", {}) and
               recent["complete"] and (recent["components"][z].get("weekly_q") or 0) > 0]
    if not state or state.get("explicit") or state.get("kind") not in {"BUILD", "MAINTAIN"}:
        allowed = []
    # Predict Q/time change from the SAME linear equivalence as canonical load.
    # Only lower zones with pulse-based simple structures permit this adapter.
    intensity_scale = {}
    if settings:
        for i, z in enumerate(COMPONENTS):
            if z not in {"Z1", "Z2", "Z3"}:
                continue
            observed = recent["components"][z]
            q, minutes = observed.get("weekly_q"), observed.get("weekly_minutes")
            width = settings.zone_bounds_bpm[i+1]-settings.zone_bounds_bpm[i]
            if z == "Z1":
                width = min(20., width)
            if q and minutes:
                intensity_scale[z] = equivalence_slope(z)/100*width/(q/minutes)
    supported = local_evidence(evidence["episodes"], recent, ti, today)
    current["unresolved_recovery"] = any(not e.get("response", {}).get("recovered")
        and e["end"] >= (today-timedelta(days=42)).isoformat() for e in supported)
    models = individual_learning.fit(supported, today)
    report = individual_learning.decide(models=models, episodes=supported, current=current,
        today=today, config=config, allowed_components=allowed, phase=phase, taper=taper,
        retained=memory, intensity_scale=intensity_scale)
    from .learning_methods import assess_methods
    method_source = {**source, "calendar": {"activities": envelope.get("activities") or []}}
    method_result = assess_methods(entries=entries, source=method_source, today=today,
                                   history=history, retained=memory.get("methods"))
    preferences = method_result.get("preferences", [])
    # One tested dimension per experiment. A concurrent method preference must
    # not turn a volume/intensity probe into an uninterpretable combined change.
    if (report.get("decision") or {}).get("experimental") or report["status"] in {"COLLECTING", "CAUTION", "OFF"}:
        preferences = []
    report["method_preferences"] = preferences
    report["current"] = current
    report["method_summary"] = method_result.get("summary")
    report["limitations"] = ["Наблюдавана индивидуална връзка; не доказва причинност.",
        "Recovery, 7/40, календарът и зададените граници остават ограничения на всяка тренировка."]
    if unavailable:
        report["limitations"].append("Съпоставимите данни за индекса на тренираност не са налични при тази оценка.")
    report["exclusions"] = evidence.get("exclusions", [])[-20:]
    report["archived_evidence_count"] = len(evidence["episodes"])
    report["memory_window_dropped"] = original_count-len(evidence["episodes"])
    report["out_of_support_count"] = len(evidence["episodes"])-len(supported)
    report["source"] = {"generation_id": envelope.get("generation_id"), "revision": envelope.get("revision"),
                        "response_revision_fingerprint": _digest(sorted((e["kind"], e["entry_key"], e["revision"]) for e in entries)),
                        "context_key": context_key}
    report["model_summary"] = {k: {"observations": v["posterior"]["count"], "validation": v["validation"]} for k, v in models.items()}
    report["memory"] = {"version": VERSION, "context_key": context_key, "as_of": today.isoformat(),
                        "episodes": deepcopy(evidence["episodes"]), "decision": deepcopy(report.get("decision")),
                        "methods": method_result.get("memory", {}),
                        "cooldown_until": (report.get("decision") or {}).get("expires_on") or memory.get("cooldown_until"),
                        "cooldown_mode": (report.get("decision") or {}).get("mode") or memory.get("cooldown_mode")}
    return report
