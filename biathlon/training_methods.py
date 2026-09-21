"""Explicit starter subset of the current v0.7 methodological specification.

Parent-card constraints were reviewed against v0.7 (Library revision 8).
Concrete pilot choices below are marked as implementation defaults, not as
scientifically validated prescriptions. The older audit does not validate
this subset. Z3 never receives the Z4/Z5 interval exception.
"""
from copy import deepcopy

VERSION = "training-methods-pilot-v1"
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

DISABLED_METHODS = (
    {"component": "Z4", "reason": "Нужен е одобрен конкретен интервален профил с повторения, почивки и резерв."},
    {"component": "Z5", "reason": "Метаболитните интервали и кратките спринтове изискват отделни профили; не се използва универсален множител."},
    {"component": "STR", "reason": "Силовият компонент се отчита в историята и Recovery; предписването изисква отделен одобрен профил с конкретни упражнения и резерв."},
)


def catalog():
    return {"version": VERSION, "library_version": "v0.7", "status": "PILOT_SUBSET",
            "methods": deepcopy(list(METHODS)), "disabled": deepcopy(list(DISABLED_METHODS)),
            "source": "onflows_training_method_library_v0.7.yaml, revision 8; договорени дозови правила.",
            "validation": "EXPERT_IMPLEMENTATION_PROFILE_NOT_SCIENTIFIC_VALIDATION",
            "implementation_defaults": "Позицията в зоната, абсолютните граници на работата и неуточнените загрявки/разпускания са видими пилотни настройки, не научни норми."}
