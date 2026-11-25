from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from apps.api import energy_model as m, energy_service as s, nutrition_recommendations as n
from tests.api.test_energy import RestRepository


DAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
PROFILE = {"weight_kg": 70, "birth_year": 1990, "allergies": "няма", "preferences": "без месо"}


def week(last=3000):
    # The last day participates in a mean of exactly 3000, as agreed.
    return {(DAY - timedelta(days=7-i)).isoformat(): {
        "total": m.grams(m.allocate(last if i == 6 else (21000-last)/6, .6, 150)),
    } for i in range(7)}


@pytest.mark.parametrize("last,target,correction", [(4110, 3277.5, 277.5), (1890, 2722.5, -277.5), (3000, 3000, 0)])
def test_quarter_is_symmetric_and_uses_expenditure_not_previous_targets(last, target, correction):
    days = week(last)
    for row in days.values():
        row["targets"] = {"kcal": 99999}
    before = deepcopy(days)
    targets, basis = n.recommend(DAY, DAY, days, PROFILE)
    assert basis["available_days"] == 7
    assert basis["mean_expenditure_kcal"] == pytest.approx(3000)
    assert basis["correction_kcal"] == pytest.approx(correction)
    assert targets["kcal"] == pytest.approx(target)
    assert sum(targets[k+"_g"] * m.FACTORS[k] for k in m.MACROS) == pytest.approx(target)
    assert targets["protein_g_per_kg"] == pytest.approx(min(1.9, max(1.6, 1.6+.0075*(target/70-40))))
    assert days == before


def test_intake_fat_is_fixed_independently_of_oxidation():
    days = {
        "2026-11-18": {"total": m.grams(m.allocate(1000, .2, 0))},
        "2026-11-19": {"total": m.grams(m.allocate(3000, .8, 0))},
    }
    targets, basis = n.recommend(DAY, DAY, days, PROFILE)
    assert basis["nonprotein_carbohydrate_fraction"] is None
    assert targets["fat_g"] * 9 == pytest.approx(targets["kcal"] * .25)
    assert targets["carbohydrate_g"] * 4 == pytest.approx(targets["kcal"] * .75 - targets["protein_g"] * 4)


def test_missing_days_are_not_zero_and_missing_last_day_is_not_an_older_donor():
    days = week(4110)
    days["2026-11-19"]["total"] = None
    days["2026-11-19"]["accounted"] = {"kcal": 9000}
    targets, basis = n.recommend(DAY, DAY, days, PROFILE)
    assert basis["status"] == "LIMITED_HISTORY"
    assert basis["available_days"] == 6
    assert basis["missing_days"] == ["2026-11-19"]
    assert basis["last_day_expenditure_kcal"] is None
    assert basis["correction_kcal"] == 0
    assert targets["kcal"] == pytest.approx((21000-4110)/6)


