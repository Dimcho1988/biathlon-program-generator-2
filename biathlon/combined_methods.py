"""Two complete method parts may share a slot to cover remaining components."""
from copy import deepcopy
from hashlib import sha256
import math

VERSION = 'combined-methods-v4-component-opportunities'


def opportunities(remaining, nominal, available_slots):
    """Candidate generation follows unmet components, not a shortage proof.

    Counts describe independent reference doses. They are not a feasibility
    claim: shared preparation, roles, Recovery and time are checked on each
    complete candidate by the common allocator.
    """
    needs = {z: math.ceil(max(0., amount-.001)/nominal[z]) for z, amount in remaining.items()
             if amount > .001 and nominal.get(z, 0.) > .001}
    return {'required_separate_sessions': sum(needs.values()),
            'available_sessions': max(0, available_slots), 'sessions_by_component': needs,
            'consider_combinations': len(set(needs)-{'STR'}) > 1,
            'basis': 'UNFINISHED_COMPONENTS_WITH_COMPLETE_METHOD_REFERENCES'}


def candidates(candidates, evidence):
    if not evidence.get('consider_combinations', len(evidence['sessions_by_component']) > 1):
        return []
    # Bound branching by actual component and execution shape, not renamed IDs.
    groups = {}
    for sport, m in sorted(candidates, key=lambda item: not bool(item[1].get("method_library"))):
        if (m['zone'] == 'STR' or m['purpose'] not in {'BUILDING', 'MAINTENANCE'} or m.get('mixed_component')
            or m.get('double_threshold') or m.get('neuromuscular_profile') or m.get('race_specific')
            or m['structure'] not in {'CONTINUOUS', 'MODEL_INTERVALS', 'METABOLIC_INTERVALS',
                                      'LIBRARY_INTERVALS', 'LIBRARY_CONTINUOUS', 'THRESHOLD_REPETITIONS'}):
            continue
        if m['zone'] not in evidence['sessions_by_component']:
            continue
        key = (sport, m['zone'])
        choices = groups.setdefault(key, [])
        def shape(method):
            recipe = (method.get('method_library') or {}).get('resolution') or {}
            interval = method.get('interval_template') or method.get('interval_profile') or {}
            return (method['structure'], recipe.get('cycle_work_seconds'), recipe.get('sets'),
                    tuple(recipe.get('work_options_seconds') or ()), interval.get('work_seconds'), interval.get('recovery_seconds'))
        if len(choices) < 3 and not any(shape(p) == shape(m) for p in choices):
            choices.append(m)
    result = []
    for (sport, zone), parents in groups.items():
        for (other_sport, other_zone), others in groups.items():
            if sport != other_sport or zone <= other_zone:
                continue
            for first in parents:
                for second in others:
                    common = set(first['periods']) & set(second['periods'])
                    if not common:
                        continue
                    result.append((sport, {'id': "COMBO-"+sha256((first["id"]+"|"+second["id"]).encode()).hexdigest()[:20],
                        'title': first['title']+' + '+second['title'], 'zone': zone,
                        'sports': (sport,), 'periods': tuple(sorted(common)), 'purpose': 'BUILDING',
                        'structure': 'COMBINED_METHODS', 'position': first['position'],
                        'combined_methods': deepcopy([first, second]), 'combination_shortage': deepcopy(evidence),
                        'min_work_min': .5, 'max_work_min': first['max_work_min'],
                        'warmup_min': max(first['warmup_min'], second['warmup_min']),
                        'cooldown_min': max(first['cooldown_min'], second['cooldown_min']),
                        'source_id': 'TWO_REGISTRY_METHODS', 'source_version': VERSION,
                        'instructions': 'Две основни части според остатъка до целта за всеки компонент, всяка в собствения си ролеви бюджет. Запази отделните скорости, цели отсечки и почивки. Общата загрявка и разпускане се отчитат веднъж.',
                        'adaptation': 'Комбинация за разпределение на оставащите цели по компоненти. Recovery намалява всяка част веднъж; общият Q/E товар се проверява отделно.'}))
    return result
