"""Executable coach profiles alongside the v0.8 methodological specification.

Parent-card constraints were reviewed against v0.7 (Library revision 8).
Concrete pilot choices below are marked as implementation defaults, not as
scientifically validated prescriptions. The older audit does not validate
this subset. Z3 never receives the Z4/Z5 interval exception.
"""
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from . import adaptive_methods

VERSION = "training-methods-v11-expert-role-budget"
VARIETY_VERSION = "onflows-bounded-method-variety-v1"
COMMON = ("RE_ENTRY", "GENERAL_PREPARATION", "SPECIAL_PREPARATION", "COMPETITION")
METHODS = (
    {"id": "RUN-REC-EASY-01", "title": "Леко възстановително бягане", "zone": "Z1",
     "sports": ("Run",), "purpose": "RECOVERY", "position": .35,
     "structure": "CONTINUOUS", "periods": (*COMMON, "PRECOMPETITION", "TRANSITION"),
     "min_work_min": 10., "max_work_min": 30., "warmup_min": 0., "cooldown_min": 0.,
     "source_id": "RUN-REC-EASY-01", "source_version": "v0.2",
     "instructions": "Много леко, с удобно свободно дишане. Без ускорения и без цел да се запълни свободното време."},
    {"id": "END-CROSS-TRAIN-01-RECOVERY", "title": "Лека възстановителна работа на ски или ролкови ски", "zone": "Z1",
     "sports": ("NordicSki", "RollerSki"), "purpose": "RECOVERY", "position": .35,
     "structure": "CONTINUOUS", "periods": (*COMMON, "PRECOMPETITION", "TRANSITION"),
     "min_work_min": 10., "max_work_min": 30., "warmup_min": 0., "cooldown_min": 0.,
     "source_id": "END-CROSS-TRAIN-01", "source_version": "v0.2",
     "adaptation": "Лек профил в Z1; предсъстезателен/преходен период са изрично пилотно разширение на родителската карта.",
     "instructions": "Спокойна техника и много леко усилие в Z1. Без тежки изкачвания и ускорения."},
    {"id": "END-LONG-Z1-01-MAINTAIN", "title": "Поддържаща лека аеробна работа", "zone": "Z1",
     "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "MAINTENANCE", "position": .65,
     "structure": "CONTINUOUS", "periods": (*COMMON, "PRECOMPETITION"),
     "min_work_min": 15., "max_work_min": 90., "warmup_min": 5., "cooldown_min": 5.,
     "source_id": "END-LONG-Z1-01", "source_version": "v0.2",
     "adaptation": "Поддържане в предсъстезателен период е явно пилотно разширение; само крайният тейпър намалява обема.",
     "instructions": "Равномерно спокойно усилие. Поддържай избрания пулсов диапазон без финално ускорение."},
    {"id": "END-LONG-Z1-01-BUILD", "title": "Продължителна лека аеробна работа", "zone": "Z1",
     "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": .75,
     "structure": "CONTINUOUS", "periods": COMMON,
     "min_work_min": 20., "max_work_min": 120., "warmup_min": 5., "cooldown_min": 5.,
     "source_id": "END-LONG-Z1-01", "source_version": "v0.2",
     "instructions": "Равномерна работа в Z1. Продължителността се ограничава от капацитета, поносимостта към средството и общия бюджет."},
    {"id": "END-Z2-PROG-01", "title": "Постепенно нарастваща аеробна работа в Z2", "zone": "Z2",
     "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": .85,
     "structure": "THREE_PROGRESSIVE_BLOCKS", "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION"),
     "min_work_min": 15., "max_work_min": 75., "warmup_min": 10., "cooldown_min": 5.,
     "source_id": "END-Z2-PROG-01", "source_version": "v0.2.1",
     "adaptation": "Три равни части в долна, средна и горна Z2 са пилотна параметризация; източникът не задава равни пропорции.",
     "instructions": "След загрявката премини плавно от долна през средна към горна Z2. Без преминаване в Z3."},
    {"id": "END-THR-TIME-01", "title": "Две контролирани части в Z3", "zone": "Z3",
     "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": .75,
     "structure": "TWO_REPETITIONS", "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION"),
     "min_work_min": 12., "max_work_min": 24., "warmup_min": 12., "cooldown_min": 8.,
     "repetitions": 2, "recovery_min": 3.,
     "source_id": "END-THR-TIME-01", "source_version": "v0.2.1",
     "adaptation": "Две повторения по 6–12 минути и 3 минути активна почивка са ограничен вариант в диапазоните на картата.",
     "instructions": "Две еднакви части в Z3 с 3 минути леко движение между тях. Запази резерв. Сумарната работа остава в общата доза; почивката не разрешава допълнителен товар."},
)

