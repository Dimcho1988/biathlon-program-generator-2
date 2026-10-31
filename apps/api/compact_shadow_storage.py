"""Explicit, resumable maintenance; dry-run unless --apply is supplied.

Run on the existing worker, using its existing environment. No credentials or
athlete data are printed. Does not delete records, prune history or call Intervals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from .oauth_store import PersistentStoreFailure, SupabasePilotRepository
from .shadow_storage import SERIES_FIELDS, decode_shadow_payload, encode_shadow_payload


def logical_digest(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def compact_payload(original: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    from .history_archive import is_reference
    if any(is_reference(original.get(field)) for field in SERIES_FIELDS):
        return dict(original), logical_digest(original)
    logical = decode_shadow_payload(original)
    proposed = encode_shadow_payload(logical)
    # Keep any existing representation that is already smaller. This also
    # avoids expanding legacy tiny arrays while converting other series.
    for field in SERIES_FIELDS:
        if field not in original:
            continue
        old_size = len(json.dumps(original[field], separators=(",", ":")))
        new_size = len(json.dumps(proposed[field], separators=(",", ":")))
        if new_size >= old_size:
            proposed[field] = original[field]
    digest = logical_digest(logical)
    if logical_digest(decode_shadow_payload(proposed)) != digest:
        raise ValueError("Compaction changed logical data")
    return proposed, digest


def run(repository: SupabasePilotRepository, *, apply: bool = False,
        after: str = "", max_rows: int = 10, progress=print) -> dict[str, Any]:
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    totals: dict[str, Any] = dict(scanned=0, changed=0, unchanged=0,
                                before_json_bytes=0, after_json_bytes=0,
                                verified=0, last_run_key=after, mode="apply" if apply else "dry-run")
    timeout = httpx.Timeout(120.0, connect=5.0)
    while totals["scanned"] < max_rows:
        response = repository._request("POST", "/rpc/read_onflows_shadow_compaction_batch",
            json={"p_after_run_key": totals["last_run_key"], "p_limit": 1}, timeout=timeout)
        rows = repository._json(response)
        if not isinstance(rows, list):
            raise PersistentStoreFailure("Invalid compaction batch")
        if not rows:
            totals["complete"] = True
            break
        row = rows[0]
        original = row["result_payload"]
        proposed, digest = compact_payload(original)
        old_bytes = len(json.dumps(original, separators=(",", ":")).encode())
        new_bytes = len(json.dumps(proposed, separators=(",", ":")).encode())
        changed = proposed != original
        if apply and changed:
            outcome = repository._rpc_row("compact_onflows_shadow_run", {
                "p_run_key": row["run_key"], "p_expected_storage_hash": row["storage_hash"],
                "p_result_payload": proposed,
            })
            if not isinstance(outcome, Mapping) or outcome.get("outcome") not in ("COMPACTED", "UNCHANGED"):
                raise PersistentStoreFailure("Compaction conflict; no cursor advance")
            stored = repository._json(repository._request("GET",
                "/onflows_activity_derived_runs?select=result_hash,result_payload"
                f"&run_key=eq.{quote(row['run_key'], safe='')}&limit=1", timeout=timeout))
            if (not isinstance(stored, list) or len(stored) != 1
                    or stored[0]["result_hash"] != row["result_hash"]
                    or logical_digest(decode_shadow_payload(stored[0]["result_payload"])) != digest):
                raise PersistentStoreFailure("Stored compaction integrity check failed")
            totals["verified"] += 1
        totals["scanned"] += 1
        totals["changed" if changed else "unchanged"] += 1
        totals["before_json_bytes"] += old_bytes
        totals["after_json_bytes"] += new_bytes
        totals["last_run_key"] = row["run_key"]
        if totals["scanned"] % 25 == 0:
            progress(json.dumps(totals, sort_keys=True))
    progress(json.dumps(totals, sort_keys=True))
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--after", default="", help="Last successfully verified run key")
    parser.add_argument("--max-rows", type=int, default=10)
    args = parser.parse_args()
    repo = SupabasePilotRepository.from_environment()
    try:
        run(repo, apply=args.apply, after=args.after, max_rows=args.max_rows)
    except Exception as exc:
        # Never dump SQL responses, payloads or server environment into logs.
        print(json.dumps({"status": "stopped", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
    finally:
        repo._client.close()


if __name__ == "__main__":
    main()
