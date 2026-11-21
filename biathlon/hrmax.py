"""Working HRmax from explicit age or sustained *measured raw* observations."""
from collections import deque
from datetime import datetime
import math

VERSION = "working-hrmax-raw15s-v1"
WINDOW_SECONDS = 15
MAX_WINDOW_RANGE_BPM = 10.


def tanaka(age_years):
    if isinstance(age_years, bool) or not isinstance(age_years, int) or not 5 <= age_years <= 100:
        raise ValueError("Age must be between 5 and 100 years")
    return math.floor(208.-.7*age_years+.5)


def observed_peak(rows, *, offset_bpm=0.):
    """No interpolation, HRmod, clipping, duplicated seconds or gaps allowed."""
    window = deque()
    best = None
    previous = None
    for row in rows:
        t = row.get("elapsed_s")
        if t is None and row.get("timestamp"):
            try:
                t = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                t = None
        h = row.get("hr_raw_bpm")
        valid = (not row.get("is_estimated") and not row.get("generated")
                 and row.get("hr_source", "raw") in {"raw", "MEASURED", "RAW"}
                 and isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t)
                 and isinstance(h, (int, float)) and not isinstance(h, bool)
                 and math.isfinite(h) and 30 <= h <= 240)
        if not valid or previous is not None and not math.isclose(t-previous, 1., abs_tol=.01):
            window.clear()
        previous = t if valid else None
        if not valid:
            continue
        window.append((t, float(h)))
        if len(window) > WINDOW_SECONDS:
            window.popleft()
        if len(window) != WINDOW_SECONDS:
            continue
        values = [x[1] for x in window]
        if max(values)-min(values) > MAX_WINDOW_RANGE_BPM:
            continue
        mean = math.fsum(values)/WINDOW_SECONDS
        candidate = math.floor(mean+offset_bpm+.5)
        if candidate > 240 or best and candidate <= best["reference_hrmax_bpm"]:
            continue
        best = {"version": VERSION, "reference_hrmax_bpm": candidate,
                "raw_mean_bpm": mean, "offset_bpm": offset_bpm,
                "start_s": window[0][0], "end_s": window[-1][0]+1.,
                "window_seconds": WINDOW_SECONDS, "window_range_bpm": max(values)-min(values),
                "source": "RAW_MEASURED", "physiological_maximum_confirmed": False}
    return best
