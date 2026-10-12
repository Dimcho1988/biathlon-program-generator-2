"""Continue an approved pair only after an exact confirmed morning-method link."""
from copy import deepcopy
from biathlon.planning_schedule import day_sessions


def reconcile(locked_day, actual, observations):
    if not locked_day:
        return locked_day
    sessions = day_sessions(locked_day)
    if len(sessions) != 2 or not all(s.get('double_threshold') for s in sessions):
        return locked_day
    first, second = sorted(sessions, key=lambda s: s.get('slot', 0))
    pair = first.get('double_threshold_pair_id')
    if not pair or pair != second.get('double_threshold_pair_id') or len(actual) != 1:
        return locked_day
    activity = actual[0]
    report = next((r for r in observations if r['id'] == activity.get('activity_ref')
        and r['day'] == locked_day['date'] and r['method']['id'] == first['method_id']
        and r['method']['sport'] == first['sport'] and r['success'] and r.get('rpe', 10) <= 8), None)
    if report is None:
        return locked_day
    result = deepcopy(locked_day)
    second = deepcopy(second)
    second['continuation_of_confirmed_activity'] = activity['activity_ref']
    result.update(session=second, sessions=[second], activity_refs=[activity['activity_ref']],
                  double_threshold_continuation=True)
    return result
