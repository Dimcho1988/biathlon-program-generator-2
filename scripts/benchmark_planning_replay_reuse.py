"""Compare fixed causal scheduling replays with frozen private audit inputs.

This measures three complete generator calls in one calculation phase, not
the wall-clock-limited outer search or an authenticated live HTTP request.
It performs no provider/database requests or writes and checks complete JSON.
The fixture format is the 2026-11-01 audit replay archive; never commit fixtures.
"""
import argparse
from copy import deepcopy
import gc
from hashlib import sha256
import json
from pathlib import Path
import re
import resource
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, process_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_technical_audit import clock_models, fixture_repository, previous_module
from apps.api import training_plan_engine as current
from apps.api.planning_search import reservation_methods
from apps.api.read_session import ReadSession


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def benchmark(args):
    Repository, data, bundle, _ = fixture_repository(args.fixtures)

    class PersistedShape(Repository):
        # Exercise the production branch of context reuse without live stores.
        speed_load_cache_namespace = 'offline-frozen-planning-replays'

    now = clock_models()
    profile = bundle.profile_rows()[0]['payload']
    original = encode(data), encode(profile)
    expected, samples = {}, []
    with TemporaryDirectory() as temporary:
        before = previous_module('apps.api.replay_previous_engine', 'apps/api/training_plan_engine.py', args.baseline, temporary)
        for pair in range(args.repeats):
            variants = [('before', before), ('after', current)]
            for label, engine in variants if pair % 2 == 0 else reversed(variants):
                phase = ReadSession(PersistedShape())
                phase.capture_planning_inputs(data['athlete_alias'])
                recovery = {}
                results, steps = [], []
                gc.collect()
                for index in range(3):
                    policy = ({} if index == 0 else {
                        'optional_easy_fraction': 0., 'balance_complete_doses': False,
                        'component_gap_first': True,
                        **({'reservation_methods': reservation_methods(results[-1])} if index == 2 else {}),
                    })
                    body = {**deepcopy(profile), **({'_allocation_policy': policy} if policy else {})}
                    wall, cpu = perf_counter(), process_time()
                    result = engine.generate_plan(phase, data['athlete_alias'], body,
                        start_date=now.date(), now=now,
                        **({'_recovery_calculation_cache': recovery} if label == 'after' else {}))
                    wall, cpu = perf_counter()-wall, process_time()-cpu
                    encoded = encode(result)
                    expected.setdefault(index, encoded)
                    if encoded != expected[index]:
                        raise AssertionError(f'Complete plan differs at replay {index}, {label}')
                    results.append(result)
                    steps.append({'replay': index, 'wall_s': wall, 'cpu_s': cpu,
                                  'bytes': len(encoded), 'sha256': sha256(encoded).hexdigest()})
                if (encode(data), encode(profile)) != original:
                    raise AssertionError('Captured source inputs changed')
                sample = {'pair': pair, 'variant': label, 'wall_s': sum(s['wall_s'] for s in steps),
                          'cpu_s': sum(s['cpu_s'] for s in steps), 'steps': steps}
                samples.append(sample)
                print(json.dumps({'progress': {k:v for k,v in sample.items() if k != 'steps'}}), flush=True)
    medians = {label: {metric: statistics.median(s[metric] for s in samples if s['variant'] == label)
                      for metric in ('wall_s', 'cpu_s')} for label in ('before', 'after')}
    return {'scope': 'OFFLINE_THREE_FIXED_COMPLETE_GENERATOR_REPLAYS',
            'complete_json_equal': True, 'source_inputs_unchanged': True,
            'baseline': args.baseline, 'samples': samples, 'medians': medians,
            'process_peak_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
            'reduction_percent': {metric: 100*(1-medians['after'][metric]/medians['before'][metric])
                                  for metric in ('wall_s', 'cpu_s')}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch('[a-f0-9]{7,40}', args.baseline) or args.repeats < 2:
        parser.error('Use a concrete baseline commit and at least two alternating pairs')
    result = benchmark(args)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'samples'}, indent=2))
