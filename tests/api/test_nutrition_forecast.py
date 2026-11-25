from copy import deepcopy
from datetime import date, timedelta
import pytest
from fastapi import HTTPException

from apps.api import nutrition_forecast as f, energy_service as s, energy_model as m
from apps.api import nutrition_recommendations as n, assistant_context, prepared_views
from tests.api.test_nutrition_recommendations import DAY, NOW, PROFILE, week
from tests.api.test_energy import RestRepository


def plan(status="TRAINING", sessions=None):
    return {"revision": 9, "days": [{"date": str(DAY + timedelta(days=1)), "status": status,
        "sessions": sessions if sessions is not None else [{"title": "Run", "sport": "Run", "zone": "Z2",
        "total_minutes": 60, "blocks": [{"duration_min": 60, "target_hr_bpm": 140}]}]}]}


def estimate(p):
    repo = RestRepository()
    return f.estimate(p, DAY + timedelta(days=1), repo.entries[0]["payload"], repo.settings, 1800)


def test_planned_gross_energy_replaces_history_workout_and_excludes_resting_time():
    predicted = estimate(plan())
    rate = m.hr_energy_rate(.7, "FEMALE", 68, 1800/1440)
    assert predicted["activity_kcal"] == pytest.approx(rate * 60)
    assert predicted["outside_kcal"] == pytest.approx(1800 * 23/24 * 1.2)
    targets, basis = n.recommend(DAY+timedelta(days=1), DAY, week(4110), PROFILE, forecast=predicted)
    assert targets["kcal"] == pytest.approx(predicted["kcal"] + 277.5)
    assert basis["mean_expenditure_kcal"] == 3000
    assert targets["kcal"] != pytest.approx(3000 + predicted["activity_kcal"] + 277.5)
    assert sum(targets[k+"_g"] * m.FACTORS[k] for k in m.MACROS) == pytest.approx(targets["kcal"])


def test_missing_plan_and_explicit_rest_are_distinct():
    assert estimate(None) is None
    assert estimate(plan(sessions=[])) is None
    rest = estimate(plan("REST", []))
    assert rest["source"] == "PLANNED_REST" and rest["activity_kcal"] == 0
    assert rest["kcal"] == 2160
    assert f.estimate(plan(), DAY+timedelta(days=2), PROFILE, RestRepository().settings, 1800) is None


def test_repeated_work_recovery_and_warmup_account_for_actual_duration_once():
    sessions = [{"sport": "Run", "zone": "Z4", "total_minutes": 25, "blocks": [
        {"kind": "WARMUP", "duration_min": 5, "target_hr_bpm": 110},
        {"kind": "REPEAT_GROUP", "repeat_count": 4, "duration_min": 20, "sequence": [
            {"duration_min": 3, "target_hr_bpm": 180},
            {"duration_min": 2, "passive_rest": True}]}]}]
    result = estimate(plan(sessions=sessions))
    expected = 5*m.hr_energy_rate(.55, "FEMALE", 68, 1.25) + 12*m.hr_energy_rate(.9, "FEMALE", 68, 1.25) + 8*1.25
    assert result["duration_s"] == 25*60 and result["activity_kcal"] == pytest.approx(expected)
    sessions[0]["total_minutes"] = 40
    assert estimate(plan(sessions=sessions)) is None


def test_strength_uses_whole_session_met_even_when_sport_is_nordic():
    sessions = [{"sport": "NordicSki", "zone": "STR", "total_minutes": 40, "blocks": [
        {"duration_min": 10, "target_hr_bpm": 180}, {"duration_min": 30, "passive_rest": True}]}]
    result = estimate(plan(sessions=sessions))
    assert result["activity_kcal"] == pytest.approx(3.5*3.5*68/200*40)
    assert result["sessions"][0]["methods"] == ["STRENGTH_MET"]


def test_zone_and_met_fallbacks_cover_missing_planned_hr():
    sessions = [{"sport": "Run", "zone": "Z2", "total_minutes": 60}]
    result = estimate(plan(sessions=sessions))
    assert result["sessions"][0]["methods"] == ["ZONE_MIDPOINT"]
    sessions[0]["zone"] = None
    assert estimate(plan(sessions=sessions))["sessions"][0]["methods"] == ["SPORT_MET"]


def test_seven_day_range_matches_single_dates_and_never_reports_forecasts_as_actual(monkeypatch):
    saved = plan()
    saved["days"].append({"date": str(DAY+timedelta(days=2)), "status": "REST", "sessions": []})
    before = deepcopy(saved)
    monkeypatch.setattr(f, "active_plan", lambda *args: saved)
    repo = RestRepository()
    report = s.history(repo, "a", DAY+timedelta(days=1), DAY+timedelta(days=7), now=NOW)
    assert len(report["days"]) == 7 and report["forecast_until"] == str(DAY+timedelta(days=7))
    for row in report["days"]:
        selected = date.fromisoformat(row["day"])
        single = s.history(repo, "a", selected, selected, now=NOW)["days"][0]
        assert row == single
        assert row["total"] is None and row["outside"] is None and not row["activities"]
        assert row["nutrition_basis"]["window_end"] == "2026-11-19"
    assert [d["nutrition_basis"]["anchor_source"] for d in report["days"][:3]] == ["PLANNED_TRAINING", "PLANNED_REST", "HISTORICAL_MEAN"]
    assert saved == before


def test_no_recursive_forecast_donors_and_plan_can_work_without_history():
    days = week(4110)
    for i in range(8):
        days[str(DAY+timedelta(days=i))] = {"total": {"kcal": 99999}, "targets": {"kcal": 99999}}
    for i in range(1, 8):
        target, basis = n.recommend(DAY+timedelta(days=i), DAY, days, PROFILE)
        assert target["kcal"] == 3277.5 and basis["correction_kcal"] == 277.5
    forecast = estimate(plan())
    target, basis = n.recommend(DAY+timedelta(days=1), DAY, {}, PROFILE, forecast=forecast)
    assert target["kcal"] == forecast["kcal"] and basis["available_days"] == 0
    assert basis["correction_kcal"] == 0 and basis["status"] == "LIMITED_HISTORY"


def test_warm_default_report_supplies_exact_future_range_and_hides_plan_details(monkeypatch):
    monkeypatch.setattr(f, "active_plan", lambda *args: plan())
    payload = s.history(RestRepository(), "a", now=NOW)
    calls = []
    def read(repository, alias, kind, parameters):
        calls.append(parameters)
        if parameters:
            raise prepared_views.pending()
        return deepcopy(payload)
    monkeypatch.setattr(prepared_views, "read", read)
    result = assistant_context.read(RestRepository(), "a", {"can_view_recovery": True, "can_view_plan": False},
        {"kind": "energy", "start": "2026-11-21", "end": "2026-11-27"}, now=NOW)
    assert len(result["data"]["days"]) == 7
    forecast = result["data"]["days"][0]["nutrition_basis"]["forecast"]
    assert "sessions" not in forecast and "plan_revision" not in forecast
    assert payload["days"][-7]["nutrition_basis"]["forecast"]["sessions"]
    assert calls[-1] == {}
