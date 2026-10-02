from copy import deepcopy
import base64
import hashlib
import json
from threading import Event

import httpx
import pytest

from apps.api.history_archive import (BUCKET, CODEC, HistoryArchive, archive_row,
    canonical, digest, pack, restore, run, unpack, verify_backup)
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.shadow_storage import decode_shadow_payload, encode_shadow_payload
from apps.api.sync_worker import run_worker


KEY = "a" * 64
INPUT_KEY = "b" * 64


def payload():
    return {"result_hash": "d" * 64, "zone_summary": [{"zone": "Z3", "seconds": 900}],
        "configuration_fingerprint": "model-config",
        "timeseries": [{"elapsed": i, "speed": 12.123456789, "zero": -0.0,
                        "flag": False, "label": "зона"} for i in range(3000)]}


class Backend:
    def __init__(self, original=None):
        self.payload = deepcopy(original or payload())
        self.objects = {}
        self.writes = 0
        self.uploads = 0
        self.conflict = False
        self.corrupt = False
        self.fail_upload = False
        client = httpx.Client(transport=httpx.MockTransport(self.storage))
        self._history_archive = HistoryArchive("https://example.supabase.co", {"apikey": "server-only"}, client)

    def storage(self, request):
        assert request.headers["apikey"] == "server-only"
        assert "server-only" not in str(request.url)
        path = request.url.path.replace("/storage/v1/object/authenticated/", "").replace("/storage/v1/object/", "")
        if request.method == "POST":
            self.uploads += 1
            if self.fail_upload:
                return httpx.Response(500)
            if path in self.objects:
                return httpx.Response(400, json={"error": "Duplicate"})
            self.objects[path] = request.content
            return httpx.Response(200)
        content = self.objects.get(path)
        if content is None:
            return httpx.Response(404)
        return httpx.Response(200, content=(b"corrupted" if self.corrupt else content))

    def _json(self, value):
        return value

    def _request(self, method, path, **kwargs):
        if path.endswith("read_onflows_history_archive_batch"):
            args = kwargs["json"]
            if args["p_kind"] == "input" or args["p_after"]:
                return []
            return [{"entity_key": KEY, "payload": deepcopy(self.payload), "storage_hash": "f" * 32}]
        return [{"result_payload": deepcopy(self.payload), "input_payload": deepcopy(self.payload)}]

    def _rpc_row(self, name, args):
        if name == "read_onflows_history_storage_hash":
            return {"storage_hash": "f" * 32}
        assert name == "replace_onflows_history_payload"
        if self.conflict:
            return {"outcome": "CONFLICT"}
        self.writes += 1
        self.payload = deepcopy(args["p_payload"])
        return {"outcome": "RESTORED" if args["p_restore"] else "ARCHIVED"}


def test_dry_run_writes_neither_objects_nor_database():
    backend = Backend()
    result = run(backend, max_rows=2)
    assert result["scanned"] == 1 and result["fields"] == 1
    assert result["after_json_bytes"] < result["before_json_bytes"]
    assert backend.uploads == backend.writes == 0


def test_archive_restores_exact_values_and_keeps_scientific_metadata():
    backend = Backend()
    original = digest(backend.payload)
    run(backend, apply=True)
    assert backend.writes == 1 and backend.payload["timeseries"]["codec"] == CODEC
    hydrated = backend._history_archive.hydrate(backend.payload, "shadow", KEY)
    assert digest(hydrated) == original
    assert backend.payload["zone_summary"] == payload()["zone_summary"]
    assert backend.payload["result_hash"] == payload()["result_hash"]
    restore(backend, "shadow", KEY)
    assert digest(backend.payload) == original


def test_backup_only_is_idempotent_and_never_changes_database():
    backend = Backend()
    row = {"entity_key": KEY, "payload": backend.payload}
    archive_row(backend, row, "shadow", backup=True)
    archive_row(backend, row, "shadow", backup=True)
    assert backend.writes == 0 and len(backend.objects) == 1
    assert isinstance(backend.payload["timeseries"], list)


def test_database_field_selection_does_not_stall_borderline_candidates():
    backend = Backend({"timeseries": [{"x": 1}]})
    row = {"entity_key": KEY, "payload": backend.payload, "storage_hash": "f" * 32,
           "archive_fields": ["timeseries"]}
    assert archive_row(backend, row, "shadow", apply=True)["fields"] == 1
    assert backend.writes == 1
    row["archive_fields"] = ["zone_summary"]
    with pytest.raises(ValueError):
        archive_row(backend, row, "shadow", apply=True)


@pytest.mark.parametrize("failure", ["conflict", "corrupt", "fail_upload"])
def test_failures_keep_original_database_payload(failure):
    backend = Backend()
    original = digest(backend.payload)
    setattr(backend, failure, True)
    with pytest.raises(ValueError):
        run(backend, apply=True)
    assert backend.writes == 0 and digest(backend.payload) == original