EXERCISES = (
    "Контролиран клек със собствено тегло", "Редуващ се напад назад", "Повдигане на пръсти",
    "Планк", "Страничен планк — смени страната по средата", "Редуване ръка–крак от тилен лег",
    "Лицеви опори на стена", "Лопатъчни лицеви опори на стена", "Повдигане на ръце в Y от лицев лег",
)


def _bounded_variants(methods):
    """Add actual structures, without activating unresolved source examples.

    The parent supplies the supported effort/capacity, not new physiological
    evidence. These are transparent coach choices; all session budgets remain
    the engine's responsibility. Append-only keeps the established defaults.
    """
    result = []
    by_id = {m["id"]: m for m in methods}
    aerobic = by_id["END-CROSS-TRAIN-01-Z2"]
    result.append({**deepcopy(aerobic), "id": "ONFLOWS-Z2-TWO-PARTS-V1",
        "title": "Две равномерни аеробни части в Z2", "purpose": "BUILDING",
        "structure": "TWO_REPETITIONS", "min_work_min": 20., "repetitions": 2,
        "recovery_min": 2., "variation_parent_id": aerobic["id"],
        "implementation_profile": VARIETY_VERSION,
        "instructions": "Две равни части в Z2, разделени с 2 минути леко движение в Z1. Запази еднакво усилие и технически резерв в двете части.",
        "adaptation": "Треньорски вариант v1 на разрешената равномерна Z2: същата интензивност и максимум 90 мин обща работа, разделена в две части. Почивката се отчита отделно; не увеличава процентната доза."})
    easy = by_id["END-LONG-Z1-01-BUILD"]
    result.append({**deepcopy(easy), "id": "ONFLOWS-Z1-TWO-PARTS-V1",
        "title": "Лека аеробна работа в две части", "structure": "TWO_REPETITIONS",
        "min_work_min": 20., "max_work_min": 90., "repetitions": 2, "recovery_min": 2.,
        "variation_parent_id": easy["id"], "implementation_profile": VARIETY_VERSION,
        "instructions": "Две равни леки части в Z1 с 2 минути много леко движение между тях. Използвай прехода за проверка на стойката и техниката; не повишавай усилието след него.",
        "adaptation": "Треньорски вариант v1 на продължителната Z1: две части със същия целеви пулс, до 90 мин обща работа. Преходът е в общото време и товара; няма втори независим дозов бюджет."})
    threshold = by_id["END-THR-TIME-01-FLEX"]
    for structure, suffix, title, minimum, maximum, rest, instructions in (
        ("THRESHOLD_LONG", "LONG", "Прагови части по 6–10 минути", 12., 60., 1.,
         "Равномерни части по 6–10 минути в Z3 с 1 минута леко движение между тях. Запази контролирано усилие и резерв; почивката не е разрешение за по-висока скорост."),
        ("THRESHOLD_SHORT", "SHORT", "Кратко прагово редуване: 1 мин / 30 сек", 6., 20., .5,
         "Една минута контролирано прагово усилие, 30 секунди леко движение в Z1. Поддържай зададеното усилие и повторяема техника. Не гони моментен пулс и не превръщай отсечките в спринтове."),
    ):
        result.append({**deepcopy(threshold), "id": f"ONFLOWS-Z3-{suffix}-REPETITIONS-V1",
            "title": title, "structure": structure, "min_work_min": minimum,
            "max_work_min": maximum, "recovery_min": rest,
            "variation_parent_id": threshold["id"], "capacity_method": deepcopy(threshold),
            "implementation_profile": VARIETY_VERSION, "instructions": instructions,
            "adaptation": "Самостоятелен треньорски вариант v1 от вече разрешения прагов профил и изпълнимата структура за двоен праг. Капацитетът е от същото индивидуално усилие; общата Z3 работа остава процент от непрекъснатия Tmax, без интервален множител и без изискване за второ занимание."})
    for parent in methods:
        if parent["structure"] not in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
            continue
        key = "interval_template" if parent["structure"] == "MODEL_INTERVALS" else "interval_profile"
        p = parent[key]
        # Subdivision changes structure at the same supported effort. It never
        # raises the parent's cumulative work or shortens its recovery.
        if p["work_seconds"] < 30 or p["min_repetitions"] * 2 > 24:
            continue
        child = deepcopy(parent)
        cp = child[key]
        cp.update(work_seconds=p["work_seconds"] / 2,
                  min_repetitions=p["min_repetitions"] * 2,
                  max_repetitions=min(24, p["max_repetitions"] * 2))
        if p.get("speed_time_duration_ratio") is not None:
            cp["speed_time_duration_ratio"] = p["speed_time_duration_ratio"] * 2
        child.update(id=parent["id"] + "-SPLIT-V1",
            title=f"По-кратки повторения в {parent['zone']} с пълна активна почивка",
            min_work_min=cp["min_repetitions"] * cp["work_seconds"] / 60,
            max_work_min=cp["max_repetitions"] * cp["work_seconds"] / 60,
            variation_parent_id=parent["id"], variation_policy={
                "version": VARIETY_VERSION, "work_duration_ratio": .5,
                "recovery_duration_ratio": 1., "max_total_work_ratio": 1.,
                "effort_anchor": "UNCHANGED_PARENT_CAPACITY",
                "validation": "COACH_STRUCTURE_NOT_EQUIVALENT_PHYSIOLOGICAL_RESPONSE"},
            instructions="По-кратки, повторяеми отсечки при същото зададено усилие. Използвай цялата предписана активна почивка. Запази поне две качествени повторения в резерв; не ускорявай заради по-кратката отсечка или за да достигнеш пулсово число.",
            adaptation="Треньорски вариант v1: работната отсечка е разделена на две, броят е удвоен до максимум 24, а всяка почивка запазва продължителността от родителския профил. Общата работа и множителят за капацитета не нарастват. Това не твърди еднакъв физиологичен отговор; всички сесийни и компонентни бюджети се проверяват отново.")
        result.append(child)
    return result


