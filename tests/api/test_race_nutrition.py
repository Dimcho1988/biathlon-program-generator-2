from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from apps.api import energy_service, nutrition_recommendations, prepared_views, race_nutrition as r
from tests.api.test_energy import RestRepository
from tests.api.test_nutrition_recommendations import week, PROFILE, DAY, NOW


def event(**changes):
    return {"event_id": "race-1", "event_type": "MAIN_RACE", "name": "Маратон София",
            "start_date": str(DAY+timedelta(days=3)), "end_date": str(DAY+timedelta(days=3)), **changes}


def target(day):
    return nutrition_recommendations.recommend(day, DAY, week(), PROFILE)


def proposal(events=None, entries=None, today=DAY):
    return r.proposals(events or [event()], entries or [], today, target)


def accepted(p, status="ACCEPTED"):
    return {"kind": "NUTRITION", "entry_key": r.PREFIX+p["event_id"], "observed_date": str(DAY), "revision": 1,
            "payload": {"schema_version": r.VERSION, "status": status,
                        **{k: p[k] for k in ("event_id", "event_name", "event_fingerprint", "race_date", "days", "preview_hash")}}}


@pytest.mark.parametrize("changes,qualifies", [
    ({}, True), ({"name": "Half marathon"}, False), ({"name": "Полумаратон"}, False),
    ({"name": "Ски маратон"}, False), ({"event_type": "CAMP"}, False),
    ({"end_date": "2026-11-24"}, False),
    ({"race_course": {"expected_duration_min": [80, 110]}}, False),
    ({"race_course": {"expected_duration_min": [91, 120]}}, True),
    ({"race_course": {"expected_duration_min": [50, 60]}}, False),
])
def test_only_unambiguous_long_races_qualify(changes, qualifies):
    assert r.eligible(event(**changes), DAY) is qualifies
    assert not r.eligible(event(), DAY-timedelta(days=1))
    assert not r.eligible(event(), DAY+timedelta(days=3))


def test_loading_preserves_protein_and_balances_energy_without_lowering_adequate_intake():
    for base_kcal in (2000, 3000, 6000):
        base = energy_service.model.dietary_targets(base_kcal, 70, 0, True)
        t = r.loading_target(base, 70)
        assert t["protein_g"] == base["protein_g"]
        assert t["carbohydrate_g"] >= max(700, base["carbohydrate_g"])
        assert t["kcal"] >= base_kcal
        assert .2-1e-9 <= t["fat_energy_fraction"] <= .25+1e-9
        assert t["kcal"] == pytest.approx(4*t["protein_g"]+4*t["carbohydrate_g"]+9*t["fat_g"])


def test_two_explicit_days_and_no_implicit_acceptance():
    p = proposal()[0]
    assert p["status"] == "PENDING" and p["can_accept"]
    assert [d["day"] for d in p["days"]] == ["2026-11-21", "2026-11-22"]
    days = [{"day": "2026-11-21", "targets": {"kcal": 3000}, "nutrition_basis": {"notes": []}}]
    r.apply_accepted(days, [], [event()], DAY)
    assert days[0]["targets"] == {"kcal": 3000}


def test_accepted_exact_targets_survive_midnight_and_expire_after_dates():
    p = proposal()[0]
    entry = accepted(p)
    next_day = DAY+timedelta(days=1)
    changed_target = lambda day: (energy_service.model.dietary_targets(5000, 70, 0, True), target(day)[1])
    new = r.proposals([event()], [entry], next_day, changed_target)[0]
    assert new["days"] == p["days"]
    rows = [{"day": str(DAY+timedelta(days=i)), "targets": {"kcal": 5000}, "nutrition_basis": {"notes": []}} for i in range(5)]
    r.apply_accepted(rows, [entry], [event()], next_day)
    assert rows[1]["targets"] == p["days"][0]["targets"]
    assert rows[2]["targets"] == p["days"][1]["targets"]
    assert [rows[i]["targets"] for i in (0, 3, 4)] == [{"kcal": 5000}]*3


