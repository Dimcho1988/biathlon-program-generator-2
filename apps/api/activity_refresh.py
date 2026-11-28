"""Small, generation-pinned inputs for appending chronological recordings."""
import base64
from datetime import datetime
from hashlib import sha256
import json
import zlib

from intervals_inspector.intervals_client import ACTIVITY_LIST_LIMIT
from intervals_inspector.real_data_source import _activity_date, _activity_id


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def pack(record):
    raw = json.dumps(record, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) <= 256 * 1024:
        return base64.b64encode(zlib.compress(raw, level=3)).decode()
    return None


def unpack(value):
    if not isinstance(value, str) or len(value) > 350_000:
        return {}
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(value, validate=True), 256 * 1024 + 1)
        if decoder.eof and len(raw) <= 256 * 1024:
            result = json.loads(raw)
            if isinstance(result, dict):
                return result
    except (ValueError, zlib.error):
        pass
    return {}


def _timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None


def calculation_version(listed):
    if isinstance(listed, list) and any(isinstance(r, dict) and (r.get("analyzed") or r.get("icu_sync_date")) for r in listed):
        from .preparation_identity import source_fingerprint
        return source_fingerprint("activity")
    return None


class ActivityRefresh:
    def __init__(self, listed, catalog, previous, *, start, end, key_for, release, sport_rules):
        self.prior = {}
        self.saved = {}
        self.refs = {}
        self.signatures = {}
        self.records = {}
        self.reused = set()
        if not release or not isinstance(listed, list) or len(listed) >= ACTIVITY_LIST_LIMIT:
            return
        inputs = {}
        for row in listed:
            if not isinstance(row, dict):
                return
            day, utc, provider_id = _activity_date(row), _timestamp(row.get("start_date")), _activity_id(row)
            if day is None or utc is None or utc.tzinfo is None or not provider_id or not start <= day <= end:
                return
            key = key_for(provider_id)
            if key in inputs:
                return
            inputs[key] = (day, utc)
            # A synchronized or analyzed revision plus the full list-row digest
            # catches both stream revisions and edits to recorded metadata.
            if any(_timestamp(row.get(field)) for field in ("analyzed", "icu_sync_date")):
                self.signatures[key] = digest([release, sport_rules, row])
        history = (previous or {}).get("load_history") or {}
        if not history or not history.get("period_start") or history["period_start"] > start.isoformat() or history["period_end"] > end.isoformat():
            return
        expected = {a["activity_ref"] for a in history["activities"] if start.isoformat() <= a["date"] <= end.isoformat()}
        prior = {row.get("provider_activity_key"): row for row in catalog}
        if not expected or len(prior) != len(catalog) or {row["activity_ref"] for row in catalog} != expected:
            return
        if not prior.keys() <= inputs.keys():
            return
        added = inputs.keys() - prior.keys()
        if added:
            # Local dates group daily loads; UTC orders the recordings across time zones.
            first_new_day = min(inputs[key][0] for key in added)
            first_new_start = min(inputs[key][1] for key in added)
            if any(inputs[key][0] > first_new_day or inputs[key][1] >= first_new_start for key in prior):
                return
        for key, row in prior.items():
            saved = unpack(row.get("refresh_checkpoint"))
            ref = row["activity_ref"]
            if (self.signatures.get(key) and self.signatures[key] == saved.get("signature")
                    and saved.get("canonical_key") == row.get("latest_canonical_run_key")
                    and saved.get("shadow_key") == row.get("latest_shadow_run_key")
                    and row.get("local_date") == inputs[key][0].isoformat()):
                self.prior[ref] = row
                self.saved[ref] = saved
            self.refs[key] = ref


def compact_summary(summary):
    if summary is None:
        return None
    comparison = summary.get("shadow_model_comparison") or {}
    def rows(name):
        return (comparison.get(name) or {}).get("rows", ())
    fields = ("zone", "T_z", "T_eq_z", "E_z", "cascade", "spillover_received", "direct_ratio",
              "mean_effective_hr_bpm", "average_minute_value_percent", "tmax_minutes", "tmax_source")
    return {
        "activity_metadata": summary["activity_metadata"],
        "model_status": summary["model_status"],
        "onflows_load_analysis": {"hr_coverage_percent": summary["onflows_load_analysis"].get("hr_coverage_percent")},
        "unified_load": {k: v for k, v in (summary.get("unified_load") or {}).items() if k != "zones"},
        "shadow_model_comparison": {
            "baseline": {"rows": [{"zone": r["zone"], "E_z": r["E_z"]} for r in rows("baseline")]},
            "experimental": {"rows": [{k: r.get(k) for k in fields} for r in rows("experimental")]},
        },
    }
