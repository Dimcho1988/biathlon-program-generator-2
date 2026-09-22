"""Explicit starter subset of the current v0.7 methodological specification.

Parent-card constraints were reviewed against v0.7 (Library revision 8).
Concrete pilot choices below are marked as implementation defaults, not as
scientifically validated prescriptions. The older audit does not validate
this subset. Z3 never receives the Z4/Z5 interval exception.
"""
from copy import deepcopy

VERSION = "training-methods-v4"
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


def resolved_methods(profile):
    """Resolve individual profiles and bounded, exposure-gated pilot methods.

    No elite source variant inherits numeric defaults from its parent card.
    """
    methods = deepcopy(list(METHODS))
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
         "adaptation": "3–10 цели цикъла. Дозата е споделена между компонентите; леките части не са допълнителна пълна доза.",
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
            "min_work_min": 6., "max_work_min": profile.get("strength_circuits", 2) * 3.,
            "warmup_min": 10., "cooldown_min": 5., "source_id": "STR-CIRCUIT-RUN-01", "source_version": "0.1",
            "instructions": "20 секунди контролирана работа, 30 секунди преход. Съпротивление с поне 3 качествени повторения в резерв. Спри при болка или загуба на техника.",
            "adaptation": "Общ силов профил: 9 упражнения, 2–3 кръга, 2 минути между кръговете. При ски е обща, а не специфична силова подготовка.",
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
        for zone, work, rest in (("Z4", 60, 120), ("Z5", 30, 60)):
            if any(p["zone"] == zone for p in profile.get("interval_profiles", [])):
                continue
            methods.append({"id": f"ONFLOWS-CONTROLLED-{zone}-V1", "title": f"Повторяеми интервали в {zone}",
                "zone": zone, "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING", "position": .5,
                "structure": "MODEL_INTERVALS", "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"),
                "min_work_min": 3*work/60, "max_work_min": 6*work/60, "warmup_min": 15., "cooldown_min": 10.,
                "source_id": "END-VO2-TREF-01", "source_version": "0.3",
                "implementation_profile": "onflows-controlled-intervals-v1",
                "interval_template": {"zone": zone, "work_seconds": work, "recovery_seconds": rest,
                                      "min_repetitions": 3, "max_repetitions": 6, "reserve_repetitions": 2,
                                      "building_ratio": .6, "maintenance_ratio": .4},
                "instructions": "Силно, но повторяемо усилие; без спринт или финал до отказ. Остави резерв за още две качествени отсечки. Запази ритъма и техниката; прекрати при разпадането им. Не ускорявай, за да достигнеш пулсово число.",
                "adaptation": "Отделен начален onFlows профил: 3–6 × 1 min / 2 min за Z4 или 3–6 × 30 s / 60 s за Z5; работа до 60% от капацитета, 40% за поддържане. Не активира източников елитен вариант или изключението над 100%. Изисква скорошна експозиция в действителното средство."})
    return methods


DISABLED_METHODS = (
    {"component": "NMS", "reason": "Кратките спринтове и плиометрията изискват отделна механична опора; не се маскират като метаболитна Z5."},
    {"component": "SOURCE_VARIANTS", "reason": "Незатворените източникови и норвежки кандидати не се активират чрез наследени числа."},
)


def catalog(profile=None):
    profile = profile or {}
    disabled = deepcopy(list(DISABLED_METHODS))
    for z in ("Z4", "Z5"):
        if not any(p["zone"] == z for p in profile.get("interval_profiles", [])) and not (profile.get("planning_controls") and profile["planning_controls"].get("automatic_intervals", True)):
            disabled.append({"component": z, "reason": "Добавете индивидуален интервален профил: устойчивост при описаното усилие, повторения, паузи и резерв."})
    if not profile.get("strength_enabled"):
        disabled.append({"component": "STR", "reason": "Включете общата силова подготовка в профила след проверка на упражненията."})
    return {"version": VERSION, "library_version": "v0.7", "status": "RESOLVED_PROFILES",
            "methods": resolved_methods(profile), "disabled": disabled,
            "source": "onflows_training_method_library_v0.7.yaml, revision 8; договорени дозови правила.",
            "validation": "EXPERT_IMPLEMENTATION_PROFILE_NOT_SCIENTIFIC_VALIDATION",
            "implementation_defaults": "Позицията в зоната, абсолютните граници на работата и неуточнените загрявки/разпускания са видими пилотни настройки, не научни норми."}
