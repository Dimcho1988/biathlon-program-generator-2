"""One selectable registry for source recipes and existing coach profiles.

Stable IDs keep execution history valid. Numerical source recipes retain their
resolver; personal coach profiles retain their own capacity calculation.
"""
from copy import deepcopy
from hashlib import sha256
import json
from . import method_dosing, method_library, training_methods, race_specific

VERSION = 'method-registry-v4-individual-tmax'
COMBINED_STRUCTURES = {'ALTERNATING', 'CRUISE_ALTERNATING', 'THRESHOLD_HIGH', 'AEROBIC_STRENGTH', 'AEROBIC_SUPPORT', 'MIXED_AEROBIC'}


def coach_card(method):
    interval = method.get('interval_template') or method.get('interval_profile') or {}
    card = {'id': method['id'], 'title': method['title'], 'sports': list(method['sports']),
        'periods': list(method['periods']), 'disciplines': [], 'registry_kind': 'COACH_PROFILE',
        'automatic_selection': method.get('automatic_selection', True), 'prescription': None,
        'dosing': method.get('dosing') or (method_dosing.parameters(method) if method['zone'] != 'STR' else None),
        'source': {'authors': 'onFlows', 'reference': method.get('source_id', method['id']),
                   'locator': method.get('implementation_profile', training_methods.VERSION)},
        'adaptation': method.get('adaptation', 'Работен треньорски профил с индивидуална опора и общите правила за Recovery и дозиране.'),
        'guidance': {'purpose': f"{method['title']} · компонент {method['zone']}.",
            'execution': method['instructions'],
            'recovery': ('Запази предписаните паузи и резерв от качествени повторения. Почивката не увеличава автоматично дозовия бюджет.' if interval else 'Работата е контролирана; загрявката, разпускането и преходите се отчитат в общия товар.'),
            'success': 'Изпълни предписаното усилие с равномерен ритъм и запазена техника. Потвърди реалното изпълнение към активността.',
            'errors': 'Не ускорявай, за да достигнеш пулсово число, и не добавяй работа само за да запълниш свободното време.',
            'adjustment': 'Под 50% Recovery целевата работа над Z1 отпада. Ролята следва тренировъчната цел. Поддържащата работа е 60% от развиващата методна доза без допълнителен Recovery множител. Развиващата доза намалява с половината от дефицита на Recovery.',
            'progression': 'Поддържащата работа може да подготви следваща развиваща сесия. Увеличението изисква успешно изпълнение и допустими доза, общ товар и възстановяване. 90% Recovery не е задължителен праг; календарът сам не доказва готовност.'},
        'coach_profile': {'zone': method['zone'], 'structure': method['structure'],
            'purpose': method['purpose'], 'dose_policy': deepcopy(method.get('dose_policy')),
            'interval': deepcopy(interval), 'work_minutes': [method['min_work_min'], method['max_work_min']]}}


    if method.get('strength_profile'):
        p = method['strength_profile']
        card['coach_profile']['strength'] = deepcopy(p)
        card['guidance'].update(
            execution='; '.join(f"{e['name']}: {p['sets']} × {e['reps']}" for e in p['exercises']) + '. ' + p['reserve_definition'],
            recovery=f"{p['rest_seconds']} сек между сериите; {p['transition_seconds']} сек между упражненията. Загрявката и разпускането са извън STR.",
            adjustment='Поддържащата и активиращата доза са 50–60% от изграждащите повторения за едно занимание. Запазват се всички упражнения и цели серии. ' + p['continuity'],
            progression=p['progression'])
    return card


def enabled(profile, method):
    preferences = profile.get('method_library') or {}
    if not preferences.get('enabled', True):
        return False
    overrides = preferences.get('overrides') or {}
    identifier = method.get('method_library_id') or method['id']
    parent = method.get('variation_parent_id')
    if not overrides.get(identifier, {}).get('enabled', method.get('automatic_selection', True)):
        return False
    return not (parent and overrides.get(parent, {}).get('enabled') is False)


def executable_methods(profile, speed_by_sport, *, legacy_methods=None, race_reference=None):
    legacy = deepcopy(training_methods.resolved_methods(profile) if legacy_methods is None else legacy_methods)
    if race_reference:
        legacy.extend(race_specific.methods(profile, race_reference, legacy))
    result = []
    mixed = (profile.get('planning_controls') or {}).get('mixed_sessions_enabled', True)
    for m in legacy:
        if not enabled(profile, m) or (not mixed and (m.get('mixed_component') or m['structure'] in COMBINED_STRUCTURES)):
            continue
        m['method_card'] = coach_card(m)
        m['registry_version'] = VERSION
        result.append(m)
    for sport, speed in speed_by_sport.items():
        for m in method_library.executable_methods({**profile, 'actual_sport': sport}, speed):
            m['registry_version'] = VERSION
            result.append(m)
    return result


def catalog(profile, speed_view=None, *, phase=None, today=None):
    sourced = method_library.catalog(profile, speed_view, phase=phase, today=today)
    sport = profile.get('actual_sport') or profile.get('sport')
    methods = training_methods.resolved_methods(profile)
    # Race references remain visible and controllable through the same registry.
    if speed_view and profile.get('race_duration_min'):
        from . import race_duration
        event = race_duration.resolve(profile, speed_view)
        ref = race_specific.reference(profile, speed_view, event, today)
        methods += race_specific.methods(profile, ref, methods)
    for m in methods:
        card = coach_card(m)
        allowed = enabled(profile, m)
        mixed_off = not (profile.get('planning_controls') or {}).get('mixed_sessions_enabled', True) and (m.get('mixed_component') or m['structure'] in COMBINED_STRUCTURES)
        compatible = sport in m['sports'] and (phase is None or phase in m['periods'])
        card['resolution'] = {'status': 'DISABLED' if not allowed or mixed_off else 'READY' if compatible else 'INCOMPATIBLE',
            'reason': 'DISABLED_BY_PROFILE' if not allowed or mixed_off else None if compatible else 'SPORT_OR_PERIOD_NOT_SUPPORTED',
            'phase': phase, 'zone': m['zone'], 'automatic_selection_enabled': allowed and not mixed_off,
            'selection_basis': 'COACH_PROFILE_RECHECKED_AGAINST_CAPACITY_RECOVERY_AND_WEEKLY_BUDGET',
            'message': 'Личната доза се изчислява в седмичния план. Числовият профил се настройва в „Управление“.'}
        sourced['methods'].append(card)
    return {**sourced, 'registry_version': VERSION}


def plan_catalog(profile):
    """Pin catalogue provenance without copying unused recipes into every plan.

    Selected sessions retain their full guidance and frozen execution descriptor.
    The complete, current catalogue is served by the methods endpoint. The hash
    also pins the personal numerical profiles that produced this draft.
    """
    legacy = training_methods.catalog(profile)
    methods = legacy.pop('methods')
    source = legacy['source_catalog']
    legacy['source_catalog'] = {key: value for key, value in source.items() if key != 'methods'}
    return {**legacy, 'status': 'PINNED_REGISTRY_PROVENANCE', 'registry_version': VERSION,
            'source_method_library': method_library.provenance(),
            'resolved_profile_count': len(methods), 'method_ids': [m['id'] for m in methods],
            'resolved_profiles_sha256': sha256(json.dumps(methods, sort_keys=True,
                ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}
