"""Private, content-addressed history objects; exact, bounded restoration."""
from __future__ import annotations

import hashlib
import json
import lzma
import re
from typing import Any, Mapping

import httpx

from .shadow_storage import SERIES_FIELDS, decode_shadow_payload

CODEC = "onflows-history-object-xz-v1"
BUCKET = "onflows-history"
MAX_OBJECT_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
KEY = re.compile(r"^[a-f0-9]{64}$")
TABLES = {"shadow": ("onflows_activity_derived_runs", "run_key", "result_payload"),
          "input": ("onflows_activity_model_inputs", "input_key", "input_payload")}


class ArchiveSizeLimit(ValueError):
    """A valid inline field exceeds the supported object envelope."""


def fields_for(kind: str) -> tuple[str, ...]:
    if kind not in TABLES:
        raise ValueError("Invalid history kind")
    return SERIES_FIELDS if kind == "shadow" else ("samples",)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def is_reference(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("codec") == CODEC


def pack(kind: str, key: str, field: str, value: Any) -> tuple[dict[str, Any], bytes]:
    if not KEY.fullmatch(key) or field not in fields_for(kind):
        raise ValueError("Invalid history identity")
    raw = canonical(value)
    if not 0 < len(raw) <= MAX_JSON_BYTES:
        raise ArchiveSizeLimit("History field exceeds archive limit")
    content = lzma.compress(raw, preset=3)
    if len(content) > MAX_OBJECT_BYTES:
        raise ArchiveSizeLimit("History object exceeds archive limit")
    checksum = hashlib.sha256(content).hexdigest()
    return {"codec": CODEC, "bucket": BUCKET,
            "path": f"v1/{kind}/{key}/{field}/{checksum}.xz",
            "sha256": checksum, "json_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(content), "json_bytes": len(raw)}, content


def validate_reference(reference: Mapping[str, Any], kind: str, key: str, field: str) -> None:
    checksum = reference.get("sha256")
    if (not KEY.fullmatch(key) or field not in fields_for(kind)
            or reference.get("codec") != CODEC or reference.get("bucket") != BUCKET
            or not isinstance(checksum, str) or not KEY.fullmatch(checksum)
            or reference.get("path") != f"v1/{kind}/{key}/{field}/{checksum}.xz"
            or not isinstance(reference.get("json_sha256"), str)
            or not KEY.fullmatch(reference["json_sha256"])
            or type(reference.get("bytes")) is not int
            or not 0 < reference["bytes"] <= MAX_OBJECT_BYTES
            or type(reference.get("json_bytes")) is not int
            or not 0 < reference["json_bytes"] <= MAX_JSON_BYTES):
        raise ValueError("Invalid history archive reference")


def unpack(reference: Mapping[str, Any], content: bytes, kind: str, key: str, field: str) -> Any:
    validate_reference(reference, kind, key, field)
    if len(content) != reference["bytes"] or hashlib.sha256(content).hexdigest() != reference["sha256"]:
        raise ValueError("History archive checksum mismatch")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=32 * 1024 * 1024)
    raw = decoder.decompress(content, max_length=reference["json_bytes"] + 1)
    if (not decoder.eof or decoder.unused_data or len(raw) != reference["json_bytes"]
            or hashlib.sha256(raw).hexdigest() != reference["json_sha256"]):
        raise ValueError("History archive integrity failure")
    return json.loads(raw)


class HistoryArchive:
    def __init__(self, supabase_url: str, headers: Mapping[str, str], client: httpx.Client):
        self._url = supabase_url.rstrip("/") + "/storage/v1/object"
        self._headers = {**headers, "Content-Type": "application/octet-stream"}
        self._client = client

    def download(self, reference, kind, key, field):
        validate_reference(reference, kind, key, field)
        # The reference cannot name another host, bucket, entity or field.
        url = f"{self._url}/authenticated/{BUCKET}/{reference['path']}"
        chunks = []
        received = 0
        try:
            with self._client.stream("GET", url, headers=self._headers,
                                     timeout=httpx.Timeout(60., connect=5.)) as response:
                if response.status_code != 200:
                    raise ValueError("History archive is unavailable")
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > reference["bytes"]:
                        raise ValueError("History archive response exceeds limit")
                    chunks.append(chunk)
            return unpack(reference, b"".join(chunks), kind, key, field)
        except (httpx.HTTPError, lzma.LZMAError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("History archive could not be restored") from exc

    def upload_verified(self, kind, key, field, value):
        reference, content = pack(kind, key, field, value)
        try:
            response = self._client.post(f"{self._url}/{BUCKET}/{reference['path']}",
                headers={**self._headers, "x-upsert": "false"}, content=content,
                timeout=httpx.Timeout(60., connect=5.))
            # A retry can encounter the same immutable content-addressed object.
            if response.status_code not in (200, 201, 400, 409):
                raise ValueError("History archive upload failed")
        except httpx.HTTPError as exc:
            raise ValueError("History archive upload failed") from exc
        if digest(self.download(reference, kind, key, field)) != digest(value):
            raise ValueError("Stored history archive changed values")
        return reference

    def hydrate(self, payload: Mapping[str, Any], kind: str, key: str) -> dict[str, Any]:
        restored = dict(payload)
        for field in fields_for(kind):
            if is_reference(restored.get(field)):
                restored[field] = self.download(restored[field], kind, key, field)
        return restored


def read_payload(repository, kind: str, key: str) -> dict[str, Any]:
    if kind not in TABLES or not KEY.fullmatch(key):
        raise ValueError("Invalid history identity")
    table, key_field, payload_field = TABLES[kind]
    rows = repository._json(repository._request("GET",
        f"/{table}?select={payload_field}&{key_field}=eq.{key}&limit=1",
        timeout=httpx.Timeout(60., connect=5.)))
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0].get(payload_field), Mapping):
        raise ValueError("History record is unavailable")
    return dict(rows[0][payload_field])


