"""Private read-phase inputs; fields retain their existing lazy parsers."""
from collections.abc import Mapping
from copy import deepcopy

from .oauth_store import PersistentStoreFailure, _PlanningSettingsBundle


def valid_analysis_hints(hints):
    if not isinstance(hints, (list, tuple)):
        return []
    return [h for h in hints or [] if isinstance(h, dict)
            and isinstance(h.get("generation_id"), str) and h["generation_id"]
            and type(h.get("analysis_revision")) is int and h["analysis_revision"] > 0
            and isinstance(h.get("snapshot_fingerprint"), str)
            and len(h["snapshot_fingerprint"]) == 64
            and all(c in "0123456789abcdef" for c in h["snapshot_fingerprint"])]


def parse_analysis(row):
    if row is None:
        return None
    snapshot, revision, generation = row.get("snapshot_payload"), row.get("revision"), row.get("generation_id")
    if (not isinstance(snapshot, Mapping) or type(revision) is not int or revision < 0
            or (generation is not None and not isinstance(generation, str))):
        raise PersistentStoreFailure("Stored active analysis is invalid")
    return {"generation_id": generation, "revision": revision, "analysis_as_of": row.get("analysis_as_of"),
            "activated_at": row.get("activated_at"), "snapshot_payload": dict(snapshot)}


def parse_analysis_metadata(row):
    if row is None:
        return None
    if (type(row.get("revision")) is not int or row["revision"] < 0
            or (row.get("generation_id") is not None and
                (not isinstance(row["generation_id"], str) or not row["generation_id"]))
            or any(row.get(k) is not None and not isinstance(row[k], str)
                   for k in ("analysis_as_of", "activated_at"))):
        raise PersistentStoreFailure("Stored active analysis metadata is invalid")
    return {k: row.get(k) for k in ("generation_id", "revision", "analysis_as_of", "activated_at")}


class PlanningInputBundle:
    _KEYS = {"schema_version", "athlete_alias", "settings_rows", "profile_rows", "model_entries",
             "model_history_exhausted", "response_entries", "response_history_exhausted", "analysis"}

    def __init__(self, value, alias, mode, hints):
        if (not isinstance(value, dict) or set(value) != self._KEYS
                or value["schema_version"] != "planning-inputs-v1" or value["athlete_alias"] != alias
                or not isinstance(mode, str) or mode not in {"FULL", "HINTED"}):
            raise PersistentStoreFailure("Invalid planning input bundle")
        self._value, self._mode, self._hints = value, mode, deepcopy(valid_analysis_hints(hints))

    def _rows(self, key):
        rows = self._value[key]
        if not isinstance(rows, list) or len(rows) > 1 or any(not isinstance(row, dict) for row in rows):
            raise PersistentStoreFailure("Invalid planning input bundle")
        return rows

    def settings_bundle(self):
        return _PlanningSettingsBundle(self._rows("settings_rows"))

    def profile_rows(self):
        return deepcopy(self._rows("profile_rows"))

    def _entries(self, section):
        exhausted = self._value[f"{section}_history_exhausted"]
        if type(exhausted) is not bool:
            raise PersistentStoreFailure("Invalid planning input bundle")
        if exhausted:
            raise PersistentStoreFailure("Model history exceeds bounded read" if section == "model"
                                         else "Response history exceeds the bounded read")
        rows = self._value[f"{section}_entries"]
        keys = {"kind", "entry_key", "revision", "payload", "recorded_at"}
        if section == "response":
            keys.add("observed_date")
        if (not isinstance(rows, list) or len(rows) >= 10000 or any(
                not isinstance(row, dict) or set(row) != keys
                or not isinstance(row["kind"], str) or not isinstance(row["entry_key"], str)
                or type(row["revision"]) is not int or row["revision"] < 1
                or not isinstance(row["payload"], dict) or not isinstance(row["recorded_at"], str)
                or (section == "response" and not isinstance(row["observed_date"], str)) for row in rows)):
            raise PersistentStoreFailure("Invalid planning input bundle")
        identities = [(row["kind"], row["entry_key"]) for row in rows]
        if len(set(identities)) != len(identities):
            raise PersistentStoreFailure("Invalid planning input bundle")
        return deepcopy(rows)

    def model_entries(self):
        return self._entries("model")

    def response_entries(self):
        return self._entries("response")

    def _analysis(self):
        value = self._value["analysis"]
        if value is None:
            return None
        if (not isinstance(value, dict) or set(value) != {"mode", "row"}
                or not isinstance(value["mode"], str) or value["mode"] not in {"FULL", "METADATA"}
                or not isinstance(value["row"], dict)):
            raise PersistentStoreFailure("Invalid planning input bundle")
        row = value["row"]
        keys = {"generation_id", "revision", "analysis_as_of", "activated_at"}
        if value["mode"] == "FULL":
            keys.add("snapshot_payload")
        if set(row) != keys:
            raise PersistentStoreFailure("Invalid planning input bundle")
        if value["mode"] == "METADATA":
            metadata = parse_analysis_metadata(row)
            if (self._mode != "HINTED" or "snapshot_payload" in row or not metadata["generation_id"]
                    or metadata["revision"] <= 0 or not any(
                        metadata["generation_id"] == h["generation_id"]
                        and metadata["revision"] == h["analysis_revision"]
                        and metadata["analysis_as_of"] == h.get("analysis_as_of") for h in self._hints)):
                raise PersistentStoreFailure("Invalid planning input bundle")
        return value

    def active_analysis(self):
        value = self._analysis()
        if value is None:
            return None
        if value["mode"] != "FULL":
            raise PersistentStoreFailure("Captured planning analysis has no full snapshot")
        return parse_analysis(value["row"])

    def active_analysis_metadata(self):
        value = self._analysis()
        return None if value is None else parse_analysis_metadata(value["row"])
