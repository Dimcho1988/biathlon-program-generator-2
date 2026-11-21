"""Presentation-only lap averages from the pinned, full-resolution models."""
from bisect import bisect_right
from collections.abc import Mapping

from biathlon.unified_load import finite, observed_hr_valid, _smart_span
from .device_laps import catalog_laps, number


class Integral:
    """Integrals over disjoint linear spans, with explicit unsupported gaps."""
    def __init__(self, spans):
        self.spans = spans
        self.ends = [s[1] for s in spans]
        self.area = [0.]
        self.time = [0.]
        for left, right, a, b in spans:
            self.area.append(self.area[-1] + (right-left)*(a+b)/2)
            self.time.append(self.time[-1] + right-left)

    def at(self, value):
        i = bisect_right(self.ends, value)
        area, seconds = self.area[i], self.time[i]
        if i < len(self.spans):
            left, right, a, b = self.spans[i]
            dt = max(0., min(value, right)-left)
            area += a*dt + (b-a)*dt*dt/(2*(right-left))
            seconds += dt
        return area, seconds

    def between(self, left, right):
        a, ta = self.at(left)
        b, tb = self.at(right)
        dt = max(0., tb-ta)
        return ((b-a)/dt if dt > 1e-8 else None), dt


def _hr_integrals(shadow, duration):
    rows = sorted((r for r in shadow.get("timeseries", []) if finite(r.get("elapsed_s"))), key=lambda r: r["elapsed_s"])
    raw, paired, modulated = [], [], []
    previous = 0.
    for i, row in enumerate(rows):
        following = rows[i+1] if i+1 < len(rows) else None
        smart = _smart_span(row, following)
        left = max(0., previous, row["elapsed_s"])
        right = min(duration, following["elapsed_s"] if smart else row["elapsed_s"]+1,
                    following["elapsed_s"] if following else duration)
        previous = max(previous, right)
        if (right <= left or not observed_hr_valid(row)
                or (following and row.get("hr_interval_classification") not in {None, "original_1hz", "smart_recording"})
                or (smart and not observed_hr_valid(following))):
            continue
        a = row["hr_raw_bpm"]
        b = following["hr_raw_bpm"] if smart else a
        if smart:
            span = following["elapsed_s"] - row["elapsed_s"]
            a, b = (a + (b-a)*(t-row["elapsed_s"])/span for t in (left, right))
        raw.append((left, right, a, b))
        m = row.get("hrmod_final_bpm")
        n = following.get("hrmod_final_bpm") if smart else m
        if all(finite(v) and v > 0 for v in (m, n)):
            if smart:
                m, n = (m + (n-m)*(t-row["elapsed_s"])/span for t in (left, right))
            paired.append((left, right, a, b))
            modulated.append((left, right, m, n))
    return Integral(raw), Integral(paired), Integral(modulated)


def _speed_integrals(shadow):
    raw, paired, flat = [], [], []
    previous = 0.
    # Preserve the same independent, right-aligned speed support as the existing
    # whole-activity speed comparison. Never substitute load-completion speeds.
    for row in sorted(shadow.get("speed_test_series") or [], key=lambda r: r.get("elapsed_s", 0)):
        t, dt, v, vf = (row.get(k) for k in ("elapsed_s", "dt_s", "speed_raw_kmh", "vflat_b65_kmh"))
        if not finite(t) or not finite(dt) or not 0 < dt <= 1:
            continue
        left, right = max(0., t-dt, previous), t
        previous = max(previous, right)
        if right <= left or number(v) is None:
            continue
        raw.append((left, right, v, v))
        if not row.get("exclusion_reason") and number(vf) is not None:
            paired.append((left, right, v, v))
            flat.append((left, right, vf, vf))
    return Integral(raw), Integral(paired), Integral(flat)


def enrich_activity_laps(catalog, shadow, raw_input=None):
    rows, status = catalog_laps(catalog)
    if not rows:
        return [], status
    duration = max(number(catalog.get("elapsed_time_s")) or 0., number(catalog.get("recording_time_s")) or 0.)
    source = shadow if isinstance(shadow, Mapping) else {"timeseries": (raw_input or {}).get("samples", [])}
    hr, paired_hr, mod = _hr_integrals(source, duration)
    speed, paired_speed, flat = _speed_integrals(source)
    counters = {"device": 0, "intervals": 0}
    for row in rows:
        row.setdefault("source", "intervals")
        group = "device" if row["source"] == "device" else "intervals"
        counters[group] += 1
        row["number"] = counters[group]
        left, right = number(row.get("start_time_s")), number(row.get("end_time_s"))
        # Provider indices refer to its original stream, not seconds or our
        # normalized samples. Without explicit time bounds do not guess.
        valid = left is not None and right is not None and 0 <= left < right <= duration + 2
        row["has_time_bounds"] = valid
        row.update(average_hrmod_bpm=None, average_vflat_kmh=None, comparison_hr_bpm=None,
                   comparison_speed_kmh=None, hr_comparison_seconds=0., speed_comparison_seconds=0.)
        if not valid:
            continue
        if row.get("elapsed_time_s") is None:
            row["elapsed_time_s"] = right-left
        if row.get("average_hr_bpm") is None:
            row["average_hr_bpm"] = hr.between(left, right)[0]
        if row.get("average_speed_kmh") is None:
            row["average_speed_kmh"] = speed.between(left, right)[0]
        row["comparison_hr_bpm"] = paired_hr.between(left, right)[0]
        row["average_hrmod_bpm"], row["hr_comparison_seconds"] = mod.between(left, right)
        row["comparison_speed_kmh"] = paired_speed.between(left, right)[0]
        row["average_vflat_kmh"], row["speed_comparison_seconds"] = flat.between(left, right)
    return rows, status


def add_comparison_channels(series, shadow):
    """Display decimation only; lap averages above use the full stored series."""
    models = {r.get("elapsed_s"): r for r in (shadow or {}).get("timeseries", [])}
    for point in series["series"]:
        model = models.get(point["elapsed_s"], {})
        point["hrmod_bpm"] = number(model.get("hrmod_final_bpm")) if observed_hr_valid(model) else None
        point["vflat_kmh"] = number(model.get("vflat_b65_kmh"))
    return series
