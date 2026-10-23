from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.api.body_observations import LabReport, LabResult, WeightReport, lab_reports, weight_context
from apps.api.response_monitoring import build_history, latest_entries
from apps.api.response_service import save_report

DAY = date(2026, 9, 20)


def entry(kind, key, payload, revision=1):
    return {"kind": kind, "entry_key": str(key), "payload": payload, "revision": revision, "recorded_at": "2026-09-20T08:00:00Z"}


def weight(day, kg, **kwargs):
    return entry("WEIGHT", day, WeightReport(day=day, morning_kg=kg, morning_standardized=True, **kwargs).model_dump(mode="json"))


def lab(day=DAY, identity="a"*32, **kwargs):
    defaults = dict(sample_id=identity, day=day, collection_time="08:00", laboratory="Lab A", protocol="48h recovery", comparable=True,
                    results=[dict(analyte="CK", value=100, unit="U/L", sample="SERUM", reference_high=180)])
    return entry("LAB", identity, LabReport(**(defaults | kwargs)).model_dump(mode="json"))


def test_author_ratio_uses_calendar_window_current_day_and_four_real_measurements():
    rows = [weight(DAY-timedelta(days=i), 70) for i in [1, 3, 6, 7, 20]] + [weight(DAY, 69)]
    rows += [weight(DAY+timedelta(days=1), 100)]
    result = weight_context(latest_entries(rows), DAY)
    assert result["morning_count"] == 4
    assert result["morning_mean_kg"] == 69.75
    assert result["morning_ratio"] == pytest.approx(69/69.75)
    assert result["morning_change_percent"] == pytest.approx(100*(69/69.75-1))
    assert result["morning_ratio"] < 1 and result["automatic_weight"] == 0


def test_gap_never_fills_missing_days_or_reuses_old_baseline():
    old = [weight(DAY-timedelta(days=i), 70) for i in range(10, 20)]
    result = weight_context(latest_entries(old + [weight(DAY, 69)]), DAY)
    assert result["morning_count"] == 1 and result["morning_ratio"] is None
    assert result["gap_days"] == 10
    assert weight_context(latest_entries(old), DAY)["status"] == "MISSING_CURRENT"


def test_only_latest_revision_and_confirmed_morning_conditions_count():
    rows = [weight(DAY-timedelta(days=i), 70) for i in range(4)]
    correction = weight(DAY, 69)
    correction["revision"] = 2
    correction["payload"]["morning_standardized"] = False
    result = weight_context(latest_entries(rows+[correction]), DAY)
    assert result["morning_count"] == 3 and result["morning_ratio"] is None
    assert result["revision"] == 2


def test_pairs_never_cross_sessions_and_ratio_preserves_weight_gain():
    r = WeightReport(day=DAY, sessions=[
        {"session": 1, "before_kg": 70, "after_kg": 69, "comparable": True},
        {"session": 2, "before_kg": 69, "after_kg": 70, "comparable": True},
        {"session": 3, "before_kg": 71, "comparable": True},
        {"session": 4, "after_kg": 68, "comparable": True},
    ])
    c = weight_context(latest_entries([entry("WEIGHT", DAY, r.model_dump(mode="json"))]), DAY)
    assert c["paired_sessions"] == 2 and c["session_ratio"] == 1
    assert c["sessions"][1]["mass_loss_percent"] < 0
    assert c["sessions"][2]["ratio"] is None and c["sessions"][3]["ratio"] is None


def test_generic_imported_weight_does_not_silently_become_morning_measurement():
    h = build_history(entries=[], wellness=[{"date":str(DAY),"metrics":{"weight":{"value":70,"unit":"kg"}}}], activities=[], start=DAY, end=DAY, today=DAY)
    assert h["days"][0]["body_observations"]["weight"]["morning_ratio"] is None
    assert h["days"][0]["device_metrics"]["weight"]["value"] == 70


def test_laboratory_units_same_sample_molar_ratio_and_limits():
    e = lab(results=[
        dict(analyte="TESTOSTERONE", value=500, unit="ng/dL", sample="SERUM"),
        dict(analyte="CORTISOL", value=20, unit="ug/dL", sample="SERUM"),
        dict(analyte="HEMOGLOBIN", value=12, unit="g/dL", sample="WHOLE_BLOOD", reference_low=13),
    ])
    r = lab_reports(latest_entries([e]), DAY)[0]
    assert r["testosterone_cortisol_ratio"] == pytest.approx((500*.03467)/(20*27.59))
    assert r["results"][2]["normalized_value"] == 120
    assert r["results"][2]["reference_status"] == "LOW" and r["outside_reference"]


@pytest.mark.parametrize("mismatch", ["matrix", "censored", "zero"])
def test_ratio_requires_compatible_exact_positive_results(mismatch):
    results=[dict(analyte="TESTOSTERONE",value=20,unit="nmol/L",sample="SERUM"),dict(analyte="CORTISOL",value=400,unit="nmol/L",sample="SERUM")]
    if mismatch == "matrix": results[1]["sample"] = "SALIVA"
    if mismatch == "censored": results[1]["qualifier"] = "LT"
    if mismatch == "zero": results[1]["value"] = 0
    assert lab_reports(latest_entries([lab(results=results)]), DAY)[0]["testosterone_cortisol_ratio"] is None


