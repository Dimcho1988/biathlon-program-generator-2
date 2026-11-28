"""Nutrition dialogue projections of already authorized, saved application data."""
from copy import deepcopy
import re


def is_nutrition_request(message):
    return bool(re.search(r"меню|хранител|хранен|\bям\b|\bяде\w*|алерги|непоносим|веган|вегетари", message.lower()))


def compact_context(context):
    """Keep intake evidence and training timing, not the full coaching manual.

    This projection is only for a nutrition question. Detailed training reads
    and every write contract retain their original data and permissions.
    """
    kind = context.get("query_kind")
    if kind == "day":
        fields = {"session_id", "title", "sport", "sport_label_bg", "zone", "purpose",
                  "main_work_minutes", "total_minutes", "double_threshold", "paired_session",
                  "mixed_component", "is_key_session"}
        block_fields = {"kind", "label", "zone", "duration_min", "duration_s", "passive_rest",
                        "recovery_type", "repeat_count", "sequence"}

        def block_summary(block):
            return {key: [block_summary(row) for row in value] if key == "sequence" else deepcopy(value)
                    for key, value in block.items() if key in block_fields}

        result = {key: deepcopy(value) for key, value in context.items()
                  if key not in {"sessions", "day_explanation", "input_snapshot", "source"}}
        result["sessions"] = [{**{key: deepcopy(value) for key, value in row.items() if key in fields},
                               "blocks": [block_summary(b) for b in row.get("blocks", [])]}
                              for row in context.get("sessions", [])]
        result["context_summary"] = "NUTRITION_TIMING: saved sessions and durations; coaching details remain available through day/method reads. No session start time is inferred."
        return result
    if kind == "energy" and isinstance(context.get("data"), dict) and not context.get("section"):
        result = deepcopy(context)
        data = result["data"]
        # Preserve targets, basis, preferences, source dates, status and race
        # decisions verbatim. Per-activity oxidation is not a menu target.
        for day in [*data.get("days", []), data.get("tomorrow")]:
            if isinstance(day, dict):
                day.pop("activities", None)
        data.pop("profiles", None)
        result["context_summary"] = "NUTRITION_TARGETS: full saved targets and basis; per-activity detail remains available through energy section reads."
        return result
    return context


def render_targets(contexts):
    """On synthesis failure, show nutrition facts without inventing a menu."""
    lines = []
    for context in contexts:
        if context.get("query_kind") != "energy":
            continue
        data = context.get("data") or {}
        if not isinstance(data, dict):
            continue
        for day in data.get("days", []):
            targets = day.get("targets") or {}
            basis = day.get("nutrition_basis") or {}
            label = day.get("day") or day.get("date") or basis.get("target_day") or "избрания ден"
            if not isinstance(targets.get("kcal"), (int, float)):
                continue
            values = [f"Хранителна цел за {label}: {targets['kcal']:.0f} kcal."]
            for key, title in (("carbohydrate_g", "Въглехидрати"), ("protein_g", "Белтъчини"), ("fat_g", "Мазнини")):
                if isinstance(targets.get(key), (int, float)):
                    values.append(f"{title}: {targets[key]:.0f} g.")
            lines.append(" ".join(values))
            lines.extend(str(note) for note in basis.get("notes", []))
    if not lines:
        lines.append("Не успях да получа готова хранителна цел за поисканата дата.")
    lines.append("Конкретното меню не е изготвено поради проблем с обработката на заявката. Можеш да повториш искането; хранителните цели са достъпни в „Енергия и хранене“.")
    return "\n\n".join(dict.fromkeys(lines))[:18000]