def method_family(method):
    """Group actual execution patterns so renamed methods cannot fake variety."""
    structure = method["structure"]
    family = {
        "CONTINUOUS": "STEADY", "THREE_PROGRESSIVE_BLOCKS": "PROGRESSIVE",
        "TWO_REPETITIONS": "TWO_PARTS", "THRESHOLD_REPETITIONS": "LONG_REPETITIONS",
        "THRESHOLD_LONG": "LONG_REPETITIONS", "THRESHOLD_SHORT": "SHORT_REPETITIONS",
        "ALTERNATING": "AEROBIC_ALTERNATING", "CRUISE_ALTERNATING": "AEROBIC_CRUISE",
        "AEROBIC_SUPPORT": "EASY_THRESHOLD", "THRESHOLD_HIGH": "THRESHOLD_HIGH",
        "STRENGTH_CIRCUIT": "STRENGTH_CIRCUIT", "AEROBIC_STRENGTH": "AEROBIC_STRENGTH",
        "MIXED_AEROBIC": "STEADY" if method.get("mixed_variant") == "STEADY" else "SHORT_REPETITIONS",
    }.get(structure, structure)
    if structure in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
        p = method.get("interval_template") or method["interval_profile"]
        family = ("MICRO_INTERVALS" if p["work_seconds"] <= 30 else
                  "SHORT_INTERVALS" if p["work_seconds"] <= 120 else "LONG_INTERVALS")
        if p["recovery_seconds"] >= 1.5 * p["work_seconds"]:
            family += "_FULL_REST"
    prefix = "MIXED_" if method.get("mixed_component") else ""
    suffix = "_NMS" if method.get("neuromuscular_profile") else ""
    return f"{method['zone']}_{prefix}{family}{suffix}"


