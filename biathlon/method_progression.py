"""Replay confirmed execution into explicit coaching steps, never calendar fitness.

Two comparable successful sessions on different dates unlock one next step.
A fatigue failure returns one step. These are versioned coaching choices, not
validated physiological cutoffs. Source counts remain ceilings, not targets.
"""
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json

VERSION = 'method-progression-v1'
SUCCESS_COUNT = 2


@lru_cache(maxsize=128)
def _group_ids(group):
    from .method_library import entries
    return frozenset(m["id"] for m in entries() if (m.get("progression") or {}).get("group") == group)


@lru_cache(maxsize=128)
def _group_recipes(group):
    from .method_library import entries
    return [{k: m.get(k) for k in ("id", "version", "prescription", "progression")}
            for m in entries() if (m.get("progression") or {}).get("group") == group]


def definition(record, profile):
    explicit = record.get('progression')
    rx = record.get('prescription') or {}
    if explicit:
        spec = deepcopy(explicit)
    elif rx.get('work', {}).get('range', [0, 0])[0] != rx.get('work', {}).get('range', [0, 0])[-1]:
        spec = {'group': record['id'], 'stage': None, 'steps': 3, 'kind': 'WITHIN_RANGE'}
    else:
        return None
    overrides = ((profile.get('method_library') or {}).get('overrides') or {})
    group_ids = _group_ids(spec["group"]) | {record["id"]}
    numeric = {key: {k: v for k, v in value.items() if k != 'enabled'} for key, value in overrides.items()
               if key in group_ids and any(k != 'enabled' for k in value)}
    identity = {'version': VERSION, 'group': spec['group'], 'discipline': profile.get('discipline'),
                'overrides': numeric, 'recipes': _group_recipes(spec['group']) or [{k: record.get(k) for k in ('id', 'version', 'prescription', 'progression')}]}
    spec['key'] = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    return spec


def state(record, profile, *, phase=None):
    spec = definition(record, profile)
    if not spec:
        return None
    sport = profile.get('actual_sport') or profile.get('sport')
    observations = (profile.get('_method_progression') or {}).get('observations', [])
    rows = sorted([child for row in observations for child in ([row] + [{**row, 'method': part} for part in row['method'].get('parts', [])])], key=lambda r: (r['day'], r['id']))
    stage, successes, last_day = 0, [], None
    for row in rows:
        m = row['method']
        p = m.get('progression') or {}
        if m.get('sport') != sport or p.get('key') != spec['key'] or p.get('version') != VERSION:
            continue
        if not row['success']:
            stage, successes = max(0, min(stage, p.get('step', stage))-1), []
            last_day = row['day']
        elif p.get('step') == stage and row.get('rpe', 10) <= 8 and row['day'] not in successes:
            successes.append(row['day'])
            last_day = row['day']
            if len(successes) >= SUCCESS_COUNT:
                stage, successes = min(spec['steps']-1, stage+1), []
    # Preparation may cap the available shape, but never advances it.
    phase_cap = 0 if phase in {'RE_ENTRY', 'GENERAL_PREPARATION', 'TRANSITION'} else spec['steps']-1
    unlocked = min(stage, phase_cap)
    step = spec.get('stage') if spec.get('stage') is not None else unlocked
    matching = [r for r in rows if r['method'].get('sport') == sport
        and (r['method'].get('progression') or {}).get('key') == spec['key']
        and (r['method'].get('progression') or {}).get('version') == VERSION]
    last_failure = max((r['day'] for r in matching if not r['success']), default='')
    practiced = any(r['success'] and r.get('rpe', 10) <= 8 and r['day'] > last_failure
        and r['method']['progression'].get('step') == step for r in matching)
    return {**spec, 'version': VERSION, 'step': step, 'requires_fresh_building': step > 0 and not practiced, 'unlocked_step': unlocked,
        'position': step / max(1, spec['steps']-1) if spec.get('kind') == 'WITHIN_RANGE' else 0.,
        'eligible': step <= unlocked, 'successful_dates_at_step': successes,
        'required_successes': SUCCESS_COUNT, 'last_observation_date': last_day,
        'basis': 'CONFIRMED_COMPARABLE_EXECUTION', 'advancement_recovery_min_exclusive': 90,
        'explanation': 'След две потвърдени качествени изпълнения на различни дати може да се премине една стъпка напред при Recovery над 90%. Умора с неизпълнена доза връща една стъпка. Датата сама не удължава отсечките.'}


def descriptor(method):
    r = (method.get('method_library') or {}).get('resolution') or {}
    p = r.get('progression')
    return {k: p[k] for k in ('version', 'key', 'group', 'step')} if p else None
