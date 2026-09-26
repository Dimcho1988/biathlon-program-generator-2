"""Regression tests for coach intent versus independently permitted execution."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import load_adaptation, training_plan_engine as engine
from biathlon import load_progression as policy, planning_controls
from tests.api.test_load_progression import observed, response_fixture
from tests.api.test_mesocycle_focus import configured
from tests.api.test_training_plan_engine import TODAY


def preparation(p):
    return {"phases":[{"kind":"GENERAL_PREPARATION", "start_date":p["program_start"], "end_date":p["program_end"]}], "taper_windows":[]}


def setup():
    p=configured(load_progression={"enabled":True})
    p["program_start"]=TODAY.isoformat()
    p["planning_controls"]["mesocycle_anchor"]=TODAY.isoformat()
    _,source,rows=observed()
    return p,source,rows


@pytest.mark.parametrize("measured,reference",[(20,30),(58,58),(150,120)])
def test_approved_reference_is_the_actual_weekly_intent_not_just_a_display_value(measured,reference):
    p,source,rows=setup()
    ctx=policy.context(p,source,rows,TODAY)
    c=ctx["components"]["Z3"]
    c.update(weekly_q=measured,reference_q=reference,recent_observed_q=measured)
    base=planning_controls.reference(rows,TODAY)
    intents=[]
    for week in range(4):
        day=TODAY+timedelta(days=7*week)
        state=planning_controls.resolve(p,day,"GENERAL_PREPARATION",[])
        state["recovery_support_components"]=[]
        if week==3: state["accents"]=[]
        goals=planning_controls.goals(p,state,base,{},limited=False,taper_factor=1)
        original_target=goals["Z3"]["target"]
        goals=policy.apply(goals,p,state,ctx,day,"GENERAL_PREPARATION",False,False,1,base)
        intents.append(goals["Z3"]["target_weekly_q"])
        assert goals["Z3"]["target"]==original_target  # 7/40 is not inflated to fit Q.
    assert sum(intents)/4==pytest.approx(reference)
    assert intents[-1]<reference<max(intents)
    assert c["weekly_q"]==measured


@pytest.mark.parametrize("support",[[],["Z5"]])
def test_whole_regular_cycle_preserves_component_means_including_recovery(support):
    p,_,_=setup();p["planning_controls"]["wave"]=[.96,1.4,1.5,.78]
    for z in ("Z1","Z2","Z3","Z4","Z5"):
        shapes=[]
        for week in range(4):
            state=planning_controls.resolve(p,TODAY+timedelta(days=week*7),"GENERAL_PREPARATION",[])
            state["recovery_support_components"]=support
            if week==3: state["accents"]=support
            shapes.append(policy.cycle_shape(p,state,z))
        assert sum(shapes)==pytest.approx(4)
        assert shapes[-1]==(.9 if z in support else .65)


def test_all_general_components_receive_calendar_growth_and_shortening_does_not_compress_it():
    p,source,rows=setup()
    p["load_progression"].update(low_volume_annual_percent=25,upper_volume_annual_percent=25)
    for days in (28,108,365):
        p["program_end"]=(TODAY+timedelta(days=days-1)).isoformat()
        ctx=policy.context(p,source,rows,TODAY,periodization=preparation(p))
        for z in policy.WEEKLY_Q_BOUNDS:
            assert ctx["trajectory"][p["program_end"]][z]==pytest.approx(1.25**(days/365.25))
        assert ctx["trajectory"][p["program_end"]]["STR"]==1


def test_deliberate_reduced_wave_and_taper_are_not_refilled():
    p,source,rows=setup();p["planning_controls"]["wave"]=[.5,.7,.8,.6]
    state=planning_controls.resolve(p,TODAY+timedelta(days=14),"GENERAL_PREPARATION",[])
    assert all(policy.cycle_shape(p,state,z)<=.8 for z in policy.WEEKLY_Q_BOUNDS)
    phases=preparation(p)
    phases["taper_windows"]=[{"start_date":(TODAY+timedelta(days=7)).isoformat(),"end_date":p["program_end"]}]
    ctx=policy.context(p,source,rows,TODAY,periodization=phases)
    assert ctx["trajectory"][p["program_end"]]==ctx["trajectory"][(TODAY+timedelta(days=6)).isoformat()]


def test_positive_component_learning_increases_future_tempo_without_rewriting_the_past():
    entries,rows=response_fixture(positive=True)
    feedback=load_adaptation.assess(entries,TODAY,rows=rows)
    assert feedback["components"]["Z3"]["growth_factor"]==1.05
    assert policy.feedback_factor(feedback,"Z3","growth_factor",TODAY)==1
    assert policy.feedback_factor(feedback,"Z3","growth_factor",TODAY+timedelta(days=1))==1.05
    assert policy.feedback_factor(feedback,"Z4","growth_factor",TODAY+timedelta(days=1))==1
    p,source,actual=setup()
    anchor=policy.context(p,source,actual,TODAY)["anchor"]
    neutral=policy.context(p,source,actual,TODAY+timedelta(days=1),retained=anchor,periodization=preparation(p))
    learned=policy.context(p,source,actual,TODAY+timedelta(days=1),feedback,retained=anchor,periodization=preparation(p))
    assert learned["trajectory"][TODAY.isoformat()]==neutral["trajectory"][TODAY.isoformat()]
    assert learned["components"]["Z3"]["target_q"]>neutral["components"]["Z3"]["target_q"]
    assert learned["components"]["Z4"]["target_q"]==neutral["components"]["Z4"]["target_q"]


def test_negative_signal_wins_and_temporary_load_reduction_expires():
    assert policy.feedback_factor({"global":{"growth_factor":.75},"components":{"Z3":{"growth_factor":1.4}}},"Z3","growth_factor",TODAY)==.75
    entries,rows=response_fixture()
    feedback=load_adaptation.assess(entries,TODAY,rows=rows)
    assert policy.feedback_factor(feedback,"Z3","load_factor",TODAY+timedelta(days=1))==.9
    assert policy.feedback_factor(feedback,"Z3","load_factor",TODAY+timedelta(days=30))==1
    assert policy.feedback_factor(feedback,"Z3","growth_factor",TODAY+timedelta(days=30))==.75


def test_old_reference_migrates_without_losing_date_or_inventing_observations():
    p,source,rows=setup()
    original=policy.context(p,source,rows,TODAY)["anchor"]
    original["version"]="load-progression-v4-clamped-q"
    before=deepcopy(original)
    ctx=policy.context(p,source,rows,TODAY+timedelta(days=1),retained=original)
    assert ctx["anchor"]["version"]==policy.VERSION
    assert ctx["anchor"]["components"]==original["components"]
    assert ctx["anchor"]["created_on"]==original["created_on"]
    assert original==before


@pytest.mark.parametrize("recent_q",[None,0,2])
def test_current_exposure_is_checked_even_with_a_saved_positive_reference(recent_q):
    p,source,rows=setup()
    anchor=policy.context(p,source,rows,TODAY)["anchor"]
    start=(TODAY-timedelta(days=14)).isoformat()
    rows=[r for r in rows if r["date"]>=start]
    source["activities"]=[a for a in source["activities"] if a["date"]>=start]
    for a in source["activities"]:
        next(v for v in a["zones"] if v["zone"]=="Z5")["equivalent_time_min"]=recent_q
    ctx=policy.context(p,source,rows,TODAY,retained=anchor,periodization=preparation(p))
    assert ctx["components"]["Z5"]["weekly_q"]==anchor["components"]["Z5"]["weekly_q"]>0
    goals,_,_=engine._goals(p,TODAY,"GENERAL_PREPARATION",False,{},None,0,4,rows,TODAY,False,1,ctx)
    if recent_q is None:
        assert goals["Z5"]["target_weekly_q"] is None
        assert ctx["components"]["Z5"]["limitation"]=="NO_RELIABLE_COMPONENT_HISTORY"
    elif recent_q==0:
        assert goals["Z5"]["target_weekly_q"]==0
        assert ctx["components"]["Z5"]["limitation"]=="NO_OBSERVED_EXPOSURE"
    else:
        assert goals["Z5"]["target_weekly_q"]>0  # A full 40 days is not an additional gate.
