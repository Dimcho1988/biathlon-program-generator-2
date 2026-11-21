"""Prospective canonical 7/40 checks, separate from prescription targets.

The existing maximum index is a coaching policy. A desired maintenance index
is not this limit. Proposed E enters all three moving means exactly as it
does in the recorded-load model; it never changes the long-term Q reference.
"""
from datetime import timedelta
import math

from .constants import COMPONENTS, DEFAULT_PARAMETERS

VERSION = "planning-7-40-envelope-v2-exact-candidate"
INDEX_CEILING = 2.


def _index(window, extra):
    e7, e40, e50 = [(s + extra) / n for s, n in zip(window["sums"], window["counts"])]
    base = max(window["base"], .5 * e50)
    return (base + e7) / max(base + e40, 1e-9)


def permits(budget, extra):
    """Check an exact candidate, independently of the Q search bound."""
    if not math.isfinite(extra) or extra < 0:
        return False
    window = budget.get("index_window")
    if window is None:
        return extra <= budget["deficit_effective"] + .001
    return _index(window, extra) <= INDEX_CEILING + 1e-12


def headroom(rows, day, maximum_effective, *, parameters=DEFAULT_PARAMETERS):
    """Largest additional E, bounded only by 7/40 and possible remaining Q.

    The finite search bound comes from all remaining component prescriptions,
    including their maximum possible influence. If that entire bound fits,
    the index cannot constrain this candidate. Missing history is not filled
    with rest; only the proposed day is introduced as a known forecast day.
    """
    windows = [parameters[k] for k in ("short_window_days", "long_window_days", "base_window_days")]
    cutoffs = [(day-timedelta(days=n-1)).isoformat() for n in windows]
    upper = day.isoformat()
    values = {z: {} for z in COMPONENTS}
    for row in rows:
        if cutoffs[-1] <= row["date"] <= upper:
            zone = row["zone"]
            if row["date"] in values[zone]:
                raise ValueError("Duplicate daily load in 7/40 forecast")
            value = float(row["effective_load"])
            if not math.isfinite(value) or value < 0:
                raise ValueError("Invalid effective load in 7/40 forecast")
            values[zone][row["date"]] = value
    result = {}
    for z in COMPONENTS:
        daily = values[z]
        daily.setdefault(upper, 0.)
        samples = [[v for d, v in daily.items() if d >= cutoff] for cutoff in cutoffs]
        sums, counts = [math.fsum(v) for v in samples], [len(v) for v in samples]
        window = {"sums": sums, "counts": counts, "base": parameters["base_loads"][z]}
        bound = float(maximum_effective[z])
        if not math.isfinite(bound):
            raise ValueError("7/40 search bound must be finite")
        bound = max(0., bound)
        before = _index(window, 0.)
        bound_fits = _index(window, bound) <= INDEX_CEILING+1e-12
        lo = 0.
        if before <= INDEX_CEILING+1e-12:
            if bound_fits:
                lo = bound
            else:
                hi = bound
                for _ in range(36):
                    mid = (lo+hi)/2
                    if _index(window, mid) <= INDEX_CEILING:
                        lo = mid
                    else:
                        hi = mid
        result[z] = {"deficit_effective": math.floor(lo*1000)/1000,
                     "index_window": window,
                     "index_7_40_before": round(before, 6), "index_7_40_limit": INDEX_CEILING,
                     "effective_limit_basis": "PROSPECTIVE_CANONICAL_7_40",
                     "index_limit_binding": not bound_fits or before > INDEX_CEILING}
    return result
