"""Freeze the continuous capacity used to convert direct Q into component E.

Q is expressed at each zone's upper boundary, except Z5, which is expressed
at its lower boundary (the shared Z4/Z5 edge). Capacity must use exactly those
same references. Historical weekly Q and historical E/Tref are never capacities.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math

from biathlon import dosing_curve, hr_speed, preliminary_capacity, speed_duration, speed_equivalence, initial_speed_profile
from .speed_load_cache import SpeedLoadCache


VERSION = "component-load-common-profile-v3-initial-heuristic"
COMPARISON_CONTEXT_VERSION = speed_equivalence.VERSION
ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5")
_context_cache = SpeedLoadCache(max_entries=32, ttl_seconds=300)


class ComponentCapacityUnavailable(ValueError):
    """An individual curve exists but cannot define its zonal Tmax references."""

    code = "COMPONENT_CAPACITY_REFERENCE_REQUIRED"

    def __init__(self, zone=None):
        reference = f" за {zone}" if zone else " за зоните"
        self.user_message = (
            f"Наличната крива скорост–време няма валидна референтна скорост{reference}. "
            "Попълнете или проверете HRmax и пулсовите граници в настройките на спортиста, "
            "след това обновете изчисленията на модела скорост–време."
        )
        super().__init__(self.user_message)


def _positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _tests(view):
    keys = set(view.get("active_test_keys") or ())
    tests = [row["payload"] for row in view.get("tests", ())
             if row.get("entry_key") in keys]
    if any(t.get("maximal") is not True or t.get("test_mode", "STRICT") != "STRICT"
           or t.get("comparable") is False or t.get("generated") or t.get("is_estimated")
           or str(t.get("source", "")).upper() in {"MODEL", "MODEL_GENERATED", "GENERATED", "ESTIMATED", "REFERENCE"}
           for t in tests):
        raise ValueError("Component capacity requires accepted maximal curve tests")
    return tests


def _selected_curve(view):
    """Use the exact continuous curve selected for method dosing, not chart samples."""
    if view.get("status") == "INITIAL_HEURISTIC":
        curve = initial_speed_profile.curve_from_view(view)
        if curve is None:
            raise ValueError("Initial component curve requires explicit heuristic provenance")
        return curve, view.get("hr_model"), "INITIAL_HEURISTIC"
    if view.get("status") not in {"CALIBRATED", "PRELIMINARY"}:
        return None, None, None
    tests = _tests(view)
    blend = dosing_curve.from_view(view)
    if blend is not None:
        return blend, view["dosing_model"].get("hr_model"), "BLENDED_DOSING_CURVE"
    prior = preliminary_capacity.curve_from_summary(view.get("preliminary_capacity"))
    if not tests and prior is None:
        # Do not manufacture an individual curve by calibrating an empty list.
        raise ValueError("Available component curve has no individual evidence")
    curve = speed_duration.calibrated(tests, prior=prior)
    return curve, view.get("hr_model"), "SPEED_DURATION" if tests else "SPEED_DURATION_PRIOR"


def context_from_speed_view(view=None):
    """Return serializable Q-reference capacities and unambiguous source evidence.

    An unavailable/conflicting curve uses explicit expert duration anchors, or
    the existing expert midpoint when there is no profile. An available curve
    is inverted at its published zone reference speeds without clipping the
    result to expert bounds. An incomplete available curve is an error, not an
    excuse to silently replace individual evidence with an expert duration.
    """
    view = view or {}
    curve, hr_model, curve_source = _selected_curve(view)
    if curve is not None and hr_model is None:
        # Absolute speed evidence alone is not an HR-boundary observation.
        # Keep HR-only load available with explicit expert continuous capacity.
        profile = (view.get("dosing_model") or {}).get("speed_zones") or view.get("speed_zones")
        if profile and profile.get("status") == "UNAVAILABLE":
            curve, curve_source = None, None
        else:
            raise ComponentCapacityUnavailable()
    anchors = {row.get("zone"): row for row in
               (view.get("preliminary_capacity") or {}).get("anchors", ())}
    references = {}
    minutes = {}
    sources = {}
    if curve is None:
        for zone in ZONES:
            reference_zone = "Z4" if zone == "Z5" else zone
            anchor = anchors.get(reference_zone) or {}
            seconds = anchor.get("duration_s")
            if seconds is not None and not _positive(seconds):
                raise ValueError("Invalid expert continuous capacity")
            if seconds is None:
                seconds = sum(hr_speed.TMAX_RANGES_S[reference_zone]) / 2
            minutes[zone] = float(seconds) / 60
            sources[zone] = "EXPERT_CONTINUOUS_TMAX"
            references[zone] = {
                "reference_zone": reference_zone,
                "reference_edge": "LOWER_SHARED_Z4" if zone == "Z5" else "UPPER",
                "reference_speed_kmh": None,
                "reference_source": anchor.get("duration_source", "EXPERT_MIDPOINT"),
                "curve_unavailable_reason": view.get("status", "NO_SPEED_VIEW"),
            }
    else:
        rows = {row.get("zone"): row for row in (hr_model or {}).get("zones", ())}
        for zone in ZONES:
            reference_zone = "Z4" if zone == "Z5" else zone
            row = rows.get(reference_zone) or {}
            speed = row.get("speed_kmh")
            if not _positive(speed):
                raise ComponentCapacityUnavailable(reference_zone)
            # A stored anchor duration can describe another model. The selected
            # curve's inverse is authoritative, including extrapolated regions.
            try:
                seconds = curve.inverse(float(speed) / 3.6)
            except (ValueError, ArithmeticError) as exc:
                anchor = anchors.get(reference_zone) or {}
                seconds = anchor.get("duration_s") or sum(hr_speed.TMAX_RANGES_S[reference_zone])/2
                if not _positive(seconds):
                    raise ComponentCapacityUnavailable(reference_zone) from exc
                minutes[zone] = float(seconds)/60
                sources[zone] = "EXPERT_CONTINUOUS_TMAX"
                references[zone] = {"reference_zone": reference_zone,
                    "reference_edge": "LOWER_SHARED_Z4" if zone == "Z5" else "UPPER",
                    "reference_speed_kmh": float(speed), "reference_source": row.get("source"),
                    "reference_reason": "BOUNDARY_OUTSIDE_CURVE_DOMAIN", "is_estimate": True}
                continue
            if not _positive(seconds):
                raise ComponentCapacityUnavailable(reference_zone)
            minutes[zone] = float(seconds) / 60
            sources[zone] = curve_source
            references[zone] = {
                "reference_zone": reference_zone,
                "reference_edge": "LOWER_SHARED_Z4" if zone == "Z5" else "UPPER",
                "reference_speed_kmh": float(speed),
                "reference_source": row.get("index_hr_source", row.get("source")),
                "reference_reason": row.get("reason"),
                "is_estimate": True,
            }
    result = {
        "version": VERSION,
        "minutes": minutes,
        "sources": sources,
        "references": references,
        "unit": "DIRECT_EQUIVALENT_MINUTES_AT_ZONE_REFERENCE",
        "sport": view.get("sport"),
        "curve_model_version": getattr(curve, "model_version", None) if curve is not None else None,
        "status": "CURVE" if curve is not None else "EXPERT",
        "speed_role": view.get("speed_role", "FLAT_EQUIVALENT"),
        "initial_heuristic": view.get("status") == "INITIAL_HEURISTIC",
        "calibration_source": "INITIAL_HEURISTIC" if view.get("status") == "INITIAL_HEURISTIC" else curve_source,
    }
    # Additive comparison uses the exact method-dosing boundaries. Canonical
    # continuous capacities and their fingerprint stay unchanged; Tmax is not
    # a migrated speed-index Tref. No historical HR normalizer is repurposed.
    profile = ((view.get("dosing_model") or {}).get("speed_zones")
               if curve_source == "BLENDED_DOSING_CURVE" else view.get("speed_zones"))
    comparison = speed_equivalence.prepare(profile, sport=view.get("sport"),
        curve_version=result["curve_model_version"])
    result["zone_profile"] = profile
    result["fingerprint"] = sha256(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode()).hexdigest()
    # Source identities are provenance, not physiological parameters: refreshing
    # the same evidence must not invalidate an otherwise identical context.
    result["comparison_parameters"] = comparison
    result["source_generation_id"] = view.get("source_generation_id")
    result["source_revision"] = view.get("source_revision")
    return result


def read_contexts(repository, alias, sports, *, speed_by_sport=None):
    """Read once per sport; persistence/service failures are never expert fallbacks."""
    from . import model_service

    known = speed_by_sport or {}
    sports = list(dict.fromkeys(sports))
    missing = [s for s in sports if s not in known]
    # Only persisted repositories can provide the complete fresh model inputs.
    # Pure callers and supplied views retain the existing uncached contract.
    namespace = getattr(repository, "speed_load_cache_namespace", None)
    if not missing or not namespace or not hasattr(repository, "_request"):
        return {sport: context_from_speed_view(known[sport] if sport in known else
                model_service.speed_view(repository, alias, sport)) for sport in sports}
    inputs = model_service.speed_view_inputs(repository, alias)
    settings = inputs["settings"]
    identity = {"namespace": namespace, "alias": alias, "version": VERSION, "comparison_version": COMPARISON_CONTEXT_VERSION,
                "day": inputs["today"].isoformat(), "sports": missing,
                "bounds": settings.zone_bounds_bpm, "hrmax": settings.hrmax_bpm,
                "timezone": settings.timezone, "calendar": inputs["calendar"],
                "entries": inputs["entries"], "history": inputs["history"],
                "initial_profile": inputs.get("initial_profile"),
                "initial_zone_policy": initial_speed_profile.ZONE_POLICY_VERSION}
    key = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    calculated = _context_cache.get_or_compute(key, lambda: {
        s: context_from_speed_view(model_service.speed_view(repository, alias, s,
            _inputs=inputs, _capacity_only=True)) for s in missing})
    return {s: context_from_speed_view(known[s]) if s in known else calculated[s] for s in sports}