def resolved_methods(profile):
    """Resolve individual profiles and bounded, exposure-gated pilot methods.

    No elite source variant inherits numeric defaults from its parent card.
    """
    methods = deepcopy(list(METHODS))
    if (profile.get("load_progression") or {}).get("enabled"):
        methods.append({"id": "ONFLOWS-Z3-SUPPORT-01", "title": "Лека тренировка с поддържаща част в Z3", "zone": "Z3",
            "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "SUPPORTING", "position": .55,
            "structure": "AEROBIC_SUPPORT", "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION"),
            "min_work_min": 6., "max_work_min": 20., "warmup_min": 8., "cooldown_min": 5.,
            "source_id": "ONFLOWS-Z3-SUPPORT-01", "source_version": "1",
            "instructions": "Лека аеробна част и кратка контролирана част в Z3. Запази резерв; без финално ускоряване.",
            "adaptation": "Начална треньорска настройка: Z1 е два пъти времето в Z3; всеки компонент остава в собствения си поддържащ бюджет, с обща проверка на товара и преливането. Използва само оставащия бюджет след ключовите задачи."})
    methods.extend([
        {"id": "END-CROSS-TRAIN-01-Z2", "title": "Равномерна аеробна работа в Z2", "zone": "Z2",
         "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "MAINTENANCE", "position": .5,
         "structure": "CONTINUOUS", "periods": (*COMMON, "PRECOMPETITION"),
         "min_work_min": 15., "max_work_min": 90., "warmup_min": 8., "cooldown_min": 5.,
         "source_id": "END-CROSS-TRAIN-01", "source_version": "0.2",
         "adaptation": "Равномерна Z2 с поддържаща доза; конкретните граници са начални треньорски настройки.",
         "instructions": "Равномерно умерено усилие и запазен дихателен резерв. Не преминавай към прагова работа."},
        {"id": "END-THR-TIME-01-FLEX", "title": "Прагoви интервали по време", "zone": "Z3",
         "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": .75,
         "structure": "THRESHOLD_REPETITIONS", "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"),
         "min_work_min": 12., "max_work_min": 60., "warmup_min": 12., "cooldown_min": 8.,
         "recovery_min": 2., "source_id": "END-THR-TIME-01", "source_version": "0.2.1",
         "adaptation": "2–8 повторения по 6–12 минути, 2 минути активна почивка. Общата работа остава в процентната доза за Z3.",
         "instructions": "Контролирано прагово усилие с технически резерв. Еднакъв ритъм в повторенията; без финал до изчерпване."},
        {"id": "END-ALT-10-05-01", "title": "Аеробно редуване: 10 мин Z2 / 5 мин Z1", "zone": "Z2",
         "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": .7,
         "structure": "CRUISE_ALTERNATING", "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION"),
         "min_work_min": 45., "max_work_min": 150., "warmup_min": 10., "cooldown_min": 5.,
         "source_id": "END-ALT-10-05-01", "source_version": "0.2",
         "adaptation": "3–10 цели цикъла. Компонентите имат отделни методни бюджети; общият товар с преливането се проверява съвместно.",
         "instructions": "10 минути устойчиво усилие в Z2, последвани от 5 минути осезаемо по-леко движение в Z1. Не съкращавай леката част."},
    ])
    methods.append({"id": "END-THR-LONG-01", "title": "Продължителна контролирана прагова работа", "zone": "Z3",
        "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": .65,
        "structure": "CONTINUOUS", "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION"),
        "min_work_min": 15., "max_work_min": 40., "warmup_min": 12., "cooldown_min": 8.,
        "source_id": "END-THR-LONG-01", "source_version": "0.2",
        "instructions": "Влез плавно в работното усилие. Поддържай равен ритъм и технически резерв; това не е тест до изчерпване.",
        "adaptation": "Един блок 15–40 минути в рамките на индивидуалния процентен бюджет; позицията в зоната следва дисциплината."})
    methods.append({"id": "END-ALT-Z1Z2-01", "title": "Редуване на леко и умерено усилие", "zone": "Z2",
        "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": .7,
        "structure": "ALTERNATING", "periods": ("RE_ENTRY", "GENERAL_PREPARATION", "SPECIAL_PREPARATION"),
        "min_work_min": 20., "max_work_min": 60., "warmup_min": 8., "cooldown_min": 5.,
        "source_id": "END-ALT-Z1Z2-01", "source_version": "0.2",
        "instructions": "Два цикъла Z1 → Z2, с плавна промяна на усилието. Запази свободно дишане в леките части.",
        "adaptation": "Два цикъла с равни дялове и един споделен бюджет; начална настройка."})
    if profile.get("strength_enabled"):
        strength = {"id": "STR-CIRCUIT-RUN-01", "title": "Обща сила и стабилност", "zone": "STR",
            "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": 0.,
            "structure": "STRENGTH_CIRCUIT", "periods": ("RE_ENTRY", "GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"),
            "min_work_min": 3., "max_work_min": profile.get("strength_circuits", 2) * 3.,
            "warmup_min": 10., "cooldown_min": 5., "source_id": "STR-CIRCUIT-RUN-01", "source_version": "0.1",
            "instructions": "20 секунди контролирана работа, 30 секунди преход. Съпротивление с поне 3 качествени повторения в резерв. Спри при болка или загуба на техника.",
            "adaptation": "Общ силов профил: 9 упражнения; 1–3 цели кръга според дозата и зададения треньорски максимум, 2 минути между кръговете. Един цял кръг е намаленият изпълним вариант. При ски е обща, а не специфична силова подготовка.",
            "circuits": profile.get("strength_circuits", 2)}
        methods.append(strength)
        methods.append({**strength, "id": "END-AER-STR-COMBINATION-V2", "title": "Леко аеробно движение и обща сила",
            "structure": "AEROBIC_STRENGTH", "min_work_min": 13., "max_work_min": 33.,
            "adaptation": "Половин аеробна поддържаща доза и един кръг от силовия профил; обща сесийна граница."})
    for p in profile.get("interval_profiles", []):
        zone = p["zone"]
        method = {"id": f"END-VO2-TREF-01-{zone}", "title": f"Контролирани интервали в {zone}", "zone": zone,
            "sports": (p["sport"],), "purpose": "BUILDING", "position": .5, "structure": "METABOLIC_INTERVALS",
            "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"),
            "min_work_min": p["min_repetitions"] * p["work_seconds"] / 60,
            "max_work_min": p["max_repetitions"] * p["work_seconds"] / 60,
            "warmup_min": 15., "cooldown_min": 10., "source_id": "END-VO2-TREF-01", "source_version": "0.3",
            "instructions": f"{p['effort']} Запази резерв от {p['reserve_repetitions']} качествени повторения. Прекрати при загуба на повторяем ритъм или техника. Не ускорявай, за да достигнеш пулсово число.",
            "interval_profile": deepcopy(p), "adaptation": "Числата са от индивидуалния треньорски профил; не са наследени от елитен източников пример."}
        methods.append(method)
        methods.append({**method, "id": f"END-MIX-Z3-HI-01-{zone}", "title": f"Контролирана Z3 с кратък завършек в {zone}",
            "structure": "THRESHOLD_HIGH", "zone": "Z3", "position": .75,
            "periods": ("SPECIAL_PREPARATION", "PRECOMPETITION"), "min_work_min": 6., "max_work_min": 12.,
            "source_id": "END-MIX-Z3-HI-01", "source_version": "0.2",
            "adaptation": "До половината Z3 доза и половината интервален бюджет. Минималният интервален вариант остава задължителен; иначе комбинацията отпада."})
    controls = profile.get("planning_controls")
    if controls and controls.get("automatic_intervals", True):
        for zone, work, rest, minimum, maximum, ratio in (("Z4", 180, 180, 3, 6, 1.2), ("Z5", 30, 30, 6, 20, 1.5)):
            if any(p["zone"] == zone for p in profile.get("interval_profiles", [])):
                continue
            methods.append({"id": f"ONFLOWS-CONTROLLED-{zone}-V2", "title": f"Повторяеми интервали в {zone}",
                "zone": zone, "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": .5,
                "structure": "MODEL_INTERVALS", "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"),
                "min_work_min": minimum*work/60, "max_work_min": maximum*work/60, "warmup_min": 15., "cooldown_min": 10.,
                "source_id": "END-VO2-TREF-01", "source_version": "0.3",
                "implementation_profile": "onflows-metabolic-intervals-v2",
                "interval_template": {"zone": zone, "work_seconds": work, "recovery_seconds": rest,
                                      "min_repetitions": minimum, "max_repetitions": maximum, "reserve_repetitions": 2,
                                      "total_capacity_ratio": ratio, "rest_type": "ACTIVE_Z1",
                                      "dose_status": "VERSIONED_COACH_DEFAULT_NOT_VALIDATED_NORM"},
                "instructions": "Силно, но повторяемо усилие; без спринт или финал до отказ. Остави резерв за още две качествени отсечки. Запази ритъма и техниката; прекрати при разпадането им. Не ускорявай, за да достигнеш пулсово число.",
                "adaptation": "Отделни onFlows профили v2: Z4 — 3–6 × 3 min / 3 min, общ работен бюджет до 1,2 от непрекъснатия капацитет; Z5 — 6–20 × 30 s / 30 s, до 1,5. Това са конкретни начални треньорски настройки, не универсални множители или научно валидирани норми. Изграждането използва 100% от този бюджет, поддържането — 50%, след което Recovery намалява обема веднъж. Цели повторения, почивки, резерв и всички бюджети се проверяват съвместно. Индивидуалният профил замества тези настройки."})
    methods.extend(_bounded_variants(methods))
    if adaptive_methods.developmental(profile):
        methods = [adaptive_methods.short_variant(m) if m["structure"] in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}
                   else m for m in methods if m["structure"] != "THRESHOLD_HIGH"]
    if controls and controls.get("mixed_sessions_enabled", True):
        methods.extend(adaptive_methods.mixed_variants(methods))
    nms = profile.get("neuromuscular") or {}
    if nms.get("enabled"):
        for base in list(methods):
            if base["zone"] == "Z1" and base["purpose"] in {"BUILDING", "MAINTENANCE"} and base["structure"] == "CONTINUOUS":
                methods.append({**deepcopy(base), "id": base["id"] + "-NMS",
                    "title": base["title"] + " с кратки ускорения",
                    "periods": tuple(p for p in base["periods"] if p not in {"RE_ENTRY", "TRANSITION"}),
                    "warmup_min": 15., "neuromuscular_profile": deepcopy(nms),
                    "adaptation": "Индивидуално включена NMS добавка: повторения, пълни почивки и дни от треньорския профил. Времето участва в общата сесия; пулсовият модел не оценява пълния механичен товар."})
    for method in methods:
        interval = (method.get("interval_template") if method["structure"] == "MODEL_INTERVALS"
                    else method.get("interval_profile") if method["structure"] == "METABOLIC_INTERVALS" else None)
        method["dosing_policy"] = ({"version": "expert-method-role-budget-v1",
            "capacity_basis": "CONTINUOUS_TMAX_AT_PRESCRIBED_EFFORT",
            "total_capacity_ratio": interval["total_capacity_ratio"],
            "speed_time_duration_ratio": interval.get("speed_time_duration_ratio"),
            "building_budget_fraction": 1., "maintenance_budget_fraction": .5,
            "recovery_adjustment": "MULTIPLY_VOLUME_ONCE",
            "profile_source": "EXPLICIT_COACH_PROFILE" if method["structure"] == "METABOLIC_INTERVALS" else "EXISTING_VERSIONED_COACH_DEFAULT"}
            if interval else {"version": "continuous-role-budget-v1",
                "capacity_basis": "CONTINUOUS_TMAX_AT_PRESCRIBED_EFFORT",
                "building_fraction": profile.get("building_fraction", .65),
                "maintenance_fraction": profile.get("maintenance_fraction", .3),
                "recovery_adjustment": "MULTIPLY_VOLUME_ONCE"}
            if method["zone"] != "STR" else {"version": "strength-method-profile-v1", "capacity_basis": "STRENGTH_PROFILE"})
        method["method_family"] = method_family(method)
        method["method_family_version"] = VARIETY_VERSION
    return methods


