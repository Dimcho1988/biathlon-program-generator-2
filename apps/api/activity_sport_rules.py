"""Explicit athlete-owned corrections to provider sport labels at sync time.

No global name heuristic: the repository scopes the rules to one athlete. The
original provider sport and the matched rule remain in the catalog provenance.
"""
from collections.abc import Mapping

from vflat_b65.sports import supports_speed_load


def validate_rules(rows):
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("Invalid activity sport rules")
    for row in rows:
        if not isinstance(row, Mapping) or any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in ("id", "source_sport", "name_contains", "target_sport")
        ) or not supports_speed_load(row["target_sport"]):
            raise ValueError("Invalid activity sport rule")
    return tuple(dict(row) for row in rows)


def apply_sport_rules(detail, rules):
    source = detail.get("type") or detail.get("sport")
    name = detail.get("name")
    if not isinstance(source, str) or not isinstance(name, str):
        return detail
    matches = [r for r in rules if source.casefold() == r["source_sport"].casefold()
               and r["name_contains"].casefold() in name.casefold()]
    if not matches:
        return detail
    if len({r["target_sport"] for r in matches}) != 1:
        raise ValueError("Conflicting athlete sport rules")
    rule = matches[0]
    return {**detail, "type": rule["target_sport"], "onflows_sport_rule": {
        "rule_id": rule["id"], "provider_sport": source,
        "effective_sport": rule["target_sport"], "name_contains": rule["name_contains"],
    }}
