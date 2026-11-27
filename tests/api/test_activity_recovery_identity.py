"""Recovery-only releases preserve activity work without hiding load changes."""
from datetime import timedelta
from pathlib import Path
import shutil

import pytest

from apps.api import activity_refresh, preparation_identity as identity
from apps.api.cloud import InMemorySnapshotRepository
from apps.api.real_service import refresh
from biathlon import recovery_v2, recovery_capacity
from tests.api.test_incremental_activity_refresh import Recordings, cloud_fixture, END, NOW, ENV


@pytest.fixture
def release_sources(tmp_path):
    modules = set().union(*(identity.source_manifest(d, _project_activity=False) for d in identity._ROOTS))
    for module in modules:
        source = (identity._ROOT / module.removeprefix('asset:') if module.startswith('asset:')
                  else identity._source_path(identity._ROOT, module))
        target = tmp_path / source.relative_to(identity._ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return tmp_path


def key(root, domain='activity'):
    return activity_refresh.digest(identity.source_manifest(domain, root=root))


def edit(root, relative, old, new):
    path = root / relative
    source = path.read_text()
    assert old in source
    path.write_text(source.replace(old, new))


@pytest.mark.parametrize('schema_change', [False, True])
def test_recovery_change_preserves_activity_but_invalidates_consumers(release_sources, schema_change):
    before = {d: key(release_sources, d) for d in identity._ROOTS}
    if schema_change:
        edit(release_sources, 'apps/api/model_schemas.py',
             recovery_v2.VERSION, recovery_v2.VERSION + '-test')
    else:
        edit(release_sources, 'biathlon/recovery_v2.py', '1. + 4. *', '1. + 3. *')
    assert key(release_sources) == before['activity']
    for domain in ('dashboard', 'learning', 'pages'):
        assert key(release_sources, domain) != before[domain]


@pytest.mark.parametrize('relative,old,new', [
    ('apps/api/model_schemas.py', 'extra="forbid"', 'extra="ignore"'),
    ('apps/api/model_schemas.py', 'le=150,strict=True', 'le=149,strict=True'),
    ('biathlon/component_load.py', 'VERSION =', 'CHANGED = 1\nVERSION ='),
    ('biathlon/recovery_capacity.py', 'return minutes/t,', 'return 2*minutes/t,'),
    ('apps/api/activity_shadow_pipeline.py', 'from __future__ import annotations',
     'from __future__ import annotations\nCHANGED = 1'),
])
def test_load_and_shared_contract_changes_still_invalidate(release_sources, relative, old, new):
    before = key(release_sources)
    edit(release_sources, relative, old, new)
    assert key(release_sources) != before


@pytest.mark.parametrize('new_dependency', [
    'def new_load():\n    return recovery_v2.defaults()\n',
    'def new_load():\n    return project_recovery(None, None, None)\n',
    'def new_load():\n    return ModelStore(None).config(None)\n',
    'from biathlon.recovery_v2 import simulate\ndef new_load():\n    return simulate([])\n',
    'from biathlon import recovery_v2 as rv\ndef new_load():\n    return rv.defaults()\n',
])
def test_new_upstream_recovery_dependency_falls_back_to_full_graph(release_sources, new_dependency):
    path = release_sources / 'apps/api/model_service.py'
    path.write_text(path.read_text() + '\n' + new_dependency)
    projected = identity.source_manifest('activity', root=release_sources)
    assert projected == identity.source_manifest('activity', root=release_sources, _project_activity=False)
    before = key(release_sources)
    edit(release_sources, 'biathlon/recovery_v2.py', '1. + 4. *', '1. + 3. *')
    assert key(release_sources) != before


def test_shared_schema_using_recovery_contract_disables_projection(release_sources):
    path = release_sources / 'apps/api/model_schemas.py'
    path.write_text(path.read_text() + '\nclass NewLoadInput(Strict):\n    config: ZoneConfig\n')
    assert identity.source_manifest('activity', root=release_sources) == identity.source_manifest(
        'activity', root=release_sources, _project_activity=False)


def test_recovery_release_reuses_recordings_and_matches_full_load(monkeypatch, release_sources):
    client = Recordings()
    client.add(END - timedelta(days=1))
    client.add(END)
    repo = InMemorySnapshotRepository()
    settings = cloud_fixture(monkeypatch, repo)
    monkeypatch.setattr(activity_refresh, 'calculation_version', lambda _: key(release_sources))
    def sync():
        client.streams.clear()
        refresh(repo, environ=ENV, client=client, period_end=END, now=NOW, athlete_settings=settings)
        return repo.latest('pilot')
    before = sync()
    daily = recovery_capacity.daily_rows(before['load_history'])
    assert any(r.get('depletion_fraction', 0) > 0 for r in daily)
    recovery_before = recovery_v2.simulate(daily, target=END)
    edit(release_sources, 'biathlon/recovery_v2.py', '1. + 4. *', '1. + 3. *')
    # Exercise changed recovery numerics independently of the unchanged loads.
    monkeypatch.setattr(recovery_v2, 'duration_shape', lambda days: 5.)
    after = sync()
    assert client.streams == []
    assert after['load_history'] == before['load_history']
    assert recovery_v2.simulate(recovery_capacity.daily_rows(after['load_history']), target=END) != recovery_before
    monkeypatch.setattr(activity_refresh, 'calculation_version', lambda _: None)
    full = sync()
    assert len(client.streams) == 2
    assert full == after
