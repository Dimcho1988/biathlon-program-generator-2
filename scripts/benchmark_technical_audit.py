"""Reproduce the 2026-11-01 audit with private, frozen JSON fixtures.

Examples (from a checkout containing both commits):
  python scripts/benchmark_technical_audit.py --fixtures /path/to/replay --mode planner
  python scripts/benchmark_technical_audit.py --fixtures /path/to/replay --mode recording
  python scripts/benchmark_technical_audit.py --fixtures /path/to/replay --mode history

No provider/database requests or writes are made. Planner mode replays the pure
initial generator, without the outer timed search or saved draft progression
anchors. It is deliberately not a measurement of the complete live generate
endpoint. Recording mode uses ten copies of one real long recording as a
controlled memory workload. History mode compares all captured Blagoy records.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gc
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import resource
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, process_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(directory, name):
    return json.loads((directory / name).read_text())


def previous_module(name, path, revision, directory):
    source = subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=ROOT)
    local = Path(directory) / (name.split('.')[-1] + '.py')
    local.write_bytes(source)
    spec = importlib.util.spec_from_file_location(name, local)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fixture_repository(directory, athlete='dimitar'):
    from apps.api.planning_input_bundle import PlanningInputBundle
    from apps.api.shadow_storage import decode_shadow_payload
    data = read(directory, f'{athlete}-inputs.json')[0]['inputs']
    catalog = read(directory, f'{athlete}-catalog.json')
    extra = read(directory, 'dimitar-extra.json') if athlete == 'dimitar' else {'management': [], 'progression': []}
    bundle = PlanningInputBundle(data, data['athlete_alias'], 'FULL', [])

    class Repository:
        def planning_inputs_bundle(self, *args, **kwargs): return bundle
        def athlete_settings(self, alias): return bundle.settings_bundle().athlete_settings()
        def active_analysis(self, alias): return bundle.active_analysis()
        def active_planning_calendar(self, alias, start, end):
            return {**bundle.active_analysis(), 'activities': [deepcopy(a) for a in catalog['catalog']
                    if start.isoformat() <= a['local_date'] <= end.isoformat()]}
        active_trainability_calendar = active_planning_calendar
        active_activity_calendar = active_planning_calendar
        def trainability_summaries(self, alias, keys):
            return {r['run_key']: r for r in catalog['summaries'] if r['run_key'] in keys}
        def athlete_planning_calendar(self, alias): return bundle.settings_bundle().athlete_planning_calendar()
        def athlete_planning_profile(self, alias): return bundle.settings_bundle().athlete_planning_profile()
        def athlete_mesocycle_accent_preferences(self, alias): return bundle.settings_bundle().athlete_mesocycle_accent_preferences()
        def _request(self, method, path, **kwargs):
            if method != 'GET': raise AssertionError('Replay forbids writes')
            if path.startswith('/onflows_model_entries'): return data['model_entries']
            if path.startswith('/onflows_response_entries'): return data['response_entries']
            if path.startswith('/onflows_management_entries'):
                if 'kind=eq.DRAFT' in path: return []
                return [r for r in (extra['management'] or []) if 'kind=eq.PROFILE' not in path or r['kind'] == 'PROFILE']
            if path.startswith('/onflows_management_plan_revisions'): return extra['progression'] or []
            raise AssertionError('Unexpected replay read: ' + path)
        def _json(self, response): return response

    return Repository, data, bundle, decode_shadow_payload


def clock_models():
    from apps.api import model_service
    now = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return now.astimezone(tz)
    model_service.datetime = Clock
    return now


def planner(args):
    from apps.api import training_plan_engine as current
    from apps.api.read_session import ReadSession
    Repository, data, bundle, _ = fixture_repository(args.fixtures)
    now = clock_models()
    profile = bundle.profile_rows()[0]['payload']
    samples, expected = [], None
    with TemporaryDirectory() as temporary:
        before = previous_module('apps.api.audit_previous_engine', 'apps/api/training_plan_engine.py', args.baseline, temporary)
        for pair in range(args.repeats):
            variants = [('before', before), ('after', current)]
            for label, engine in variants if pair % 2 == 0 else reversed(variants):
                scope = ReadSession(Repository()); scope.capture_planning_inputs(data['athlete_alias'])
                body = deepcopy(profile)
                result = None
                gc.collect()
                wall, cpu = perf_counter(), process_time()
                result = engine.generate_plan(scope, data['athlete_alias'], body, start_date=now.date(), now=now)
                elapsed, cpu = perf_counter()-wall, process_time()-cpu
                encoded = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
                expected = encoded if expected is None else expected
                if encoded != expected or body != profile: raise AssertionError('Plan or source inputs differ')
                samples.append({'pair': pair, 'variant': label, 'wall_s': elapsed, 'cpu_s': cpu,
                                'bytes': len(encoded), 'sha256': sha256(encoded).hexdigest()})
    return {'scope': 'OFFLINE_PARTIAL_INITIAL_GENERATOR', 'complete_json_equal': True, 'samples': samples}


def recording_once(args):
    import httpx
    from apps.api.oauth_store import SupabasePilotRepository
    original = read(args.fixtures, 'blagoy-largest.json')[0]['result_payload']
    fields = ('configuration_fingerprint', 'vflat_model_version', 'vflat_config_version', 'trainability_index',
              'speed_test_series', 'speed_load_summary', 'timeseries')
    keys = tuple(f'{i:064x}' for i in range(10))
    response = httpx.Response(200, json=[{'run_key': k, 'activity_ref': k,
               **{f: original.get(f) for f in fields}} for k in keys])
    repo = SupabasePilotRepository(supabase_url='https://offline.invalid', secret_key='offline',
        encryption_key='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=', generation_reads=True,
        client=httpx.Client(transport=httpx.MockTransport(lambda r: response), trust_env=False))
    calls = []
    repo._request = lambda *a, **kw: calls.append(1) or response
    repo._hydrate_history = lambda payload, *a: payload
    wall, cpu = perf_counter(), process_time()
    if args.variant == 'batch':
        result = repo.activity_speed_history_samples('athlete', keys)
        iterator = ((key, result[key]) for key in keys)
    else:
        iterator = repo.iter_activity_speed_history_samples('athlete', keys)
    digest = sha256()
    for _, row in iterator:
        digest.update(json.dumps(row, sort_keys=True, separators=(',', ':')).encode())
    return {'variant': args.variant, 'wall_s': perf_counter()-wall, 'cpu_s': process_time()-cpu,
            'peak_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 'calls': len(calls),
            'response_bytes': len(response.content), 'sha256': digest.hexdigest()}


def recording(args):
    samples = []
    for pair in range(args.repeats):
        for variant in ('batch', 'stream') if pair % 2 == 0 else ('stream', 'batch'):
            command = [sys.executable, str(Path(__file__).resolve()), '--fixtures', str(args.fixtures),
                       '--mode', 'recording', '--variant', variant]
            samples.append(json.loads(subprocess.check_output(command, cwd=ROOT, text=True)))
    if len({r['sha256'] for r in samples}) != 1: raise AssertionError('Decoded recordings differ')
    return {'scope': 'TEN_COPIES_OF_A_REAL_LONG_RECORDING_FRESH_PROCESSES', 'samples': samples}


def history(args):
    from apps.api import actual_load_projection as current
    from apps.api.component_load_context import read_contexts
    from apps.api.component_load_projection import project_history
    from apps.api.read_session import ReadSession
    Repository, data, bundle, decode = fixture_repository(args.fixtures, 'blagoy')
    packed = {r['run_key']: r for p in args.fixtures.glob('blagoy-runs-*.json') for r in json.loads(p.read_text())}
    class Recordings(Repository):
        speed_load_cache_namespace = 'frozen-blagoy-history'
        def activity_speed_history_samples(self, alias, keys): return dict(self.iter_activity_speed_history_samples(alias, keys))
        def iter_activity_speed_history_samples(self, alias, keys):
            for key in keys:
                row = packed[key]
                yield key, {'run_key': key, 'activity_ref': row['activity_ref'], 'shadow_payload': decode(row['shadow_payload'])}
    clock_models()
    expected, samples = None, []
    with TemporaryDirectory() as temporary:
        before = previous_module('apps.api.audit_previous_projection', 'apps/api/actual_load_projection.py', args.baseline, temporary)
        before.unified_load = previous_module('biathlon.audit_previous_load', 'biathlon/unified_load.py', args.baseline, temporary)
        for label, model in [('before', before), ('after', current)]:
            scope = ReadSession(Recordings()); scope.capture_planning_inputs(data['athlete_alias'])
            source = bundle.active_analysis()['snapshot_payload']['load_history']
            result = model.reconstruct_history(scope, data['athlete_alias'], deepcopy(source))
            contexts = read_contexts(scope, data['athlete_alias'], list(dict.fromkeys(a['sport'] for a in result['activities'])))
            result = project_history(result, contexts)
            encoded = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            expected = encoded if expected is None else expected
            if encoded != expected: raise AssertionError('Historical Q/E/7-40 differs')
            samples.append({'variant': label, 'activities': len(result['activities']), 'daily_rows': len(result['daily']),
                            'bytes': len(encoded), 'sha256': sha256(encoded).hexdigest()})
    return {'scope': 'FULL_FROZEN_BLAGOY_HISTORY', 'complete_json_equal': True, 'samples': samples}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--mode', choices=('planner', 'recording', 'history'), required=True)
    parser.add_argument('--baseline', default='058ac0f19230ebe68ab0c791553e5494fa6b8bd1')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--variant', choices=('batch', 'stream'), help=argparse.SUPPRESS)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(); args.fixtures = args.fixtures.resolve()
    if not re.fullmatch('[a-f0-9]{7,40}', args.baseline) or args.repeats < 1: parser.error('Invalid baseline or repeat count')
    result = recording_once(args) if args.variant else {'planner': planner, 'recording': recording, 'history': history}[args.mode](args)
    text = json.dumps(result, indent=2)
    if args.output: args.output.write_text(text + '\n')
    print(text)
