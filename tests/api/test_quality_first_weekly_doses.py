"""Component intent, whole repetitions and exclusive preparation reserves."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from biathlon import adaptive_methods, combined_methods
from tests.api.test_methodology_completion import parents
from tests.api.test_planning_continuity import ledger, reference, headroom
from tests.api.test_training_plan_engine import Repository


@pytest.mark.parametrize('requested,ceiling,expected', [(7.4,12.,9.), (7.4,8.99,6.), (6.,12.,6.)])
def test_upper_repetition_respects_the_actual_interval_and_hard_ceiling(requested, ceiling, expected):
    method = {'structure':'MODEL_INTERVALS', 'interval_template':{'work_seconds':180.}}
    assert engine._whole_work_at_most(method, requested, ceiling) == pytest.approx(expected)


def test_upper_source_variant_preserves_nonuniform_distance_cycles():
    method = {'structure':'LIBRARY_INTERVALS', 'method_library':{'resolution':{
        'structure':'SEQUENCE', 'work_options_seconds':[600.,960.,1200.]}}}
    assert engine._whole_work_at_most(method, 12., 18.) == 16.
    assert engine._whole_work_at_most(method, 12., 15.) == 10.


def test_rounded_building_dose_still_rejects_extra_repetitions_and_lower_recovery():
    method = {'id':'TEST-Z4', 'zone':'Z4', 'structure':'MODEL_INTERVALS',
              'min_work_min':3., 'max_work_min':30., 'interval_template':{
                  'work_seconds':180., 'recovery_seconds':90., 'total_capacity_ratio':2.5}}
    evidence = {'capacity_minutes':12., 'readiness_policy':{'observed_dose_factor':.8}}
    profile = {'building_fraction':.65}
    fraction = engine._nominal_fraction(method, 'BUILDING', profile, 'GENERAL_PREPARATION', evidence, {})
    assert evidence['whole_role_work_minutes'] == 18.
    evidence.update(base_fraction=fraction, base_max_dose_fraction=.65)
    session = {'zone':'Z4', 'purpose':'BUILDING', 'dose_evidence':evidence,
               'blocks':[{'kind':'WORK','zone':'Z4','duration_min':18.}]}
    assert engine._locked_dose_fits(session, profile, {'Z1':100.,'Z4':80.})
    assert not engine._locked_dose_fits(session, profile, {'Z1':100.,'Z4':50.})
    assert not engine._dose_fits([{**session['blocks'][0], 'duration_min':21.}], evidence, 'Z4', .65*.9)


def test_combination_meets_unequal_component_needs_without_halving_or_doubling_recovery():
    _, method = combined_methods.candidates([('Run',m) for m in parents()],
        {'shortage':True, 'sessions_by_component':{'Z2':1,'Z3':1}})[0]
    method['combined_work_targets'] = {'Z3':35., 'Z2':55.}
    caps = [{'capacity_minutes':100., 'target_speed_kmh':None,
             'target_hr_bpm':155. if p['zone']=='Z3' else 143.} for p in method['combined_methods']]
    policy = adaptive_methods.readiness_policy(method, {}, {'Z1':100.,'Z2':80.,'Z3':80.})
    evidence = {**caps[0], 'combined_capacities':caps, 'readiness_policy':policy}
    original = deepcopy(method)
    fraction = engine._nominal_fraction(method, 'BUILDING', {'building_fraction':.65},
                                        'GENERAL_PREPARATION', evidence, {})
    blocks = engine._blocks(method, 35., evidence, Repository().settings)
    assert [sum(b['duration_min'] for b in blocks if b['kind']=='WORK' and b.get('combination_part')==i)
            for i in (1,2)] == [35.,55.]
    assert len([b for b in blocks if b['kind']=='WARMUP']) == 1
    assert len([b for b in blocks if b['kind']=='COOLDOWN']) == 1
    assert engine._dose_fits(blocks, evidence, 'Z3', fraction*.9)
    too_much = deepcopy(blocks)
    next(b for b in too_much if b['kind']=='WORK' and b['combination_part']==2)['duration_min'] = 60.
    assert not engine._dose_fits(too_much, evidence, 'Z3', fraction*.9)
    assert method['combined_work_targets'] == original['combined_work_targets']


def test_combination_cannot_hide_an_undersized_second_component():
    _, method = combined_methods.candidates([('Run',m) for m in parents()],
        {'shortage':True, 'sessions_by_component':{'Z2':1,'Z3':1}})[0]
    method['combined_work_targets'] = {'Z3':35., 'Z2':5.}
    method['combined_work_limits'] = {'Z3':40., 'Z2':5.5}
    caps = [{'capacity_minutes':100., 'target_speed_kmh':None, 'target_hr_bpm':150.}
            for _ in method['combined_methods']]
    evidence = {**caps[0], 'combined_capacities':caps,
        'readiness_policy':adaptive_methods.readiness_policy(method, {}, {'Z1':100.,'Z2':100.,'Z3':100.})}
    engine._nominal_fraction(method, 'BUILDING', {'building_fraction':.65}, 'GENERAL_PREPARATION', evidence, {})
    assert engine._minimum_work(method, evidence, Repository().settings) is None


def test_quality_extra_cannot_be_spent_on_easy_work_or_refilled_by_replanning():
    rows = ledger(actual=0., planned=100.)
    refs = {'Z1':reference('Z1'), 'Z2':reference('Z2')}
    rows['Z2'].update(planned=0., planned_q=0., remaining=100.)
    rows['Z3'].update(planned=0., planned_q=0., remaining=100.)
    quality = headroom(rows, preparation=True, references=refs)
    easy = headroom(rows, primary='Z1', maintenance=True, references=refs)
    endurance_prep = headroom(rows, primary='Z2', preparation=True, references=refs)
    assert easy['q']['Z1'] == endurance_prep['q']['Z1'] == 25.
    assert quality['q']['Z1'] == 89.5
    rows['Z1'].update(planned=120., planned_q=120.)
    assert headroom(rows, preparation=True, references=refs)['q']['Z1'] == 69.5
    rows['Z1'].update(actual=120., actual_q=120., planned=0., planned_q=0.)
    assert headroom(rows, preparation=True, references=refs)['q']['Z1'] == 69.5
