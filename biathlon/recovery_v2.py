"""Causal daily E recovery, independent of the bounded Tref denominator.

Each day's dose has its own immutable decay rates. The two exponential terms
allow curve shape to change while preserving material doses' time to 90%
readiness. E already includes cascade/spillover; never apply them again here.
This is an expert model, not a validated physiological recovery measurement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math

VERSION = "recovery-daily-e-biexponential-v2.1"
ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5", "STR")
INITIAL_DAILY = dict(zip(ZONES, (40., 20., 8., 4., 2., 8.)))
SENSITIVITY = dict(zip(ZONES, (.55, .70, .88, 1., 1.12, .95)))


def number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {name}")
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(f"Invalid {name}")
    return float(value)


def defaults():
    return {z: {"duration_coefficient": 1., "shape": 1.,
                "sensitivity": SENSITIVITY[z], "initial_daily_min": INITIAL_DAILY[z]}
            for z in ZONES}


def baseline(previous, initial):
    """Only explicitly covered, completed days; zero is a real rest day.

    Seven days and one initial-equivalent week of E form the expert cold-start
    transition. This avoids a discontinuity from zero to near-zero zone history.
    The raw mean is always returned separately and is never clamped.
    """
    values = [number(v, "history") for v in previous]
    initial = number(initial, "initial daily load", positive=True)
    n = len(values)
    raw = math.fsum(values) / n if n else None
    if not n or raw == 0:
        return initial, raw, n, "NO_HISTORY" if not n else "NO_ZONE_LOAD"
    weight = min(1., n / 7., math.fsum(values) / (7. * initial))
    source = "PERSONAL" if weight==1 else "SHORT_HISTORY" if n<7 else "SPARSE_ZONE_HISTORY"
    return weight * raw + (1. - weight) * initial, raw, n, source


@dataclass(frozen=True)
class Impulse:
    day: date
    amplitude: float
    rate_slow: float
    shape: float
    nominal_days: float

    def residual(self, days):
        t = max(0., days)
        return self.amplitude * .5 * (math.exp(-self.rate_slow*t)
            + math.exp(-self.rate_slow*self.shape*t))


def impulse(day, effective, daily_baseline, config):
    dose = number(effective, "effective load") / number(daily_baseline, "daily baseline", positive=True)
    coefficient = number(config["duration_coefficient"], "duration coefficient", positive=True)
    shape = number(config["shape"], "shape", positive=True)
    sensitivity = number(config["sensitivity"], "sensitivity", positive=True)
    if not 1 <= shape <= 10:
        raise ValueError("Shape must be between 1 and 10")
    if dose == 0:
        return None
    days = coefficient * dose
    # Cap one daily impulse at 100 percentage points, not the total fatigue.
    # Larger doses still extend their decay horizon through `days`.
    amplitude = min(100., 100. * sensitivity * dose)
    # Small impulses can already satisfy the absolute 90% readiness threshold;
    # they still persist and sum with previous fatigue. Never reset to 0% ready.
    # Near the readiness threshold, solving F(D)=10 without a decay floor
    # makes A=10+epsilon persist almost indefinitely. Require at least a
    # half-life per nominal window; material doses (A>=20) still reach 10 at D.
    fraction = min(.5, 10. / amplitude)
    lo, hi = 0., -math.log(fraction)
    for _ in range(55):
        mid = (lo + hi) / 2
        if .5 * (math.exp(-mid) + math.exp(-shape * mid)) > fraction:
            lo = mid
        else:
            hi = mid
    return Impulse(day, amplitude, (lo + hi) / (2 * days), shape, days)


def residual(impulses, target, days=0.):
    return math.fsum(i.residual((target-i.day).days+days) for i in impulses if i.day <= target)


def days_to_ready(impulses, target):
    if residual(impulses, target) <= 10.:
        return 0.
    # Each term tends to zero. Derive a finite upper bracket from slow rates.
    active = [i for i in impulses if i.day <= target]
    total = residual(active, target)
    hi = math.log(total / 10.) / min(i.rate_slow for i in active) + 1.
    lo = 0.
    for _ in range(60):
        mid = (lo + hi) / 2
        if residual(active, target, mid) > 10.:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def simulate(daily, configs=None, *, target=None):
    """Daily rows: date, zone, effective_load. Missing days are unknown, not zero.

    Calendar-day granularity is retained from the canonical source. Multiple
    sessions on one day are one daily dose; no invented within-day timestamps.
    """
    configs = configs or defaults()
    grouped = {z: {} for z in ZONES}
    for row in daily:
        z = row["zone"]
        if z not in grouped:
            raise ValueError("Unknown recovery zone")
        day = date.fromisoformat(row["date"])
        if day in grouped[z]:
            raise ValueError("Duplicate daily recovery dose")
        grouped[z][day] = number(row["effective_load"], "effective load")
    all_days = sorted({d for days in grouped.values() for d in days})
    target = target or (all_days[-1] if all_days else date.today())
    rows, current, forecast = [], [], []
    for z in ZONES:
        config = configs[z]
        doses = grouped[z]
        impulses = []
        last_baseline = baseline([], config["initial_daily_min"])
        for day in sorted(d for d in doses if d <= target):
            previous = [v for d,v in sorted(doses.items()) if day-timedelta(days=40) <= d < day]
            used, raw, n, source = baseline(previous, config["initial_daily_min"])
            before = residual(impulses, day)
            added = impulse(day, doses[day], used, config)
            if added:
                impulses.append(added)
            after = residual(impulses, day)
            rows.append({"date":day.isoformat(), "zone":z,
                "readiness_before_percent":max(0., 100.-before),
                "readiness_after_percent":max(0., 100.-after),
                "residual_fatigue_after":after, "impulse":added.amplitude if added else 0.,
                "effective_load":doses[day], "baseline_daily_min":used,
                "baseline_raw_daily_min":raw, "history_days":n, "baseline_source":source,
                "isolated_days_to_90":days_to_ready([added],day) if added else 0.})
            last_baseline = used, raw, n, source
        f = residual(impulses, target)
        horizon = days_to_ready(impulses, target)
        current.append({"zone":z, "readiness_percent":max(0.,100.-f),
            "residual_fatigue":f, "days_to_practical_recovery":horizon,
            "baseline_daily_min":last_baseline[0], "baseline_raw_daily_min":last_baseline[1],
            "history_days":last_baseline[2], "baseline_source":last_baseline[3]})
        end = max(1., horizon * 1.2)
        forecast.extend({"zone":z,"days":end*i/60,"readiness_percent":max(0.,100.-residual(impulses,target,end*i/60))} for i in range(61))
    return {"daily":sorted(rows,key=lambda r:(r["date"],r["zone"])),
            "current":current,"forecast":forecast,"as_of":target.isoformat(),
            "time_resolution":"calendar-day", "ready_threshold_percent":90.}
