"""Two complete method parts share a slot only after a documented slot shortage."""
from copy import deepcopy
from hashlib import sha256
import math

VERSION = 'combined-methods-v1-half-role-budgets'


def shortage(remaining, nominal, available_slots, *, vectors=None):
    needs = {z: math.ceil(max(0., amount-.001)/nominal[z]) for z, amount in remaining.items()
             if amount > .001 and nominal.get(z, 0.) > .001}
    result = {'required_separate_sessions': sum(needs.values()), 'available_sessions': max(0, available_slots),
              'sessions_by_component': needs, 'basis': 'RELAXED_STANDALONE_SESSION_COVERAGE_LOWER_BOUND'}
    if vectors is None:
        vectors = [{z: value} for z, value in nominal.items() if value > .001]
    components = [z for z, amount in remaining.items() if amount > .001]
    if not components or not vectors or any(not any(v.get(z, 0.) > .001 for v in vectors) for z in components):
        return {**result, 'shortage': False, 'reason': 'NO_COMPLETE_STANDALONE_COVERAGE_REFERENCE'}
    from scipy.optimize import linprog
    # Relax repetitions, timing and Recovery. This can only underestimate the
    # number of full-strength independent sessions required. Warmup, recovery
    # and multi-component source recipes contribute to all their components.
    solved = linprog([1.]*len(vectors), A_ub=[[-v.get(z, 0.) for v in vectors] for z in components],
                     b_ub=[-remaining[z] for z in components], bounds=(0, None), method='highs')
    if not solved.success:
        return {**result, 'shortage': False, 'reason': 'STANDALONE_COVERAGE_UNRESOLVED'}
    required = math.ceil(solved.fun - 1e-7)
    return {**result, 'required_separate_sessions': required, 'relaxed_lower_bound': float(solved.fun),
            'shortage': required > available_slots, 'reason': 'SESSION_SHORTAGE_PROVEN_BY_LOWER_BOUND' if required > available_slots else 'SEPARATE_SESSIONS_NOT_RULED_OUT'}


def candidates(candidates, evidence):
    if not evidence['shortage']:
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
        if len(choices) < 2 and not any(p['structure'] == m['structure'] for p in choices):
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
                        'min_work_min': .5, 'max_work_min': first['max_work_min']/2,
                        'minimum_fraction': first.get('minimum_fraction', .25)/2,
                        'warmup_min': max(first['warmup_min'], second['warmup_min']),
                        'cooldown_min': max(first['cooldown_min'], second['cooldown_min']),
                        'source_id': 'TWO_REGISTRY_METHODS', 'source_version': VERSION,
                        'instructions': 'Две основни части, всяка с половината от собствения си ролеви бюджет. Запази отделните скорости, цели отсечки и почивки. Общата загрявка и разпускане се отчитат веднъж.',
                        'adaptation': 'Комбинация поради недостиг на разрешени сесии. Recovery намалява всяка част веднъж; общият Q/E товар се проверява отделно.'}))
    return result
