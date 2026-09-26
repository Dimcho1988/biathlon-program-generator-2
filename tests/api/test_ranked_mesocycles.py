from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError

from apps.api.management_schemas import PlanningControls
from biathlon import mesocycle_focus as focus, planning_controls, load_progression
from tests.api.test_mesocycle_focus import configured, state
from tests.api.test_training_plan_engine import TODAY
from tests.api.test_load_progression import observed


@pytest.mark.parametrize("minutes,zone", [(240,"Z1"),(210,"Z1"),(209,"Z2"),(135,"Z2"),(120,"Z3"),(55,"Z3"),(54,"Z4"),(22.2,"Z4"),(20,"Z4"),(19.9,"Z5"),(4,"Z5"),(None,None)])
def test_race_zone_uses_continuous_upper_edges(minutes, zone):
    assert focus.race_component({"race_duration_min":minutes}) == zone


def test_three_roles_are_distinct_and_wave_does_not_create_recovery_development():
    p = configured()
    s = state(p, 7)
    assert s["accents"] == ["Z1", "Z3", "STR"]
    assert s["component_indices"] == {"Z1":1.6,"Z3":1.5,"STR":1.2}
    assert [planning_controls.component_index(s,True,zone=z) for z in s["accents"]] == pytest.approx([1.664,1.56,1.248])
    assert focus.growth_weight(s,"STR") == pytest.approx(1/3)
    assert focus.growth_weight(s,"Z5") == pytest.approx(1/6)
    s = state(p,21)
    assert all(planning_controls.component_index(s,True,zone=z)<=.9 for z in s["accents"])


def test_rotation_and_optional_strength_and_count():
    p = configured()
    assert [state(p,n)["accents"] for n in (0,28,56)] == [["Z1","Z3","STR"],["Z2","Z3","STR"],["Z1","Z2","Z4"]]
    assert state(p,0,"SPECIAL_PREPARATION")["accents"] == ["Z4","Z3","STR"]
    assert state(p,28,"SPECIAL_PREPARATION")["accents"] == ["Z4","Z5","STR"]
    assert state(p,0,"PRECOMPETITION")["accents"] == ["Z4"]
    p["race_duration_min"] = 4
    assert state(p,28,"SPECIAL_PREPARATION")["accents"] == ["Z5","Z4","Z3"]
    p["strength_enabled"] = False
    assert "STR" not in state(p,0)["accents"]
    p["planning_controls"]["automatic_focus_count"] = 1
    assert state(p,0)["accents"] == ["Z1"]


def test_entry_covers_all_enabled_components_with_12_and_no_annual_growth():
    p = configured()
    s = state(p,0,"RE_ENTRY")
    assert len(s["accents"]) == 6
    assert set(s["component_indices"].values()) == {1.2}
    assert all(planning_controls.component_index(s,True,zone=z)==1.2 for z in s["accents"])
    assert load_progression.phase_factor("RE_ENTRY",False,load_progression.DEFAULTS) == 0


def schedule_fixture():
    p = configured()
    p["program_end"] = (TODAY+timedelta(days=140)).isoformat()
    phases = {"phases":[{"kind":kind,"start_date":(TODAY+timedelta(days=start)).isoformat(),"end_date":(TODAY+timedelta(days=end)).isoformat()}
                        for kind,start,end in [("GENERAL_PREPARATION",0,27),("SPECIAL_PREPARATION",28,83),("PRECOMPETITION",84,111)]],
              "taper_windows":[{"start_date":(TODAY+timedelta(days=105)).isoformat(),"end_date":(TODAY+timedelta(days=111)).isoformat()}]}
    return p,phases


def test_one_shock_per_phase_with_following_recovery_and_no_taper_overlap():
    p,phases = schedule_fixture()
    schedule = focus.shock_schedule(p,phases)
    assert [(s["status"],s["start_date"]) for s in schedule] == [("PLANNED",(TODAY+timedelta(days=n)).isoformat()) for n in (70,98)]
    s = state(p,70,"SPECIAL_PREPARATION",periodization=phases)
    assert s["kind"] == "STRESS" and s["automatic_shock"]
    assert list(s["component_indices"].values()) == [2,1.8,1.6]
    assert max(planning_controls.component_index(s,True,zone=z) for z in s["accents"]) == 2
    assert state(p,77,"SPECIAL_PREPARATION",periodization=phases)["kind"] == "RECOVERY"
    assert state(p,105,"PRECOMPETITION",periodization=phases)["kind"] == "RECOVERY"
    assert all(s["recovery_end"] <= s["phase_end"] for s in schedule)


def test_short_or_blocked_phase_reports_unavailable_without_forcing_shock():
    p,phases = schedule_fixture()
    phases["calendar_context"] = [{"event_type":"UNAVAILABLE","start_date":(TODAY+timedelta(days=28)).isoformat(),"end_date":(TODAY+timedelta(days=111)).isoformat()}]
    assert all(s["status"] == "UNAVAILABLE" for s in focus.shock_schedule(p,phases))
    phases["calendar_context"] = []
    phases["phases"][1]["end_date"] = (TODAY+timedelta(days=37)).isoformat()
    assert focus.shock_schedule(p,phases)[0]["status"] == "UNAVAILABLE"


def test_control_race_in_following_recovery_disqualifies_shock_slot():
    p,phases = schedule_fixture()
    phases["calendar_context"] = [{"event_type":"CONTROL_RACE","start_date":(TODAY+timedelta(days=107)).isoformat(),"end_date":(TODAY+timedelta(days=107)).isoformat()}]
    assert focus.shock_schedule(p,phases)[1]["status"] == "UNAVAILABLE"


def test_readiness_can_move_recovery_support_to_next_underloaded_component():
    p = configured()
    rows = [{"date":(TODAY-timedelta(days=n)).isoformat(),"zone":z,"effective_load":20 if n>7 else 1}
            for n in range(1,41) for z in focus.COMPONENTS]
    s = state(p,21)
    first = focus.recovery_support(s,p,rows,TODAY)["accents"][0]
    readiness = {z:100 for z in focus.COMPONENTS}; readiness[first] = 60
    next_choice = focus.recovery_support(s,p,rows,TODAY,readiness=readiness)["accents"]
    assert next_choice and first not in next_choice
    assert focus.recovery_support(s,p,rows,TODAY,readiness={})["accents"] == []


def test_light_and_background_q_progress_without_changing_frozen_reference():
    p = configured(load_progression={"enabled":True})
    p["program_end"] = (TODAY+timedelta(days=83)).isoformat()
    _,source,rows = observed()
    phases = {"phases":[{"kind":"GENERAL_PREPARATION","start_date":TODAY.isoformat(),"end_date":p["program_end"]}]}
    ctx = load_progression.context(p,source,rows,TODAY,periodization=phases)
    assert ctx["trajectory"][(TODAY+timedelta(days=20)).isoformat()]["Z5"] > 1
    assert ctx["trajectory"][(TODAY+timedelta(days=27)).isoformat()]["Z5"] > ctx["trajectory"][(TODAY+timedelta(days=20)).isoformat()]["Z5"]
    assert ctx["components"]["Z5"]["weekly_q"] == 42
    assert ctx["components"]["Z5"]["reference_q"] == 30


@pytest.mark.parametrize("patch", [{"ranked_indices":[1.2,1.6,1.5]},{"ranked_indices":[1.6,1.5,2.1]},{"shock_indices":[1.5,1.5,1.2]},{"automatic_focus_count":4}])
def test_invalid_rank_settings_are_rejected(patch):
    with pytest.raises(ValidationError):
        PlanningControls(**patch)
