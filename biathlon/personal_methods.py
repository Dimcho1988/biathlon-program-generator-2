"""Private, bounded selections from canonical executable method definitions.

Each resolution starts with the immutable base, so saved data cannot introduce
speed anchors, capacity coefficients, new structures or cross-athlete state.
Source variants select a complete declared combination, never independent
upper bounds for duration, intensity and repetitions.
"""
from copy import deepcopy

VERSION = "athlete-personal-methods-v1"


def _source_variants(base):
    p = base.get("prescription") or {}
    standard = p.get("standard_variant") or {}
    variants = p.get("allowed_variants") or ([standard] if standard else [])
    return [deepcopy(v) for v in variants if v.get("sets", 1) == 1
            and v.get("speed_multiplier", p.get("speed_multiplier")) == standard.get("speed_multiplier", p.get("speed_multiplier"))
            and v.get("hrmax_fraction", p.get("hrmax_fraction")) == standard.get("hrmax_fraction", p.get("hrmax_fraction"))
            and isinstance(v.get("repetitions"), int)
            and (v.get("work") or p.get("work") or {}).get("unit") in {"seconds", "meters"}]


def constraints(base):
    """Machine-readable admissible controls; absence is an explicit rejection."""
    if base.get("personal_variant"):
        return {"eligible": False, "reason": "PERSONAL_VARIANT_REQUIRES_CANONICAL_BASE"}
    p = base.get("prescription")
    if p:
        if (p.get("structure", "INTERVALS") not in {"CONTINUOUS", "INTERVALS"}
                or any(p.get(k) for k in ("pattern", "tail", "phase_ranges"))
                or (p.get("sets") or {}).get("count", 1) != 1
                or (p.get("recovery") or {}).get("work_ratio") is not None):
            return {"eligible": False, "reason": "COMPOUND_OR_COUPLED_STRUCTURE_REQUIRES_CANONICAL_METHOD"}
        variants = _source_variants(base)
        if not variants:
            return {"eligible": False, "reason": "DECLARED_EXECUTABLE_VARIANT_REQUIRED"}
        works = [(v.get("work") or p["work"])["range"] for v in variants]
        reps = [v["repetitions"] for v in variants]
        return {"eligible": True, "work_unit": (variants[0].get("work") or p["work"])["unit"],
                "work_range": [min(w[0] for w in works), max(w[1] for w in works)],
                "repetitions": [min(reps), max(reps)],
                "recovery_seconds": list((p.get("recovery") or {}).get("seconds", [0, 0])),
                "selection_policy": "COMPLETE_DECLARED_VARIANT", "allowed_variants": variants}
    structure = base.get("structure")
    interval = base.get("interval_template") or base.get("interval_profile") or {}
    if base.get("zone") == "STR" or base.get("mixed_component") or structure not in {
            "CONTINUOUS", "TWO_REPETITIONS", "MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
        return {"eligible": False, "reason": "STRUCTURE_REQUIRES_CANONICAL_METHOD"}
    if structure in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
        if not interval.get("work_seconds"):
            return {"eligible": False, "reason": "EXPLICIT_INTERVAL_REQUIRED"}
        work = [interval["work_seconds"]] * 2
        reps = [interval["min_repetitions"], interval["max_repetitions"]]
        recovery = [interval["recovery_seconds"]] * 2
    else:
        count = base.get("repetitions", 2) if structure == "TWO_REPETITIONS" else 1
        work = [base[k] * 60 / count for k in ("min_work_min", "max_work_min")]
        reps = [count, count]
        recovery = [base.get("recovery_min", 0) * 60] * 2
    return {"eligible": True, "work_unit": "seconds", "work_range": work,
            "repetitions": reps, "recovery_seconds": recovery,
            "selection_policy": "CANONICAL_NATIVE_EXECUTION_BOUNDS"}


def _clamp(value, allowed):
    return [max(allowed[0], min(allowed[1], n)) for n in value]


def _normalize(item, base):
    c = constraints(base)
    if not c["eligible"]:
        raise ValueError(f"Method {base['id']}: {c['reason']}")
    if item.get("work_unit") and item["work_unit"] != c["work_unit"]:
        raise ValueError(f"Method {base['id']} requires work_unit={c['work_unit']}")
    result = deepcopy(item)
    result["work_unit"] = c["work_unit"]
    corrections = []
    for key in ("work_range", "repetitions", "recovery_seconds"):
        if item.get(key) is not None:
            result[key] = _clamp(item[key], c[key])
    if base.get("prescription"):
        p = base["prescription"]
        desired_work = result.get("work_range") or (p.get("standard_variant") or {}).get("work", p["work"])["range"]
        desired_reps = result.get("repetitions") or c["repetitions"]
        variants = _source_variants(base)
        selected = min(variants, key=lambda v: (
            sum(abs(a-b) for a, b in zip((v.get("work") or p["work"])["range"], desired_work)),
            max(desired_reps[0]-v["repetitions"], 0, v["repetitions"]-desired_reps[1]), v["repetitions"]))
        chosen_work = deepcopy((selected.get("work") or p["work"])["range"])
        matching = [v["repetitions"] for v in variants
                    if (v.get("work") or p["work"])["range"] == chosen_work
                    and desired_reps[0] <= v["repetitions"] <= desired_reps[1]] or [selected["repetitions"]]
        result.update(work_range=chosen_work, repetitions=[min(matching), max(matching)])
    for key in ("work_range", "repetitions", "recovery_seconds"):
        if item.get(key) is not None and list(item[key]) != list(result[key]):
            corrections.append({"field": key, "requested": list(item[key]), "applied": list(result[key]),
                                "reason": "CENTRAL_DECLARED_VARIANT" if base.get("prescription") else "CENTRAL_METHOD_BOUNDS"})
    return result, {"id": item["id"], "base_method_id": base["id"],
                    "status": "CORRECTED" if corrections else "ACCEPTED", "corrections": corrections}


def _bases(profile, source_entries=None, native_methods=None):
    if source_entries is None:
        from .method_library import entries
        source_entries = entries()
    if native_methods is None:
        from .training_methods import resolved_methods
        clean = {**profile, "method_library": {**(profile.get("method_library") or {}), "personal_methods": []}}
        native_methods = resolved_methods(clean)
    return {m["id"]: m for m in [*source_entries, *native_methods] if not m.get("personal_variant")}


def normalize_personal_methods(definitions, profile, source_entries=None, native_methods=None):
    """Return normalized definitions and server-derived correction reports."""
    bases = _bases(profile, source_entries, native_methods)
    ids, normalized, reports = set(), [], []
    for item in definitions or []:
        if item["id"] in ids or item["id"] in bases:
            raise ValueError(f"Duplicate personal training method: {item['id']}")
        ids.add(item["id"])
        base = bases.get(item["base_method_id"])
        if base is None:
            raise ValueError(f"Unknown canonical training method: {item['base_method_id']}")
        value, report = _normalize(item, base)
        normalized.append(value)
        reports.append(report)
    return normalized, reports


def _metadata(item, report):
    return {"scope": "ATHLETE", "base_method_id": item["base_method_id"],
            "validation_version": VERSION, "validation": report, "notes": item.get("notes")}


def source_methods(profile, bases):
    result = []
    by_id = {m["id"]: m for m in bases}
    for proposal in (profile.get("method_library") or {}).get("personal_methods", []):
        base = by_id.get(proposal["base_method_id"])
        if base is None:
            continue
        item, report = _normalize(proposal, base)
        method = deepcopy(base)
        p = method["prescription"]
        variants = [v for v in _source_variants(base)
                    if (v.get("work") or p["work"])["range"] == item["work_range"]
                    and item["repetitions"][0] <= v["repetitions"] <= item["repetitions"][1]]
        p.update(work={"unit": item["work_unit"], "range": item["work_range"]},
                 repetitions=item["repetitions"], source_repetitions_range=item["repetitions"],
                 standard_variant=deepcopy(variants[0]), allowed_variants=variants)
        if item.get("recovery_seconds") is not None:
            p["recovery"] = {**(p.get("recovery") or {}), "seconds": item["recovery_seconds"]}
        method.update(id=item["id"], title=item["name"], personal_variant=_metadata(item, report),
                      derived_from=[base["id"], *base.get("derived_from", [])])
        # Preserve explicit progression qualification and all source restrictions.
        result.append(method)
    return result


def native_methods(profile, bases):
    result = []
    by_id = {m["id"]: m for m in bases}
    for proposal in (profile.get("method_library") or {}).get("personal_methods", []):
        base = by_id.get(proposal["base_method_id"])
        if base is None:
            continue
        item, report = _normalize(proposal, base)
        method = deepcopy(base)
        method.update(id=item["id"], title=item["name"], variation_parent_id=base["id"],
                      personal_variant=_metadata(item, report), capacity_method=deepcopy(base.get("capacity_method") or base))
        c = constraints(base)
        work, reps = item.get("work_range") or c["work_range"], item.get("repetitions") or c["repetitions"]
        limits = {**method.get("execution_limits", {}), "min_work_min": work[0]*reps[0]/60,
                  "max_work_min": work[1]*reps[1]/60}
        for key in ("interval_template", "interval_profile"):
            if method.get(key):
                method[key].update(min_repetitions=reps[0], max_repetitions=reps[1])
                limits.update(min_repetitions=reps[0], max_repetitions=reps[1])
        method.update(min_work_min=limits["min_work_min"], max_work_min=limits["max_work_min"], execution_limits=limits)
        result.append(method)
    return result