def logical_payload(archive, payload, kind, key):
    hydrated = archive.hydrate(payload, kind, key)
    return decode_shadow_payload(hydrated) if kind == "shadow" else hydrated


def archive_row(repository, row, kind, *, apply=False, backup=False, minimum_bytes=65536):
    key, original = row["entity_key"], row["payload"]
    if not KEY.fullmatch(key) or not isinstance(original, Mapping):
        raise ValueError("Invalid archive candidate")
    proposed = dict(original)
    archived_fields = 0
    archive_bytes = 0
    skipped_fields = 0
    # PostgreSQL JSONB includes whitespace that canonical JSON omits. Honor the
    # database's selected fields so borderline candidates cannot stall a batch.
    selected = row.get("archive_fields")
    if selected is not None and (not isinstance(selected, list)
            or any(field not in fields_for(kind) for field in selected)):
        raise ValueError("Invalid archive field selection")
    for field in fields_for(kind):
        value = original.get(field)
        if (field not in original or is_reference(value)
                or (field not in selected if selected is not None
                    else len(canonical(value)) < minimum_bytes)):
            continue
        try:
            reference, content = pack(kind, key, field, value)
        except ArchiveSizeLimit:
            skipped_fields += 1
            continue
        if apply or backup:
            reference = repository._history_archive.upload_verified(kind, key, field, value)
        proposed[field] = reference
        archived_fields += 1
        archive_bytes += len(content)
    if apply and archived_fields:
        before = digest(logical_payload(repository._history_archive, original, kind, key))
        if digest(logical_payload(repository._history_archive, proposed, kind, key)) != before:
            raise ValueError("Archive changed logical history")
        outcome = repository._rpc_row("replace_onflows_history_payload", {
            "p_kind": kind, "p_key": key, "p_expected_storage_hash": row["storage_hash"],
            "p_payload": proposed, "p_restore": False})
        if not isinstance(outcome, Mapping) or outcome.get("outcome") not in ("ARCHIVED", "UNCHANGED"):
            raise ValueError("History changed during archiving")
        stored = read_payload(repository, kind, key)
        if digest(logical_payload(repository._history_archive, stored, kind, key)) != before:
            raise ValueError("Archived history readback failed")
    return {"fields": archived_fields, "archive_bytes": archive_bytes, "skipped_fields": skipped_fields,
            "before_json_bytes": len(canonical(original)),
            "after_json_bytes": len(canonical(proposed))}


def run(repository, *, apply=False, max_rows=2, progress=lambda _: None, cursors=None):
    if type(max_rows) is not int or not 1 <= max_rows <= 20:
        raise ValueError("Invalid archive batch size")
    totals = {"scanned": 0, "fields": 0, "archive_bytes": 0,
              "skipped_fields": 0, "before_json_bytes": 0, "after_json_bytes": 0,
              "mode": "apply" if apply else "dry-run"}
    cursors = {} if cursors is None else cursors
    for kind in TABLES:
        after = cursors.get(kind, "")
        while totals["scanned"] < max_rows:
            rows = repository._json(repository._request("POST", "/rpc/read_onflows_history_archive_batch",
                json={"p_kind": kind, "p_after": after, "p_limit": 1},
                timeout=httpx.Timeout(60., connect=5.)))
            if not isinstance(rows, list):
                raise ValueError("Invalid history archive batch")
            if not rows:
                cursors[kind] = ""
                break
            row = rows[0]
            measured = archive_row(repository, row, kind, apply=apply)
            totals["scanned"] += 1
            for name, value in measured.items():
                totals[name] += value
            after = row["entity_key"]
            cursors[kind] = after
            progress(dict(totals))
    return totals


def restore(repository, kind, key):
    original = read_payload(repository, kind, key)
    hydrated = repository._history_archive.hydrate(original, kind, key)
    row = repository._rpc_row("read_onflows_history_storage_hash", {"p_kind": kind, "p_key": key})
    if not row or digest(read_payload(repository, kind, key)) != digest(original):
        raise ValueError("History changed during restoration")
    outcome = repository._rpc_row("replace_onflows_history_payload", {
        "p_kind": kind, "p_key": key, "p_expected_storage_hash": row["storage_hash"],
        "p_payload": hydrated, "p_restore": True})
    if not outcome or outcome.get("outcome") not in ("RESTORED", "UNCHANGED"):
        raise ValueError("History restoration conflict")
    if digest(read_payload(repository, kind, key)) != digest(hydrated):
        raise ValueError("History restoration readback failed")
    return outcome


def verify_backup(repository, kind, key):
    """Verify real Storage and repository readers without changing a DB row."""
    original = read_payload(repository, kind, key)
    proposed = dict(original)
    fields = 0
    size = 0
    for field in fields_for(kind):
        if field not in original or is_reference(original[field]):
            continue
        if len(canonical(original[field])) < 65536:
            continue
        reference = repository._history_archive.upload_verified(kind, key, field, original[field])
        proposed[field] = reference
        fields += 1
        size += reference["bytes"]
    if not fields:
        raise ValueError("Verification needs an inline series above the archive threshold")
    expected = logical_payload(repository._history_archive, original, kind, key)
    actual = (repository._decode_shadow(proposed, key) if kind == "shadow"
              else repository._hydrate_history(proposed, kind, key))
    if digest(actual) != digest(expected):
        raise ValueError("Archive repository reader changed scientific values")
    if digest(read_payload(repository, kind, key)) != digest(original):
        raise ValueError("History changed during backup verification")
    return {"mode": "backup-verified", "fields": fields, "archive_bytes": size}
