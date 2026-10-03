"""Public plan projection; private learning observations remain in server storage.

Viewing a plan does not grant access to recovery questionnaires or laboratory
records. Apply this projection at the HTTP boundary, including archived plans
and nested copies used by diagnostics/JSON export. Internal lifecycle services
keep the complete immutable payload for replay and approval.
"""
from biathlon.constants import COMPONENTS

REPORT_FIELDS = frozenset({
    "version", "mode", "status", "as_of", "effective_from", "expires_on",
    "summary", "evidence_count", "confidence",
    "examined_period_count", "archived_evidence_count", "out_of_support_count",
})
COMPONENT_FIELDS = frozenset({
    "volume_factor", "proposed_volume_factor", "intensity_delta", "proposed_intensity_delta",
    "reason", "confidence", "action", "evidence_count",
})
CONFIG_FIELDS = frozenset({"mode", "exploration_enabled", "max_volume_step_percent", "max_intensity_step"})
ADJUSTMENT_FIELDS = COMPONENT_FIELDS | frozenset({
    "applied", "eligible", "effective_from", "expires_on", "baseline_weekly_q", "applied_volume_factor",
    "baseline_weekly_effective", "baseline_position", "applied_position", "applied_intensity_delta",
    "capacity_recalculated", "applied_score_delta",
})
VALIDATION_FIELDS = frozenset({"status", "evaluated", "model_mae", "baseline_mae"})


def _scalars(value, fields):
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key in fields
            and (item is None or isinstance(item, (str, int, float, bool)))}


def public_learning(value):
    """Compact numerical summary, also used for duplicate persisted views."""
    if not isinstance(value, dict):
        return value if value is None or isinstance(value, str) else None
    if "status" not in value or "version" not in value:
        # Profile settings and numeric per-session adjustments share this key.
        return _scalars(value, CONFIG_FIELDS | ADJUSTMENT_FIELDS)
    result = _scalars(value, REPORT_FIELDS)
    components = value.get("components")
    result["components"] = {zone: _scalars(item, COMPONENT_FIELDS)
                            for zone, item in (components if isinstance(components, dict) else {}).items()
                            if zone in COMPONENTS and isinstance(item, dict)}
    result["validation"] = _scalars(value.get("validation") or {}, VALIDATION_FIELDS)
    limitations = value.get("limitations")
    result["limitations"] = [item for item in limitations if isinstance(item, str)] if isinstance(limitations, list) else []
    return result


def public_management(value, *, _parent=None):
    """Return an isolated JSON projection without changing the saved evidence."""
    if isinstance(value, list):
        return [public_management(item, _parent=_parent) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "response_revisions" and _parent in {"input_snapshot", "persistence"}:
            continue  # Entry kinds/keys can disclose laboratory dates too.
        result[key] = public_learning(item) if key == "individual_learning" else public_management(item, _parent=key)
    return result
