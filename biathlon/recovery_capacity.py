"""Expert exhaustion estimate using the existing selected intensity coordinate.

No within-session reconstitution or claim of measured physiological reserves.
The compact inverse table is built once per capacity context, never per sample.
"""
from bisect import bisect_right
import math

VERSION = "segment-capacity-recovery-v1"
ZONES = tuple(f"Z{i}" for i in range(1, 6))


def prepare(curve, minutes, comparison):
    table = []
    if curve is not None:
        low, high = map(math.log, (curve.times[0], curve.times[-1]))
        times = sorted(set(curve.times) | {math.exp(low+(high-low)*i/512) for i in range(513)})
        table = sorted({math.log(curve.speed(t)*3.6): math.log(t/60) for t in times}.items())
    return {"version": VERSION, "log_speeds": [p[0] for p in table],
            "log_minutes": [p[1] for p in table], "fallback_minutes": dict(minutes),
            "bounds": {r["zone"]: [r.get("low_kmh"), r.get("high_kmh")]
                       for r in comparison["zones"]}}


def segment(capacity, estimate, minutes):
    """Return fraction and explicit source; coefficient/Q never multiplies it."""
    z = estimate["zone"]
    low, high = capacity.get("bounds", {}).get(z, (None, None))
    speed = (low+(high-low)*estimate["relative_position"]
             if low is not None and high is not None and low > 0 else None)
    xs, ys = capacity.get("log_speeds", ()), capacity.get("log_minutes", ())
    if speed and xs and xs[0]-1e-12 <= math.log(speed) <= xs[-1]+1e-12:
        x = min(xs[-1], max(xs[0], math.log(speed)))
        i = max(0, min(len(xs)-2, bisect_right(xs, x)-1))
        t = math.exp(ys[i]+(ys[i+1]-ys[i])*(x-xs[i])/(xs[i+1]-xs[i]))
        return minutes/t, "CURVE_ESTIMATE"
    return minutes/capacity["fallback_minutes"][z], "REFERENCE_CAPACITY_ESTIMATE"


def multiplier(fraction):
    return .8 + .6*max(0., min(.5, fraction-.5))


class Load(dict):
    """Numeric E vector with an internal recovery sidecar, never serialized as E.

    Explicit copy/add methods preserve Q and exhaustion across planner trials.
    Public E accounting remains the same six numeric components.
    """
    def __init__(self, values=(), *, direct=None, depletion=None):
        super().__init__(values)
        self.direct = dict(direct or getattr(values, "direct", {}))
        self.depletion = dict(depletion or getattr(values, "depletion", {}))

    def frozen(self):
        return tuple(sorted(self.items())), tuple(sorted(self.direct.items())), tuple(sorted(self.depletion.items()))

    @classmethod
    def thaw(cls, frozen):
        values, direct, depletion = frozen
        return cls(values, direct=dict(direct), depletion=dict(depletion))

    def add(self, other):
        for z, value in other.items():
            self[z] = self.get(z, 0.)+value
        for field in ("direct", "depletion"):
            dest = getattr(self, field)
            for z, value in getattr(other, field, {}).items():
                dest[z] = dest.get(z, 0.)+value


def daily_rows(source):
    """Preserve covered dates, sum activities once, retain legacy STR inputs."""
    totals = {}
    for activity in source.get("activities", ()):
        evidence = activity.get("load_source") or {}
        fractions = (evidence.get("recovery_capacity") or {}).get("fractions", {})
        capacities = (evidence.get("capacity_context") or {}).get("minutes", {})
        if not capacities:
            from .hr_speed import TMAX_RANGES_S
            capacities = {z: sum(TMAX_RANGES_S["Z4" if z == "Z5" else z])/120 for z in ZONES}
        for r in activity.get("zones", ()):
            z = r["zone"]
            if z not in ZONES:
                continue
            key = activity["date"], z
            q = r.get("equivalent_time_min") or 0.
            estimated_q = r.get("planning_estimated_q") or 0.
            fallback = (z not in fractions or estimated_q > 0 or
                        (evidence.get("recovery_capacity") or {}).get("source_seconds", {}).get("REFERENCE_CAPACITY_ESTIMATE", 0.) > 0)
            u = (fractions[z]+estimated_q/capacities[z] if z in fractions
                 else (r.get("raw_time_min") or q)/capacities[z])
            old = totals.get(key, (0., 0., False))
            totals[key] = old[0]+q, old[1]+u, old[2] or fallback
    rows = []
    for r in source.get("daily", ()):
        out = {k: r[k] for k in ("date", "zone", "effective_load")}
        key = r["date"], r["zone"]
        if key in totals:
            q, u, fallback = totals[key]
            out.update(direct_load=q, depletion_fraction=u, capacity_estimated=fallback)
        elif r["effective_load"] == 0:
            out.update(direct_load=0., depletion_fraction=0., capacity_estimated=False)
        # Legacy imports lacking activity evidence retain their explicit E basis.
        rows.append(out)
    rows.extend({"date": r["date"], "zone": "STR", "effective_load": r["effective_load"]}
                for r in (source.get("strength") or {}).get("daily", ()))
    return rows