def test_cancellation_move_and_decline_restore_base_and_never_become_profiles():
    p = proposal()[0]
    entry = accepted(p)
    assert energy_service.profile_entries([entry]) == []
    for entries, calendar in (([entry], []), ([entry], [event(start_date="2026-11-24", end_date="2026-11-24")]),
                              ([accepted(p, "DECLINED")], [event()])):
        rows = [{"day": "2026-11-21", "targets": {"kcal": 3000}, "nutrition_basis": {"notes": []}}]
        r.apply_accepted(rows, entries, calendar, DAY)
        assert rows[0]["targets"] == {"kcal": 3000}


def test_overlapping_proposals_do_not_stack_and_prevent_second_acceptance():
    first = event()
    second = event(event_id="race-2")
    p = proposal()[0]
    previews = proposal([first, second], [accepted(p)])
    assert previews[0]["status"] == "ACCEPTED"
    assert previews[1]["can_accept"] is False


def test_missing_or_minor_profile_blocks_loading_without_fake_numbers():
    for profile in ({**PROFILE, "birth_year": 2010}, {**PROFILE, "weight_kg": None}):
        proposals = r.proposals([event()], [], DAY, lambda day: nutrition_recommendations.recommend(day, DAY, week(), profile))
        assert not proposals[0]["can_accept"] and not proposals[0]["days"]


def test_reminder_read_is_lightweight_and_dismissal_is_event_specific():
    repo = RestRepository()
    repo.athlete_planning_calendar = lambda alias: {"events": [event()]}
    repo.active_activity_calendar = lambda *args: pytest.fail("Reminder must not integrate energy")
    assert len(r.reminders(repo, "a", NOW)["events"]) == 1
    repo.entries.append(accepted(proposal()[0], "DECLINED"))
    assert not r.reminders(repo, "a", NOW)["events"]
    repo.athlete_planning_calendar = lambda alias: {"events": [event(name="Маратон Пловдив")]}
    assert len(r.reminders(repo, "a", NOW)["events"]) == 1


def test_history_exposes_proposals_and_approved_targets_to_same_report():
    repo = RestRepository()
    repo.athlete_planning_calendar = lambda alias: {"events": [event()]}
    first = energy_service.history(repo, "a", now=NOW)
    p = first["race_preparation"][0]
    repo.entries.append(accepted(p))
    second = energy_service.history(repo, "a", now=NOW)
    loading_day = next(d for d in second["days"] if d["day"] == p["days"][0]["day"])
    assert loading_day["targets"] == p["days"][0]["targets"]
    assert loading_day["nutrition_basis"]["race_loading"]
    assert not second["days"][-1]["nutrition_basis"].get("race_loading")
    assert len(second["profiles"]) == 1


def test_save_rechecks_preview_date_event_revision_and_writes_only_canonical_targets(monkeypatch):
    repo = RestRepository()
    repo.athlete_planning_calendar = lambda alias: {"events": [event()]}
    p = proposal()[0]
    monkeypatch.setattr(prepared_views, "read", lambda *args: {"race_preparation": [p]})
    calls = []
    monkeypatch.setattr(r.ResponseStore, "save", lambda *args: calls.append(args) or {"saved": True})
    body = r.Decision(event_id=p["event_id"], preview_hash=p["preview_hash"], action="ACCEPTED")
    assert r.save_decision(repo, "a", body, "actor", NOW)["saved"]
    assert calls[0][5]["days"] == p["days"]
    assert calls[0][2:5] == ("NUTRITION", "race-loading:race-1", str(DAY))
    for invalid in (body.model_copy(update={"expected_revision": 2}), body.model_copy(update={"preview_hash": "0"*64})):
        with pytest.raises(HTTPException) as exc:
            r.save_decision(repo, "a", invalid, "actor", NOW)
        assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        r.save_decision(repo, "a", body, "actor", NOW+timedelta(days=1))
    repo.athlete_planning_calendar = lambda alias: {"events": []}
    with pytest.raises(HTTPException):
        r.save_decision(repo, "a", body, "actor", NOW)
    assert len(calls) == 1
