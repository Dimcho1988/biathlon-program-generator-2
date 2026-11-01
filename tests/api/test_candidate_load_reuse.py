"""Cached candidate arithmetic matches direct Q/E for distinct real dose inputs."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import random

from apps.api import training_plan_engine as engine
from tests.test_unified_zone_load import BOUNDS


def test_cached_trial_loads_preserve_full_arithmetic_and_do_not_share_mutable_results():
    settings=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=200)
    day=date(2026,11,1)
    technical={z:90. for z in engine.COMPONENTS}
    cached=engine._day_candidate_loads(settings,[],day,technical)
    rng=random.Random(20261101)
    for _ in range(100):
        blocks=[{'session_index':rng.choice([1,2]),'zone':rng.choice(engine.COMPONENTS),
                 'kind':rng.choice(['WORK','PREPARATION','RECOVERY']),
                 'duration_min':rng.uniform(.5,40.),'target_hr_bpm':rng.choice([None,130.,160.,190.]),
                 'passive_rest':rng.choice([False,False,True]),'load_reference_hr_bpm':170.,
                 'sport_hr_offset_bpm':rng.choice([0.,-7.]),'load_sport':'Run'} for _ in range(4)]
        capacities={z:rng.uniform(20.,200.) for z in engine.COMPONENTS if z!='STR'}
        original=deepcopy(blocks)
        expected=engine._candidate_load(blocks,settings,[],day,technical_reference=technical,zone_tmax_minutes=capacities)
        actual=cached(blocks,capacity={'zone_tmax_minutes':capacities})
        assert actual==expected
        actual[0]['Z1']=-999.;actual[1]['Z2']=-999.;actual[2]['Z3']=-999.
        assert cached(blocks,capacity={'zone_tmax_minutes':capacities})==expected
        assert blocks==original
