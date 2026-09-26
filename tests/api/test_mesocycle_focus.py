"""Mesocycle continuity, complementary recovery and shared planner/outlook intent."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine, management_service
from apps.api.management_schemas import PlanningControls
from biathlon import mesocycle_focus, planning_controls, load_progression
from tests.api.test_training_plan_engine import Repository, TODAY, NOW, profile, reference_speed


def configured(**patch):
    return profile(reentry_days=0, strength_enabled=True, race_duration_min=22,
                   planning_controls=PlanningControls(mesocycle_anchor=TODAY).model_dump(mode="json"), **patch)


def state(p, offset, period="GENERAL_PREPARATION", **kw):
    return planning_controls.resolve(p, TODAY+timedelta(days=offset), period, ["Z1", "Z3"], **kw)


def test_loading_accents_are_stable_then_rotate_and_strength_is_not_truncated_forever():
    p=configured()
    blocks=[]
    for start in (0,28,56):
        loading=[state(p,start+i)["accents"] for i in range(21)]
        assert all(v==loading[0] for v in loading)
        blocks.append(loading[0])
    assert len({tuple(v) for v in blocks})==2
    assert set().union(*(set(v) for v in blocks[:2])) == set(engine.COMPONENTS)
    assert any("STR" in v for v in blocks)
    p["strength_enabled"]=False
    assert all("STR" not in state(p,i)["accents"] for i in range(84))
    p["planning_controls"]["accent_limit"]=1
    assert len({state(p,i)["accents"][0] for i in (0,28,56)})>=2


@pytest.mark.parametrize("minutes,expected",[(4,["Z5","Z4","STR"]),(22,["Z4","Z3","STR"]),(120,["Z3","Z2","STR"])])
def test_duration_guides_specific_blocks_even_without_growth_regulator(minutes,expected):
    p=configured();p["race_duration_min"]=minutes
    assert state(p,0,"SPECIAL_PREPARATION")["accents"]==expected
    assert state(p,28,"SPECIAL_PREPARATION")["accents"]!=expected


@pytest.mark.parametrize("growth", [False, True])
def test_rotation_changes_final_load_not_only_labels_and_maintenance_has_no_loading_peak(growth):
    p=configured();p["planning_controls"]["wave"]=[.96,1.4,1.5,.78]
    if growth:p["load_progression"]={"enabled":True}
    repo=Repository();source=repo.envelope["snapshot_payload"]["load_history"]
    for a in source["activities"]:
        for z in a["zones"]:z["equivalent_time_min"]=z["raw_time_min"]*.8
    rows=engine._daily_rows(source,TODAY)
    base=planning_controls.reference(rows,TODAY)
    ctx=load_progression.context(p,source,rows,TODAY)
    values=[]
    for offset in (14,42):
        goals,focus,_=engine._goals(p,TODAY+timedelta(days=offset),"GENERAL_PREPARATION",False,{},None,2,4,rows,TODAY,False,1,ctx)
        for z in engine.COMPONENTS:
            if z not in focus:
                assert goals[z]["target_index"]<=1.1+1e-9
                assert goals[z]["target"]<=7*(1.1*(base[z]["b50"]+base[z]["c40"])-base[z]["b50"])+1e-9
        values.append(goals)
    # Same zone, same week in the wave and same history: a real difference
    # must survive the growth regulator when its mesocycle role changes.
    assert values[0]["Z1"]["target"] > 1.1*values[1]["Z1"]["target"]
    assert values[1]["Z2"]["target"] > 1.1*values[0]["Z2"]["target"]


def test_growth_ceiling_never_refills_a_deliberately_small_loading_wave():
    p=configured(load_progression={"enabled":True});p["planning_controls"]["wave"]=[.7,.8,.8,.6]
    repo=Repository();source=repo.envelope["snapshot_payload"]["load_history"]
    rows=engine._daily_rows(source,TODAY);base=planning_controls.reference(rows,TODAY)
    ctx=load_progression.context(p,source,rows,TODAY)
    s=state(p,7)
    raw=planning_controls.goals(p,s,base,{},limited=False,taper_factor=1)
    result=load_progression.apply(raw,p,s,ctx,TODAY+timedelta(days=7),"GENERAL_PREPARATION",False,False,1,base)
    requested=planning_controls.goals(p,s,base,{},limited=False,taper_factor=1)
    assert all(result[z]["target"]<=requested[z]["target"] for z in engine.COMPONENTS)


def test_manual_hybrid_and_calendar_directives_have_priority():
    p=configured();c=p["planning_controls"]
    c.update(accent_mode="MANUAL",accents=["Z5"])
    assert all(state(p,i)["accents"]==["Z5"] for i in (0,14,28,42))
    c.update(accent_mode="HYBRID",accents=["Z5"])
    assert all(state(p,i)["accents"][0]=="Z5" for i in (0,14,28,42))
    assert state(p,0)["accents"]!=state(p,28)["accents"]
    c["cycles"]=[dict(name="Треньорски блок",kind="STRESS",start_date=TODAY.isoformat(),end_date=(TODAY+timedelta(days=6)).isoformat(),accents=["Z4"],target_index=1.3,volume_factor=1.1,recovery_days=7)]
    assert state(p,2)["accents"]==["Z4"]
    assert state(p,8)["mesocycle_accents"]==["Z4"]
    assert state(p,8)["kind"]=="RECOVERY"
    assert state(p,2,"RE_ENTRY")["kind"]=="RE_ENTRY"


def test_calendar_phase_change_does_not_switch_focus_mid_loading_block():
    p=configured()
    phases={"phases":[dict(kind="GENERAL_PREPARATION",start_date=TODAY.isoformat(),end_date=(TODAY+timedelta(days=9)).isoformat()),dict(kind="SPECIAL_PREPARATION",start_date=(TODAY+timedelta(days=10)).isoformat(),end_date=(TODAY+timedelta(days=80)).isoformat())]}
    before=state(p,2,periodization=phases)
    after=state(p,15,"SPECIAL_PREPARATION",periodization=phases)
    next_block=state(p,28,"SPECIAL_PREPARATION",periodization=phases)
    assert before["accents"]==after["accents"]
    assert next_block["accents"]==["Z4","Z3","STR"]
    assert state(p,29,"TRANSITION",periodization=phases)["kind"]=="TRANSITION"


def test_recovery_selects_only_observed_underloaded_component_and_never_assumes_readiness():
    p=configured();repo=Repository();rows=engine._daily_rows(repo.envelope["snapshot_payload"]["load_history"],TODAY)
    s=state(p,21)
    r=mesocycle_focus.recovery_support(s,p,rows,TODAY)
    assert len(r["accents"])==1
    assert not set(r["accents"]) & set(s["mesocycle_accents"])
    assert r["support_requires_daily_readiness"] is True
    assert mesocycle_focus.recovery_support(s,p,rows,TODAY,limited=True)["accents"]==[]
    assert mesocycle_focus.recovery_support(s,p,rows,TODAY,taper=True)["accents"]==[]
    assert mesocycle_focus.recovery_support(s,p,[],TODAY)["accents"]==[]
    for row in rows: row["effective_load"]=10
    assert mesocycle_focus.recovery_support(s,p,rows,TODAY)["accents"]==[]
    assert s["accents"]==s["mesocycle_accents"]  # No mutation.


@pytest.mark.parametrize("growth,manual",[(False,False),(True,False),(True,True)])
def test_recovery_caps_survive_growth_manual_targets_and_extreme_wave(growth,manual):
    p=configured();p["planning_controls"]["wave"]=[1.2,1.4,1.5,1.1]
    if growth:p["load_progression"]={"enabled":True}
    if manual:p["component_targets_weekly"]={z:10000 for z in engine.COMPONENTS}
    repo=Repository();source=repo.envelope["snapshot_payload"]["load_history"]
    for a in source["activities"]:
        for z in a["zones"]:z["equivalent_time_min"]=z["raw_time_min"]*.8
    rows=engine._daily_rows(source,TODAY);base=planning_controls.reference(rows,TODAY)
    context=load_progression.context(p,source,rows,TODAY)
    frozen=deepcopy(rows)
    goals,focus,s=engine._goals(p,TODAY+timedelta(days=21),"GENERAL_PREPARATION",False,{},None,3,4,rows,TODAY,False,1,context)
    aerobic=[z for z in engine.COMPONENTS if z!="STR"]
    assert sum(goals[z]["target"] for z in aerobic)<=.78*sum(base[z]["c40"]*7 for z in aerobic)+1e-6
    for z in s["mesocycle_accents"]:assert goals[z]["target"]<=.65*base[z]["c40"]*7+1e-6
    for z in engine.COMPONENTS:
        assert not goals[z]["development"]
        assert goals[z]["target_index"]<=1
    assert rows==frozen
    assert len(focus)<=1


def test_new_components_and_missing_history_cannot_be_recovery_support():
    p=configured();rows=engine._daily_rows(Repository().envelope["snapshot_payload"]["load_history"],TODAY)
    for row in rows:
        if row["zone"] in {"STR","Z2","Z4","Z5"}:row["effective_load"]=0
    r=mesocycle_focus.recovery_support(state(p,21),p,rows,TODAY)
    assert not r["accents"]


def test_weekly_planner_and_outlook_use_same_focus_and_recovery_stays_maintenance(monkeypatch):
    monkeypatch.setattr(engine.model_service,"speed_view",reference_speed)
    p=configured();p["planning_controls"]["mesocycle_anchor"]=(TODAY-timedelta(days=21)).isoformat()
    result=engine.generate_plan(Repository(),"athlete",p,start_date=TODAY,now=NOW)
    weeks=result["long_term"]["weeks"]
    for d in result["days"]:
        week=next(w for w in weeks if w["start_date"]<=d["date"]<=w["end_date"])
        assert d["cycle"]["mesocycle_accents"]==week["cycle"]["mesocycle_accents"]
        assert d["cycle"]["accents"]==week["accents"]
        assert d["cycle"]["kind"]=="RECOVERY"
        for session in d["sessions"]:
            assert session["purpose"]!="BUILDING"
            assert "DOUBLE" not in session["method_id"]
            assert d["readiness_before"][session["zone"]]>=90
            effective=session["canonical_effective_load"]
            assert all(effective[z]<=d["load_budget"]["components"][z]["deficit_effective"]+.002 for z in engine.COMPONENTS)

@pytest.mark.parametrize('length',[2,3,4,5,6])
def test_variable_lengths_rotate_only_at_anchored_boundaries_across_years(length):
    from datetime import date
    p=configured();anchor=date(2026,12,15)
    p['planning_controls'].update(mesocycle_anchor=anchor.isoformat(),wave=[1.]*(length-1)+[.78])
    def at(n):return planning_controls.resolve(p,anchor+timedelta(days=n),'GENERAL_PREPARATION',['Z1'])
    first=at(0)
    assert all(at(n)['mesocycle_accents']==first['mesocycle_accents'] for n in range(7*length))
    assert at(7*length)['mesocycle_accents']!=first['mesocycle_accents']
    assert at(7*length)['mesocycle_start']==(anchor+timedelta(days=7*length)).isoformat()


def test_recovery_support_ranks_canonical_7_40_and_requires_all_recent_days():
    p=configured();rows=[{'date':(TODAY-timedelta(days=n)).isoformat(),'zone':z,'effective_load':20 if n>7 else 19}
                        for n in range(1,41) for z in engine.COMPONENTS]
    for r in rows:
        if r['zone']=='Z2' and r['date']>=(TODAY-timedelta(days=7)).isoformat():r['effective_load']=0
    s=state(p,21)
    assert mesocycle_focus.recovery_support(s,p,rows,TODAY)['accents']==['Z2']
    rows=[r for r in rows if not(r['zone']=='Z2' and r['date']==(TODAY-timedelta(days=1)).isoformat())]
    assert mesocycle_focus.recovery_support(s,p,rows,TODAY)['accents']!=['Z2']


def test_complementary_recovery_redistributes_instead_of_creating_extra_cycle_growth():
    p=configured();p['load_progression']={'enabled':True}
    source=Repository().envelope['snapshot_payload']['load_history']
    for a in source['activities']:
        for z in a['zones']:z['equivalent_time_min']=z['raw_time_min']*.8
    rows=engine._daily_rows(source,TODAY);ctx=load_progression.context(p,source,rows,TODAY)
    values=[]
    for i in range(4):
        g,_,s=engine._goals(p,TODAY+timedelta(days=i*7),'GENERAL_PREPARATION',False,{},None,i,4,rows,TODAY,False,1,ctx)
        values.append(g)
    assert s['accents']
    for z in load_progression.WEEKLY_Q_BOUNDS:
        expected=ctx['components'][z]['reference_q']
        assert sum(v[z]['target_weekly_q'] for v in values)/4==pytest.approx(expected)
