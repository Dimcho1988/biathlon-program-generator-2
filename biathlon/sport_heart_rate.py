"""Explicit coaching policy: cycling HR is 7 bpm below the common reference.

Provider observations and the athlete's reference HRmax are never rewritten.
The offset is an initial coaching assumption, not an individual measurement.
"""
import math

VERSION = "sport-hr-reference-cycling-plus7-v1"
CYCLING_OFFSET_BPM = 7.
CYCLING_SPORTS = frozenset({"ride", "cycling", "biking", "bike", "roadbike",
    "roadbiking", "roadcycling", "virtualride", "indoorcycling", "mountainbikeride",
    "mountainbiking", "mountainbike", "gravelride", "gravelcycling"})


def sport_key(sport):
    return "".join(c for c in sport.casefold() if c.isalnum()) if isinstance(sport, str) else ""


def is_cycling(sport):
    return sport_key(sport) in CYCLING_SPORTS


def reference_offset(sport):
    return CYCLING_OFFSET_BPM if is_cycling(sport) else 0.


def policy(sport, bounds=(), hrmax=None):
    offset = reference_offset(sport)
    return {"version": VERSION, "offset_bpm": offset,
            "basis": "COACH_INITIAL_ASSUMPTION" if offset else "REFERENCE_SPORT",
            "reference_zone_bounds_bpm": list(bounds),
            "sport_zone_bounds_bpm": [float(b)-offset for b in bounds],
            "reference_hrmax_bpm": hrmax,
            "sport_hrmax_bpm": float(hrmax)-offset if hrmax is not None else None,
            "raw_hr_unchanged": True}


def local_settings(settings, sport):
    """An ephemeral profile for target HR and equivalent-time arithmetic."""
    offset = reference_offset(sport)
    if not offset or getattr(settings, "sport_hr_offset_bpm", 0):
        return settings
    from types import SimpleNamespace
    # Settings objects can be frozen/slots dataclasses. Preserve their public
    # fields in a transient view, never mutate the stored reference profile.
    values = {name: getattr(settings, name) for name in dir(settings)
              if not name.startswith("_") and not callable(getattr(settings, name))}
    values.update(zone_bounds_bpm=tuple(float(v)-offset for v in settings.zone_bounds_bpm),
                  hrmax_bpm=settings.hrmax_bpm-offset if settings.hrmax_bpm else None,
                  sport_hr_offset_bpm=offset)
    return SimpleNamespace(**values)


def validate_offset(offset):
    if isinstance(offset, bool) or not isinstance(offset, (int,float)) or not math.isfinite(offset) or not 0 <= offset <= 30:
        raise ValueError("Invalid HR reference offset")
    return float(offset)
