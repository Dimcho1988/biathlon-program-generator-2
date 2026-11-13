"""Measured body mass and laboratory context, not a validated stress score.

The author's workbook ratios are preserved, but absent observations are never
filled and unmatched pre/post measurements are never averaged together.
"""
from datetime import date, timedelta
from statistics import mean, median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .response_monitoring import InputModel

VERSION = "body-observations-v2"
MIN_MORNING_DAYS = 4  # Data coverage rule, NOT a physiological threshold.
ANALYTES = {
    "UREA": ("mmol/L", {"mmol/L": 1, "mg/dL": 0.1665}),
    "CK": ("U/L", {"U/L": 1}),
    "HEMOGLOBIN": ("g/L", {"g/L": 1, "g/dL": 10}),
    "TESTOSTERONE": ("nmol/L", {"nmol/L": 1, "ng/dL": 0.03467, "ng/mL": 3.467}),
    "CORTISOL": ("nmol/L", {"nmol/L": 1, "ug/dL": 27.59}),
    "FERRITIN": ("ug/L", {"ug/L": 1, "ng/mL": 1}),
    "CRP": ("mg/L", {"mg/L": 1, "mg/dL": 10}),
    "TSAT": ("%", {"%": 1}),
}


class Measurement(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeightPair(Measurement):
    session: int = Field(ge=1, le=4, strict=True)
    before_kg: float | None = Field(default=None, gt=0, le=500, allow_inf_nan=False, strict=True)
    after_kg: float | None = Field(default=None, gt=0, le=500, allow_inf_nan=False, strict=True)
    comparable: bool = False
    fluid_l: float | None = Field(default=None, ge=0, le=30, allow_inf_nan=False, strict=True)
    urine_l: float | None = Field(default=None, ge=0, le=30, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def has_measurement(self):
        if self.before_kg is None and self.after_kg is None:
            raise ValueError("A session needs at least one weight measurement")
        return self


class WeightReport(InputModel):
    day: date
    morning_kg: float | None = Field(default=None, gt=0, le=500, allow_inf_nan=False, strict=True)
    morning_standardized: bool = False
    sessions: list[WeightPair] = Field(default_factory=list, max_length=4)
    note: str = Field(default="", max_length=500)
    body_water_percent: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    body_fat_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    composition_method: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def valid_measurements(self):
        if self.morning_kg is None and not self.sessions:
            raise ValueError("At least one measured weight is required")
        if len({p.session for p in self.sessions}) != len(self.sessions):
            raise ValueError("Session numbers must be unique")
        return self


class LabResult(Measurement):
    analyte: Literal["UREA", "CK", "HEMOGLOBIN", "TESTOSTERONE", "CORTISOL", "FERRITIN", "CRP", "TSAT"]
    value: float = Field(ge=0, le=1e7, allow_inf_nan=False, strict=True)
    unit: str = Field(max_length=16)
    qualifier: Literal["EQ", "LT", "GT"] = "EQ"
    sample: Literal["SERUM", "PLASMA", "WHOLE_BLOOD", "SALIVA"]
    reference_low: float | None = Field(default=None, ge=0, le=1e7, allow_inf_nan=False, strict=True)
    reference_high: float | None = Field(default=None, ge=0, le=1e7, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def valid_units(self):
        if self.unit not in ANALYTES[self.analyte][1]:
            raise ValueError("Unsupported unit for this analyte")
        if self.reference_low is not None and self.reference_high is not None and self.reference_low >= self.reference_high:
            raise ValueError("Reference interval must be ordered")
        if self.analyte == "TSAT" and self.value > 100:
            raise ValueError("Transferrin saturation cannot exceed 100 percent")
        return self


class LabReport(InputModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sample_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    day: date
    collection_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    laboratory: str = Field(min_length=1, max_length=100)
    protocol: str = Field(min_length=1, max_length=100)
    comparable: bool = False
    hours_since_training: float | None = Field(default=None, ge=0, le=8760, allow_inf_nan=False, strict=True)
    fasting: Literal["YES", "NO", "UNKNOWN"] = "UNKNOWN"
    note: str = Field(default="", max_length=500)
    results: list[LabResult] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def distinct_results(self):
        if len({r.analyte for r in self.results}) != len(self.results):
            raise ValueError("An analyte can occur only once in a sample")
        if self.comparable and self.collection_time is None:
            raise ValueError("Comparable samples need a collection time")
        return self


def weight_context(selected, day):
    key = day.isoformat()
    reports = {k: e for (kind, k), e in selected.items() if kind == "WEIGHT"}
    entry = reports.get(key)
    report = entry["payload"] if entry else {}
    dates = [(day - timedelta(days=i)).isoformat() for i in range(7)]
    values = [r["morning_kg"] for d in dates if (r := reports.get(d, {}).get("payload", {}))
              and r.get("morning_standardized") and r.get("morning_kg") is not None]
    current = report.get("morning_kg") if report.get("morning_standardized") else None
    baseline = mean(values) if values else None
    ratio = current / baseline if current and len(values) >= MIN_MORNING_DAYS else None
    pairs = []
    for p in report.get("sessions", []):
        before, after = p.get("before_kg"), p.get("after_kg")
        complete = before is not None and after is not None and p.get("comparable", False)
        pairs.append({**p, "ratio": after / before if complete else None,
                      "mass_loss_percent": 100 * (before - after) / before if complete else None})
    valid = [p for p in pairs if p["ratio"] is not None]
    # Ratio of means in the workbook == ratio of sums, only for SAME paired sessions.
    session_ratio = sum(p["after_kg"] for p in valid) / sum(p["before_kg"] for p in valid) if valid else None
    prior = [d for d in reports if d < key and reports[d]["payload"].get("morning_kg") is not None]
    return {"report": report or None, "revision": entry["revision"] if entry else 0,
            "morning_mean_kg": baseline, "morning_count": len(values), "window_days": 7,
            "minimum_morning_days": MIN_MORNING_DAYS, "morning_ratio": ratio,
            "morning_change_percent": 100 * (current / baseline - 1) if ratio else None,
            "status": "AVAILABLE" if ratio is not None else "MISSING_CURRENT" if current is None else "INSUFFICIENT_HISTORY",
            "gap_days": (day - date.fromisoformat(max(prior))).days if prior else None,
            "session_ratio": session_ratio, "paired_sessions": len(valid), "sessions": pairs,
            "automatic_weight": 0, "validated": False}


def normalize_result(result):
    unit, conversions = ANALYTES[result["analyte"]]
    factor = conversions[result["unit"]]
    value = result["value"] * factor
    low = result.get("reference_low")
    high = result.get("reference_high")
    low, high = (low * factor if low is not None else None), (high * factor if high is not None else None)
    qualifier = result.get("qualifier", "EQ")
    if low is not None and ((qualifier == "EQ" and value < low) or (qualifier == "LT" and value <= low)):
        status = "LOW"
    elif high is not None and ((qualifier == "EQ" and value > high) or (qualifier == "GT" and value >= high)):
        status = "HIGH"
    else:
        status = "CENSORED" if qualifier != "EQ" else "WITHIN_PROVIDED_LIMITS" if low is not None or high is not None else "NO_REFERENCE"
    return {**result, "normalized_value": value, "normalized_unit": unit,
            "reference_status": status, "baseline_median": None, "baseline_count": 0,
            "change_percent": None}


def lab_reports(selected, as_of):
    entries = sorted([e for (kind, _), e in selected.items() if kind == "LAB" and e["payload"]["day"] <= as_of.isoformat()],
                     key=lambda e: (e["payload"]["day"], e["payload"].get("collection_time") or "", e["entry_key"]))
    output, references = [], {}
    for entry in entries:
        p = entry["payload"]
        results = [normalize_result(r) for r in p["results"]]
        for r in results:
            per_day = {}
            if p["comparable"] and r["qualifier"] == "EQ":
                signature = (p["laboratory"].casefold(), p["protocol"].casefold(), p["fasting"],
                             p["collection_time"][:2], r["analyte"], r["sample"])
                earliest = (date.fromisoformat(p["day"]) - timedelta(days=365)).isoformat()
                window = references.setdefault(signature, {})
                for stale in [d for d in window if d < earliest]:
                    del window[stale]
                per_day = {d: v for d, v in window.items() if d < p["day"]}
                window.setdefault(p["day"], []).append(r["normalized_value"])
            # Repeated same-day samples do not inflate the number of independent days.
            r["baseline_count"] = len(per_day)
            if len(per_day) >= 3:
                center = median(median(v) for v in per_day.values())
                r["baseline_median"] = center
                r["change_percent"] = 100 * (r["normalized_value"] / center - 1) if center > 0 else None
        by_name = {r["analyte"]: r for r in results}
        testosterone, cortisol = by_name.get("TESTOSTERONE"), by_name.get("CORTISOL")
        ratio = None
        if testosterone and cortisol and testosterone["sample"] == cortisol["sample"] and all(r["qualifier"] == "EQ" and r["normalized_value"] > 0 for r in (testosterone, cortisol)):
            ratio = testosterone["normalized_value"] / cortisol["normalized_value"]
        output.append({**entry, "results": results, "testosterone_cortisol_ratio": ratio,
                       "age_days": (as_of - date.fromisoformat(p["day"])).days,
                       "outside_reference": any(r["reference_status"] in ("LOW", "HIGH") for r in results),
                       "automatic_weight": 0})
    return output


def attach_body_observations(history, selected, end):
    labs = lab_reports(selected, end)
    for d in history["days"]:
        weight = weight_context(selected, date.fromisoformat(d["day"]))
        measured = [r for r in labs if r["payload"]["day"] == d["day"]]
        d["body_observations"] = {"weight": weight, "lab_sample_keys": [r["entry_key"] for r in measured],
            "context_status": "REVIEW_LAB_REFERENCE" if any(r["outside_reference"] for r in measured) else
                              "OBSERVATIONS_AVAILABLE" if weight["report"] or measured else "NO_OBSERVATIONS"}
    history["body_observations_version"] = VERSION
    history["lab_reports"] = labs
    return history