def test_no_history_has_only_protein_fallback_and_no_zero_expenditure():
    targets, basis = n.recommend(DAY, DAY, {}, PROFILE)
    assert targets["kcal"] is None
    assert targets["carbohydrate_g"] is None and targets["fat_g"] is None
    assert targets["protein_g"] == 126
    assert basis["mean_expenditure_kcal"] is None
    assert basis["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("profile", [{**PROFILE, "weight_kg": None}, {**PROFILE, "birth_year": 2009}, {**PROFILE, "birth_year": None}])
def test_missing_body_or_possible_minor_cannot_receive_quantitative_diet(profile):
    assert n.recommend(DAY, DAY, week(), profile)[0] is None


def test_today_and_tomorrow_exclude_unfinished_day_and_roll_forward_after_midnight():
    days = week(4110)
    days[DAY.isoformat()] = {"total": None, "accounted": {"kcal": 200}}
    today_target, _ = n.recommend(DAY, DAY, days, PROFILE)
    tomorrow = DAY + timedelta(days=1)
    target, basis = n.recommend(tomorrow, DAY, days, PROFILE)
    assert target == today_target
    assert basis["provisional"] is True
    assert basis["window_end"] == "2026-11-19"
    days[DAY.isoformat()]["total"] = m.grams(m.allocate(5000, .7, 200))
    target, basis = n.recommend(tomorrow, tomorrow, days, PROFILE)
    mean = ((21000-4110)/6*5 + 4110 + 5000) / 7
    assert target["kcal"] == pytest.approx(mean+.25*(5000-mean))
    assert basis["window_start"] == "2026-11-14"
    assert basis["provisional"] is False


def test_one_day_and_range_requests_have_identical_targets_and_load_prior_week():
    repo = RestRepository()
    original = repo.active_activity_calendar
    requests = []
    def calendar(alias, start, end):
        requests.append((start, end))
        result = original(alias, start, end)
        result["activities"] = [{"activity_ref": "strength", "sport": "WeightTraining",
            "local_date": "2026-11-19", "elapsed_time_s": 3600, "start_at_utc": "2026-11-19T09:00:00Z"}]
        return result
    repo.active_activity_calendar = calendar
    single = s.history(repo, "a", DAY, DAY, now=NOW)["days"][0]
    broader = s.history(repo, "a", DAY-timedelta(days=20), DAY, now=NOW)["days"][-1]
    assert single["targets"] == broader["targets"]
    assert single["nutrition_basis"] == broader["nutrition_basis"]
    assert requests[0][0] == DAY-timedelta(days=8)
    assert single["nutrition_basis"]["correction_kcal"] > 0
    assert single["total"] is None  # current expenditure stays incomplete
    assert single["targets"]["kcal"] is not None


def test_future_day_is_target_only_with_no_reported_expenditure_or_activity():
    repo = RestRepository()
    tomorrow = DAY+timedelta(days=1)
    data = s.history(repo, "a", tomorrow, tomorrow, now=NOW)
    d = data["days"][0]
    assert len(data["days"]) == 1
    assert d["status"] == "FORECAST"
    assert d["outside"] is None and d["total"] is None
    assert d["activities"] == []
    assert all(v is None for v in d["energy_sources"].values())
    assert d["targets"]["kcal"] == 2160
    assert d["nutrition_basis"]["window_end"] == "2026-11-19"
    assert d["nutrition_basis"]["available_days"] == 7
    default_day = next(r for r in s.history(repo, "a", now=NOW)["days"] if r["day"] == str(tomorrow))
    assert d["nutrition_basis"] == default_day["nutrition_basis"]
    assert d["targets"] == default_day["targets"]
    assert "nutrition_basis" in data["menu_guidance"]
    assert data["nutrition_version"] == n.VERSION
    with pytest.raises(HTTPException) as exc:
        s.history(repo, "a", tomorrow, DAY+timedelta(days=8), now=NOW)
    assert exc.value.status_code == 422


def test_first_day_of_source_history_does_not_invent_prior_rest_days():
    data = s.history(RestRepository(), "a", date(2026,10,1), date(2026,10,2), now=NOW)
    first, second = data["days"]
    assert first["total"] is not None
    assert first["targets"]["kcal"] is None
    assert first["nutrition_basis"]["available_days"] == 0
    assert second["targets"]["kcal"] == 2160
    assert second["nutrition_basis"]["available_days"] == 1


def test_preferences_and_body_inputs_follow_target_date():
    repo = RestRepository()
    repo.entries[0]["payload"].update(preferences="без месо", allergies="ядки")
    repo.entries.append({"kind": "NUTRITION", "observed_date": "2026-11-19", "revision": 2,
        "payload": {**repo.entries[0]["payload"], "weight_kg": 70, "preferences": "без риба", "allergies": "мляко"}})
    data = s.history(repo, "a", date(2026,11,18), DAY, now=NOW)
    assert data["days"][0]["nutrition_basis"]["food_preferences"] == {"preferences":"без месо", "allergies":"ядки"}
    assert data["days"][-1]["nutrition_basis"]["food_preferences"] == {"preferences":"без риба", "allergies":"мляко"}
    assert data["days"][-1]["nutrition_basis"]["weight_kg"] == 70


def test_assistant_reads_same_future_targets_basis_preferences_and_explicit_history(monkeypatch):
    from apps.api import assistant_context, prepared_views
    repo = RestRepository()
    payload = s.history(repo, "a", DAY+timedelta(days=1), DAY+timedelta(days=1), now=NOW)
    calls = []
    def read(repository, alias, kind, parameters):
        calls.append((alias, kind, parameters))
        return payload
    monkeypatch.setattr(prepared_views, "read", read)
    access = {"can_view_recovery": True, "can_view_plan": False}
    result = assistant_context.read(repo, "authorized", access, {"kind":"energy", "date":"2026-11-21"}, now=NOW)
    assert calls == [("authorized", "energy", {"period_start":"2026-11-21", "period_end":"2026-11-21"})]
    assert result["data"]["days"][0]["targets"] == payload["days"][0]["targets"]
    assert result["data"]["days"][0]["nutrition_basis"] == payload["days"][0]["nutrition_basis"]
    result = assistant_context.read(repo, "authorized", access,
        {"kind":"energy", "date":"2026-11-21", "section":"days.0.nutrition_basis.history"}, now=NOW)
    assert result["data"] == payload["days"][0]["nutrition_basis"]["history"]
    with pytest.raises(HTTPException) as exc:
        assistant_context.read(repo, "a", access, {"kind":"energy", "date":"2026-11-28"}, now=NOW)
    assert exc.value.status_code == 422
