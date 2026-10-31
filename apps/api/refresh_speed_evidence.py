"""Current-run paired observations for causal refresh calibration.

An active generation is an immutable previous result, not the source of new
observations during a full refresh. Keep summaries only; never retain streams
or admit a calculated load as an index observation.
"""
from copy import deepcopy
from datetime import date

from .trainability_history import admit_activities


class RefreshSpeedEvidence:
    def __init__(self, inputs, start, end, *, expected_activity_dates=None):
        self.inputs = inputs
        self.start, self.end = start, end
        self.expected_activity_dates = expected_activity_dates
        from .initial_threshold_view import _catalog_covers_processed_history
        source = (inputs['calendar'].get('snapshot_payload') or {}).get('load_history') or {}
        self.covered_dates = set()
        if _catalog_covers_processed_history(source, inputs['calendar']['activities']):
            by_day = {}
            for row in source.get('daily', []):
                by_day.setdefault(row.get('date'), set()).add(row.get('zone'))
            self.covered_dates = {day for day, zones in by_day.items() if day and day < start.isoformat()
                                  and {'Z1','Z2','Z3','Z4','Z5'} <= zones}
        self.covered_dates -= {r.get('local_date') for r in inputs['calendar']['activities']
                              if r.get('quality_status') == 'provider_missing'}
        # The refreshed interval is replaced in full, including removed or
        # failed recordings. Old rows in that interval must not fill its holes.
        self.catalog = {r['activity_ref']: deepcopy(r) for r in inputs['calendar']['activities']
                        if r['local_date'] < start.isoformat()}
        self.history = {r['activity_ref']: deepcopy(r) for r in inputs['history']
                        if r['local_date'] < start.isoformat()}

    def record(self, metadata, shadow=None):
        row = deepcopy(metadata)
        ref = row['activity_ref']
        self.catalog[ref] = row
        self.history[ref] = {
            'activity_ref': ref, 'sport': row.get('sport') or row.get('activity_type') or 'Unknown',
            'local_date': row['local_date'],
            'start_at_utc': row.get('start_at_utc') or row['local_date']+'T00:00:00Z',
            'index': deepcopy((shadow or {}).get('trainability_index')),
            'unavailable_reason': None if (shadow or {}).get('trainability_index') else 'NO_SHADOW',
        }

    def at(self, day):
        # Filter before the admission screen too: future observations must not
        # affect even the screening of this historical calibration window.
        catalog = [r for r in self.catalog.values() if r['local_date'] <= day.isoformat()]
        history = [r for r in self.history.values() if r['local_date'] <= day.isoformat()]
        covered = {d for d in self.covered_dates if d <= day.isoformat()}
        if self.expected_activity_dates is not None:
            for ordinal in range(self.start.toordinal(), min(day, self.end).toordinal()+1):
                key = date.fromordinal(ordinal).isoformat()
                records = [r for r in catalog if r['local_date'] == key]
                if (len(records) == self.expected_activity_dates.get(key, 0)
                        and all(r.get('quality_status') != 'provider_missing' for r in records)):
                    covered.add(key)
        period_start = min(covered | {r['local_date'] for r in catalog}, default=self.start.isoformat())
        # Coverage markers contain no Q or E. Successful enumeration proves a
        # rest date; missing activity details leave its calendar date unknown.
        source = {'period_start': period_start, 'period_end': min(day, self.end).isoformat(),
            'load_selection': {'basis': 'IN_PROGRESS_REFRESH'},
            'daily': [{'date': d, 'zone': z} for d in sorted(covered) for z in ('Z1','Z2','Z3','Z4','Z5')],
            'quality': {'processed_activities': len(catalog),
                'limited_activities': sum(r.get('quality_status') == 'limited' for r in catalog),
                'excluded_activities': sum(r.get('quality_status') in {'excluded','provider_missing'} for r in catalog)}}
        return {**self.inputs, 'today': day,
                'calendar': {'activities': catalog,
                             # Shared Q is downstream of the speed profile.
                             # Only measured duration positions expert times.
                             'snapshot_payload': {'load_history': source}},
                'history': admit_activities(history)}


def expected_activity_dates(listed, start, end):
    """Exact provider enumeration; malformed/undated entries prevent coverage."""
    from intervals_inspector.real_data_source import _activity_date, _activity_id
    if not isinstance(listed, list):
        return None
    grouped = {}
    for row in listed:
        if not isinstance(row, dict):
            return None
        day, key = _activity_date(row), _activity_id(row)
        if day is None or key is None or not start <= day <= end:
            return None
        grouped.setdefault(day.isoformat(), set()).add(key)
    return {day: len(keys) for day, keys in grouped.items()}