DISABLED_METHODS = (
    {"component": "NMS", "reason": "Кратките спринтове и плиометрията изискват отделна механична опора; не се маскират като метаболитна Z5."},
    {"component": "SOURCE_VARIANTS", "reason": "Незатворените източникови и норвежки кандидати не се активират чрез наследени числа."},
)


@lru_cache(maxsize=1)
def source_catalog():
    data = (Path(__file__).parent / "data" / "methods_v08" / "onflows_training_method_library_v0.8.json").read_bytes()
    value = json.loads(data)
    return {"schema_version": value["schema_version"], "revision": value["library_revision"],
            "sha256": sha256(data).hexdigest(), "method_count":len(value["methods"]),
            "methods": [{k:m[k] for k in ("id", "title_bg", "activation_state", "source_definition_executable")} for m in value["methods"]]}


def catalog(profile=None):
    profile = profile or {}
    disabled = deepcopy(list(DISABLED_METHODS))
    if (profile.get("neuromuscular") or {}).get("enabled"):
        disabled = [d for d in disabled if d["component"] != "NMS"]
    for z in ("Z4", "Z5"):
        if not any(p["zone"] == z for p in profile.get("interval_profiles", [])) and not (profile.get("planning_controls") and profile["planning_controls"].get("automatic_intervals", True)):
            disabled.append({"component": z, "reason": "Добавете индивидуален интервален профил: устойчивост при описаното усилие, повторения, паузи и резерв."})
    if not profile.get("strength_enabled"):
        disabled.append({"component": "STR", "reason": "Включете общата силова подготовка в профила след проверка на упражненията."})
    return {"version": VERSION, "library_version": "v0.8", "status": "RESOLVED_PROFILES", "source_catalog":deepcopy(source_catalog()),
            "methods": resolved_methods(profile), "disabled": disabled,
            "source": "onflows_training_method_library_v0.8.json; изрично разрешени работни профили и договорени дозови правила.",
            "validation": "EXPERT_IMPLEMENTATION_PROFILE_NOT_SCIENTIFIC_VALIDATION",
            "implementation_defaults": "Позицията в зоната, абсолютните граници на работата и неуточнените загрявки/разпускания са видими пилотни настройки, не научни норми."}
