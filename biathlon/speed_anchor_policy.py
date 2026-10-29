"""Freshness of observed HR/speed anchors shared by model consumers.

The observation window may contain forty days, but each paired relationship
needs a valid observation within the existing fourteen-day dosing horizon.
Stale observations remain diagnostic evidence; they cannot suppress the
test-based or initial fallback for current zones and prescriptions.
"""
from copy import deepcopy
from datetime import date

VERSION = "speed-anchor-freshness-v1-per-band-14d"
MAX_INDEX_AGE_DAYS = 14


def select_current_indices(indices, observation_dates, as_of):
    """Keep supported aggregates only while their own band is current.

    ``as_of`` is the caller's calibration day, including historical prior-day
    evaluation. It is never replaced with the wall-clock day. Inputs contain
    only already admitted, compatible measured HR/speed observations.
    """
    if type(as_of) is not date:
        raise ValueError("Index freshness requires a calibration date")
    selected, bands = deepcopy(indices), {}
    observed_dates, current_dates = [], []
    for zone, aggregate in indices.items():
        dates = sorted({day for day in observation_dates.get(zone, ())
                        if isinstance(day, str) and day <= as_of.isoformat()})
        last = dates[-1] if dates else None
        age = (as_of-date.fromisoformat(last)).days if last else None
        status = ("CURRENT" if age is not None and age <= MAX_INDEX_AGE_DAYS
                  else "STALE" if age is not None else "UNAVAILABLE")
        bands[zone] = {"status": status, "last_activity_date": last, "age_days": age,
                       "observed_index": aggregate.get("index"),
                       "observed_count": aggregate.get("count", 0),
                       "observed_seconds": aggregate.get("seconds", 0)}
        if last:
            observed_dates.append(last)
        if status == "CURRENT":
            current_dates.append(last)
        else:
            selected[zone] = {"index": None, "count": 0, "seconds": 0}
    return selected, {"version": VERSION, "as_of": as_of.isoformat(),
                      "max_age_days": MAX_INDEX_AGE_DAYS, "bands": bands,
                      "excluded_bands": [z for z, b in bands.items() if b["status"] == "STALE"],
                      "last_observation_date": max(observed_dates) if observed_dates else None,
                      "last_current_observation_date": max(current_dates) if current_dates else None}
