"""Current-run paired observations for causal refresh calibration.

An active generation is an immutable previous result, not the source of new
observations during a full refresh. Keep summaries only; never retain streams
or admit a calculated load as an index observation.
"""
from copy import deepcopy

from .trainability_history import admit_activities


class RefreshSpeedEvidence:
    def __init__(self, inputs, start, end):
        self.inputs = inputs
        self.start, self.end = start, end
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
        return {**self.inputs, 'today': day,
                'calendar': {'activities': catalog,
                             # Shared Q is downstream of the speed profile.
                             # Only measured duration positions expert times.
                             'snapshot_payload': {'load_history': {
                                 'period_start': self.start.isoformat(),
                                 'period_end': min(day, self.end).isoformat(),
                                 'load_selection': {'basis': 'IN_PROGRESS_REFRESH'}}}},
                'history': admit_activities(history)}