def test_censored_abnormal_result_is_not_hidden_or_used_as_exact_baseline():
    e = lab(results=[dict(analyte="CK",value=2000,unit="U/L",sample="SERUM",qualifier="GT",reference_high=180)])
    r = lab_reports(latest_entries([e]), DAY)[0]
    assert r["outside_reference"] and r["results"][0]["baseline_count"] == 0


def test_personal_history_uses_three_distinct_prior_comparable_days_no_future_leakage():
    rows = [lab(DAY-timedelta(days=i), f"{i:032x}") for i in [10, 20, 30]]
    rows += [lab(DAY-timedelta(days=10), "d"*32), lab(DAY-timedelta(days=40), "e"*32, laboratory="Other")]
    current = lab(results=[dict(analyte="CK",value=120,unit="U/L",sample="SERUM")])
    rows += [current, lab(DAY+timedelta(days=1), "f"*32)]
    reports = lab_reports(latest_entries(rows), DAY)
    r = next(e for e in reports if e["entry_key"]==current["entry_key"])["results"][0]
    assert r["baseline_count"] == 3 and r["baseline_median"] == 100 and r["change_percent"] == pytest.approx(20)
    assert len(reports) == 6


@pytest.mark.parametrize("changed", [{"comparable":False}, {"protocol":"post-race"}, {"collection_time":"14:00"}, {"fasting":"YES"}])
def test_incompatible_context_does_not_create_a_personal_reference(changed):
    rows = [lab(DAY-timedelta(days=i), f"{i:032x}") for i in [10,20,30]]
    rows.append(lab(**changed))
    r=lab_reports(latest_entries(rows),DAY)[-1]["results"][0]
    assert r["baseline_count"] == 0 and r["change_percent"] is None


def test_old_labs_not_carried_forward_and_do_not_change_controller_or_daily_score():
    rows=[lab(DAY-timedelta(days=1),results=[dict(analyte="CK",value=500,unit="U/L",sample="SERUM",reference_high=180)])]
    kwargs=dict(wellness=[],activities=[],start=DAY-timedelta(days=1),end=DAY,today=DAY)
    old=build_history(entries=[],**kwargs)
    new=build_history(entries=rows,**kwargs)
    assert new["days"][0]["body_observations"]["context_status"]=="REVIEW_LAB_REFERENCE"
    assert new["days"][1]["body_observations"]["context_status"]=="NO_OBSERVATIONS"
    assert new["lab_reports"][0]["age_days"]==1
    for a,b in zip(old["days"],new["days"]):
        assert (a["total"],a["automatic_action"])==(b["total"],b["automatic_action"])
    assert not new["automatic_increase"] and not new["changes_recovery"]


def test_invalid_values_empty_reports_units_and_duplicates_rejected():
    with pytest.raises(ValidationError): WeightReport(day=DAY,morning_kg=float("nan"))
    with pytest.raises(ValidationError): WeightReport(day=DAY,morning_kg=True)
    with pytest.raises(ValidationError): WeightReport(day=DAY)
    with pytest.raises(ValidationError): WeightReport(day=DAY,sessions=[{"session":1,"before_kg":70}]*2)
    with pytest.raises(ValidationError): LabResult(analyte="CK",value=100,unit="mg/dL",sample="SERUM")
    with pytest.raises(ValidationError): LabResult(analyte="CK",value=True,unit="U/L",sample="SERUM")
    with pytest.raises(ValidationError): LabResult(analyte="CK",value=100,unit="U/L",sample="SERUM",reference_high=1e308)
    with pytest.raises(ValidationError): LabResult(analyte="TSAT",value=101,unit="%",sample="SERUM")
    with pytest.raises(ValidationError): lab(laboratory="   ")
    with pytest.raises(ValidationError): lab(collection_time=None)
    with pytest.raises(ValidationError): lab(results=[dict(analyte="CK",value=100,unit="U/L",sample="SERUM")]*2)


class Repository:
    saved = None
    def athlete_settings(self, alias): return SimpleNamespace(timezone="Europe/Sofia")
    def _request(self, method, path, **kwargs):
        assert method == "POST" and path=="/rpc/save_onflows_response_entry"
        self.saved=kwargs["json"]
        return {"saved":True,"revision":2}
    def _json(self, r): return r


def test_new_observations_use_existing_revision_storage_without_import_or_training_recompute():
    repo=Repository()
    now=datetime(2026,9,20,8,tzinfo=timezone.utc)
    body=WeightReport(day=DAY,morning_kg=70,expected_revision=1)
    save_report(repo,"ath-test","WEIGHT",body,"actor",now=now)
    assert repo.saved["p_key"]==str(DAY) and repo.saved["p_expected_revision"]==1
    sample=LabReport(**lab()["payload"])
    save_report(repo,"ath-test","LAB",sample,"actor",now=now)
    assert repo.saved["p_key"]==sample.sample_id and repo.saved["p_payload"]["automatic_weight"]==0
    with pytest.raises(HTTPException):save_report(repo,"ath-test","WEIGHT",WeightReport(day=DAY+timedelta(days=1),morning_kg=70),"actor",now=now)
    with pytest.raises(HTTPException):save_report(repo,"ath-test","LAB",sample.model_copy(update={"collection_time":"23:00"}),"actor",now=now)