def test_input_samples_and_missing_null_numeric_types_are_preserved():
    values = [{"x": 1, "zero": -0.0}, {"x": 1.0, "nullable": None, "flag": False}] * 3000
    reference, content = pack("input", INPUT_KEY, "samples", values)
    assert digest(unpack(reference, content, "input", INPUT_KEY, "samples")) == digest(values)
    backend = Backend({"input_hash": "e" * 64, "samples": values})
    original = digest(backend.payload)
    archive_row(backend, {"entity_key": INPUT_KEY, "payload": backend.payload, "storage_hash": "f" * 32}, "input", apply=True)
    assert digest(backend._history_archive.hydrate(backend.payload, "input", INPUT_KEY)) == original


@pytest.mark.parametrize("change", ["bucket", "path", "bytes", "json_bytes", "sha256", "json_sha256"])
def test_invalid_reference_and_cross_entity_paths_fail_closed(change):
    reference, content = pack("shadow", KEY, "timeseries", payload()["timeseries"])
    reference[change] = {"bucket": "public", "path": "../other", "bytes": True,
        "json_bytes": 2**30, "sha256": "0" * 64, "json_sha256": "0" * 64}[change]
    with pytest.raises(ValueError):
        unpack(reference, content, "shadow", KEY, "timeseries")


def test_archive_decoding_rejects_corruption_trailing_data_and_wrong_entity():
    reference, content = pack("shadow", KEY, "timeseries", payload()["timeseries"])
    with pytest.raises(ValueError):
        unpack(reference, content, "shadow", INPUT_KEY, "timeseries")
    altered = content + b"trailing"
    reference["bytes"] = len(altered)
    reference["sha256"] = hashlib.sha256(altered).hexdigest()
    reference["path"] = f"v1/shadow/{KEY}/timeseries/{reference['sha256']}.xz"
    with pytest.raises(ValueError):
        unpack(reference, altered, "shadow", KEY, "timeseries")


def test_existing_compacted_series_can_be_archived_and_decoded():
    encoded = encode_shadow_payload(payload())
    backend = Backend(encoded)
    archive_row(backend, {"entity_key": KEY, "payload": backend.payload, "storage_hash": "f" * 32},
                "shadow", apply=True, minimum_bytes=1)
    restored = decode_shadow_payload(backend._history_archive.hydrate(backend.payload, "shadow", KEY))
    assert digest(restored) == digest(payload())


def repository(client):
    return SupabasePilotRepository(supabase_url="https://example.supabase.co", secret_key="server-only",
        encryption_key=base64.urlsafe_b64encode(b"x" * 32).decode(), client=client, generation_reads=True)


def test_activity_view_and_speed_projection_load_archives_through_scoped_keys():
    backend = Backend()
    reference = backend._history_archive.upload_verified("shadow", KEY, "timeseries", payload()["timeseries"])
    input_reference = backend._history_archive.upload_verified("input", INPUT_KEY, "samples", [{"elapsed_s": 0}])
    def handle(request):
        if "/storage/" in request.url.path:
            return backend.storage(request)
        if request.url.path.endswith("active_onflows_activity_view"):
            return httpx.Response(200, json=[{"catalog_payload": {}, "input_key": INPUT_KEY,
                "shadow_run_key": KEY, "series_payload": {"samples": input_reference},
                "shadow_payload": {**payload(), "timeseries": reference}}])
        return httpx.Response(200, json=[{"run_key": KEY, "activity_ref": "activity",
            "timeseries": reference, "speed_test_series": [], "trainability_index": {},
            "configuration_fingerprint": "model-config"}])
    repo = repository(httpx.Client(transport=httpx.MockTransport(handle)))
    # This fixture uses a distinct header assertion from the maintenance fixture.
    repo._history_archive._headers["apikey"] = "server-only"
    view = repo.active_activity_view("athlete", "activity")
    assert view["series_payload"]["samples"] == [{"elapsed_s": 0}]
    assert digest(view["shadow_payload"]) == digest(payload())
    result = repo.activity_speed_history_samples("athlete", (KEY,))
    assert result[KEY]["shadow_payload"]["timeseries"] == payload()["timeseries"]


def test_idle_worker_runs_bounded_archive_tick():
    stop = Event()
    calls = []
    class Repo:
        def claim_sync_job(self, **kwargs):
            return None
        def archive_due_history(self):
            calls.append(True)
            stop.set()
            return {"scanned": 2}
    assert run_worker(Repo(), worker_id="test", stop_event=stop) == 0
    assert calls == [True]


def test_compaction_leaves_archived_fields_in_place():
    from apps.api.compact_shadow_storage import compact_payload
    reference, _ = pack("shadow", KEY, "timeseries", payload()["timeseries"])
    original = {**payload(), "timeseries": reference}
    assert compact_payload(original)[0] == original


def test_deployment_backup_verifies_repository_reader_without_database_write():
    backend = Backend()
    backend._decode_shadow = lambda proposed, key: decode_shadow_payload(backend._history_archive.hydrate(proposed, "shadow", key))
    original = digest(backend.payload)
    assert verify_backup(backend, "shadow", KEY)["mode"] == "backup-verified"
    assert backend.uploads > 0 and backend.writes == 0
    assert digest(backend.payload) == original
    backend._decode_shadow = lambda proposed, key: {"wrong": True}
    with pytest.raises(ValueError):
        verify_backup(backend, "shadow", KEY)
