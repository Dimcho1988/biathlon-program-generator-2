"""Rebuild synthetic dashboard data with the actual Python estimator.

Run from repository root: python -m scripts.generate_response_fixture
No athlete data, network, credentials or database writes.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from apps.api.response_monitoring import FIELDS, build_history


def fixture():
    end = date(2026, 9, 9)
    entries, wellness, activities, ti = [], [], [], []

    def add(kind, key, payload):
        entries.append({"kind": kind, "entry_key": str(key), "payload": payload, "revision": 1,
                        "recorded_at": f"{payload.get('day', key)}T08:00:00Z"})

    for i in range(56):
        d = end-timedelta(days=55-i)
        key = d.isoformat()
        visible_index = i-42
        level = [2, 2, 3, 3, 4, 4, 3, 3, 2, 2, 2, 2, 2, 2][visible_index] if visible_index >= 0 else 2
        if visible_index != 7:
            add("DAILY", key, {"day": key, "observed_at": None, **{f: level for f in FIELDS}, "competition_motivation": level,
                               "sleep_hours": 8-(level-2)*.4, "pain_or_illness": False, "note": ""})
        wellness.append({"date": key, "metrics": {} if visible_index in (7, 9) else {
            "resting_hr": {"value": 50+(level-2)*3, "unit": "bpm"}, "hrv": {"value": 65-(level-2)*6, "unit": "ms"},
            "sleep_duration": {"value": (8-(level-2)*.4)*3600, "unit": "s"}}})
        if visible_index not in (7, 8):
            add("WEIGHT", key, {"day": key, "morning_kg": 70-(level-2)*.35, "morning_standardized": True,
                                "body_water_percent": 60., "body_fat_percent": 12., "composition_method": "Примерен уред", "note": "",
                                "sessions": [{"session": 1, "before_kg": 70., "after_kg": 69.6-(level-2)*.2, "comparable": True, "fluid_l": .5, "urine_l": None}]})
        ref = f"act_{i+1:032x}"
        activities.append({"activity_ref": ref, "local_date": key, "sport": "NordicSki", "name": "Примерна тренировка с ролкови ски",
                           "elapsed_time_s": 4500, "canonical_summary": {"zones": [{"zone": f"Z{z+1}", "equivalent_time_s": v} for z, v in enumerate([1000, 2000, 1000, 200, 0])]}})
        add("SESSION", ref, {"day": key, "rpe": 4+(level-2), "timing": "DELAYED", "duration_minutes": 75., "note": "",
                             "planned_duration_minutes": 75., "planned_speed_kmh": 15., "executed_speed_kmh": 15-(level-2)*.4,
                             "execution_comparable": True, "execution_reason": "FATIGUE" if level > 2 else "AS_PLANNED"})
        ti.append({"activity_ref": ref, "local_date": key, "sport": "NordicSki", "index": {
            "comparison_key": "synthetic-v2", "admission": {"status": "ACCEPTED"},
            "general": {"valid": True, "index": .05*(1+(level-2)*.04)}}})
        if i in (6, 13, 20, 27, 34, 42, 46, 55):
            values = [("CK", 120+60*(level-2), "U/L", "SERUM"), ("UREA", 5.+.5*(level-2), "mmol/L", "SERUM"),
                      ("TESTOSTERONE", 20.-(level-2), "nmol/L", "SERUM"), ("CORTISOL", 400.+20*(level-2), "nmol/L", "SERUM"),
                      ("HEMOGLOBIN", 150.-(level-2), "g/L", "WHOLE_BLOOD"), ("FERRITIN", 70.-2*(level-2), "ug/L", "SERUM"),
                      ("TSAT", 30.-(level-2), "%", "SERUM"), ("CRP", 1.+.2*(level-2), "mg/L", "SERUM")]
            add("LAB", f"{i+1:032x}", {"sample_id": f"{i+1:032x}", "day": key, "collection_time": "08:00", "laboratory": "Примерна лаборатория", "protocol": "Сутрешен пример", "comparable": True, "fasting": "YES", "hours_since_training": 24., "note": "Синтетични данни",
                 "results": [{"analyte": a, "value": v, "unit": u, "sample": s, "qualifier": "EQ", "reference_low": None, "reference_high": 200. if a == "CK" else None} for a, v, u, s in values]})
            add("TEST", f"test-{i}", {"day": key, "protocol": "Примерен контролен тест", "protocol_version": "1", "unit": "s", "direction": "LOWER", "conditions": "Еднакво трасе", "comparable": True, "value": 600.+(level-2)*6, "meaningful_change_percent": 1., "components": ["Z3"]})
    # The gap day demonstrates a partial assessment without fabricating zeros.
    result = build_history(entries=entries, wellness=wellness, activities=activities, trainability=ti,
                           start=end-timedelta(days=13), end=end, today=end)
    result.update(timezone="Europe/Sofia", revision=None, generation_id=None)
    return result


if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "apps/web/lib/response-fixture.json"
    path.write_text(json.dumps(fixture(), ensure_ascii=False, separators=(",", ":"))+"\n")
