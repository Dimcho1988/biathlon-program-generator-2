"""Sport-specific strength capacity prescriptions approved 2026-10-16.

These are explicit coach adaptations, not reproductions of an experimental
protocol. The main sport owns a stable exercise bank; period changes dosage,
not exercise identity. Active work budgets count whole sets/repetitions, while
the existing canonical STR ledger includes internal rests exactly once.
"""
from copy import deepcopy

VERSION = "sport-strength-v1"
REDUCED_ROLES = {"MAINTENANCE", "ACTIVATION", "SUPPORTING", "RECOVERY"}


def session_limit(controls, *, accent=False):
    """One quota for selection, future reservations and feasibility reports.

    None selects automatic frequency. Two-day spacing permits at most four
    exposures in seven days; the actual target, doses and Recovery still govern
    selection. Saved numeric limits remain explicit, including zero.
    """
    if controls is None:
        return 3 if accent else 2
    chosen = controls.get("max_strength_sessions", 2)
    return 4 if chosen is None else chosen
PERIODS = ("RE_ENTRY", "GENERAL_PREPARATION", "SPECIAL_PREPARATION",
           "PRECOMPETITION", "COMPETITION", "TRANSITION")
RESERVE = ("Резервът е броят допълнителни пълни повторения със същата тежест, "
           "амплитуда и техника до невъзможност за следващо повторение. "
           "Не проверявай всяка серия до отказ.")
CONTINUITY = ("Запази същите усвоени упражнения през подготовката. При ново упражнение "
              "или връщане след прекъсване използвай вработващия вариант и по-голям резерв. "
              "При значима мускулна болезненост или влошена работоспособност намали дозата "
              "и обсъди реакцията с треньора; постоянният набор не гарантира липса на треска.")
PROGRESSION = ("Записвай тежестта и реалния резерв за всяко упражнение. Увеличи тежестта "
               "с най-малката налична стъпка след изпълнение на всички предписани серии "
               "с правилна техника и поне зададения резерв. Не добавяй серии извън плана.")

EXERCISES = {
    "half_squat": ("Полуклек с тежест", "Разгъвачи на коляното и тазобедрената става"),
    "romanian_deadlift": ("Румънска тяга", "Задна бедрена група и седалищни мускули"),
    "standing_calf_raise": ("Повдигане на пръсти от стоеж с тежест", "Прасец — gastrocnemius"),
    "seated_calf_raise": ("Повдигане на пръсти от седеж с тежест", "Прасец — soleus"),
    "lat_pulldown": ("Придърпване на горен скрипец", "Широк гръбен мускул и сгъвачи на лакътя"),
    "double_poling": ("Едновременно оттласкване на скрипец от стоеж", "Широк гръбен мускул, трицепс и динамична работа на торса"),
}
BANKS = {
    "Run": ("half_squat", "romanian_deadlift", "standing_calf_raise", "seated_calf_raise"),
    "NordicSki": ("half_squat", "romanian_deadlift", "lat_pulldown", "double_poling", "standing_calf_raise"),
}
SOURCES = {
    "storen2008": {"authors": "Støren et al. (2008)", "url": "https://pubmed.ncbi.nlm.nih.gov/18460997/",
                   "protocol": "Half squat 4×4RM, 3 min rest, 3/week, 8 weeks."},
    "taipale2010": {"authors": "Taipale et al. (2010)", "url": "https://pubmed.ncbi.nlm.nih.gov/20432192/",
                    "protocol": "Heavy squat/leg press 3×4–6 at 80–85% 1RM; explosive variant 3×6 at 30–40%, plus jumps; 2–3 min rest."},
    "losnegard2011": {"authors": "Losnegard et al. (2011)", "url": "https://pubmed.ncbi.nlm.nih.gov/20136751/",
                      "protocol": "12-week programme, 3–4 sets, 4–10RM; standing double poling kept at 10RM; 2–3 min rest."},
    "sandbakk2018": {"authors": "Sandbakk (2018)", "url": "https://ojs.srce.hr/kinesiology/article/view/6420/3389",
                     "protocol": "Descriptive elite practice: preparatory standing double poling 2×6 at 60–70% 1RM, 2 min rest; not a controlled trial."},
    "wagner2024": {"authors": "Wagner et al. (2024)", "url": "https://www.mdpi.com/2076-3417/14/7/2774",
                   "protocol": "Youth skiing/biathlon pilot: bench press/pull, dips/chin-ups, 5 sets each at 6–12RM or 15–20RM to failure; minimum 2 min rest. Hypertrophy-oriented, not proof of this adapted endurance programme."},
}
TEMPLATES = (
    {"key": "INTRO", "title": "Вработваща силова подготовка", "sets": 3, "reps": 8,
     "seconds_per_rep": 4., "rest_seconds": 90, "strength_type": "STR_END",
     "periods": ("RE_ENTRY", "GENERAL_PREPARATION", "TRANSITION"),
     "priority": {"RE_ENTRY": 3., "GENERAL_PREPARATION": 0., "TRANSITION": 3.}},
    {"key": "MAX", "title": "Развитие и поддържане на максимална сила", "sets": 4, "reps": 5,
     "seconds_per_rep": 4., "rest_seconds": 180, "strength_type": "STR_MAX",
     "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"),
     "priority": {"GENERAL_PREPARATION": 3., "SPECIAL_PREPARATION": 1., "COMPETITION": 2.}},
    {"key": "END", "title": "Силова издръжливост с постоянни упражнения", "sets": 3, "reps": 15,
     "seconds_per_rep": 4., "rest_seconds": 120, "strength_type": "STR_END",
     "periods": ("GENERAL_PREPARATION", "SPECIAL_PREPARATION", "COMPETITION"),
     "priority": {"GENERAL_PREPARATION": 1., "SPECIAL_PREPARATION": 2.}},
    {"key": "POWER", "title": "Експлозивна сила с постоянни упражнения", "sets": 4, "reps": 4,
     "seconds_per_rep": 3., "rest_seconds": 120, "strength_type": "STR_PLY",
     "periods": ("SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"),
     "priority": {"SPECIAL_PREPARATION": 3., "PRECOMPETITION": 3., "COMPETITION": 1.}},
)


def methods(profile):
    if not profile.get("strength_enabled"):
        return []
    sport = "NordicSki" if profile.get("sport") in {"NordicSki", "Biathlon", "RollerSki"} else "Run"
    result = []
    for template in TEMPLATES:
        p = deepcopy(template)
        p.update(version=VERSION, main_sport=sport, exercise_bank_id=f"{sport}-capacity-v1",
                 reduced_fraction=profile.get("strength_maintenance_fraction", .6),
                 dose_basis="TOTAL_REPETITIONS_AT_UNCHANGED_REPETITIONS_PER_SET",
                 reserve_definition=RESERVE, continuity=CONTINUITY, progression=PROGRESSION,
                 transition_seconds=120,
                 validation="COACH_ADAPTATION_NOT_ORIGINAL_STUDY_PROTOCOL")
        p["exercises"] = [{"id": key, "name": EXERCISES[key][0], "muscle_groups": EXERCISES[key][1],
                            "reps": 10 if key == "double_poling" and p["key"] == "MAX" else p["reps"]}
                           for key in BANKS[sport]]
        p["building_total_sets"] = p["sets"] * len(p["exercises"])
        p["sources"] = [SOURCES[k] for k in ("storen2008", "taipale2010") if sport == "Run"] or [SOURCES[k] for k in ("losnegard2011", "sandbakk2018")]
        if sport == "NordicSki" and p["key"] == "END":
            p["sources"] = [SOURCES["wagner2024"]]
        maximum = p["sets"] * sum(e["reps"] for e in p["exercises"]) * p["seconds_per_rep"] / 60
        result.append({"id": f"ONFLOWS-STR-{sport.upper()}-{p['key']}-V1", "title": p["title"],
            "zone": "STR", "sports": ("Run", "NordicSki", "RollerSki"), "purpose": "BUILDING",
            "position": 0., "structure": "STRENGTH_SETS", "periods": p["periods"],
            "min_work_min": minimum_from_profile(p, .5),
            "max_work_min": maximum, "warmup_min": 10., "cooldown_min": 5.,
            "strength_profile": p, "source_id": VERSION, "source_version": "1",
            "instructions": RESERVE + " " + CONTINUITY,
            "adaptation": "Треньорска адаптация на публикувани подходи. Постоянен набор според основния спорт; "
                "няколко упражнения за силов капацитет. Дозата е в цели работни серии; "
                "поддържане и активация използват 50–60% за едно занимание. "
                "Резервът, добавените упражнения и времевите оценки са настройки на onFlows, не резултат от изследването."})
    return result


def options(p):
    """Whole, balanced doses with every core exercise present; actual minutes."""
    n = len(p["exercises"])
    return [(count, sum((count//n+(i < count % n))*e["reps"] for i,e in enumerate(p["exercises"])) * p["seconds_per_rep"]/60)
            for count in range(n, p["building_total_sets"]+1)]


def minimum_from_profile(p, fraction):
    doses = options(p)
    return next(work for _,work in doses if work+1e-9 >= fraction*doses[-1][1])


def reduced_fraction(p):
    """Nearest whole dose inside 50–60%; rounding never creates a tiny dose."""
    doses = options(p)
    maximum = doses[-1][1]
    legal = [work/maximum for _,work in doses if .5-1e-9 <= work/maximum <= .6+1e-9]
    return min(legal, key=lambda fraction: (abs(fraction-p["reduced_fraction"]), fraction))


def quantize(method, work):
    return max((minutes for _,minutes in options(method["strength_profile"]) if minutes <= work+1e-8), default=0.)


def previous(method, work):
    return max((minutes for _,minutes in options(method["strength_profile"]) if minutes < work-1e-8), default=0.)


def minimum_work(method, evidence):
    p = method["strength_profile"]
    reduced = evidence.get("resolved_role") in REDUCED_ROLES or evidence.get("strength_period") == "RE_ENTRY"
    floor = .5 if reduced else .75
    return minimum_from_profile(p, floor)


def priority(method, period):
    p = method.get("strength_profile")
    if not p:
        return 0.
    return .25 * (p["priority"].get(period, 0.) - 3.)


def work_blocks(method, work, evidence, block):
    """Balance sets across the fixed bank; never remove an exercise to fit."""
    p = method["strength_profile"]
    count, actual_work = max(((count, minutes) for count,minutes in options(p) if minutes <= work+1e-8), default=(0, 0.))
    role = evidence.get("resolved_role", "BUILDING")
    if actual_work+1e-8 < minimum_work(method, evidence):
        return []
    if role in REDUCED_ROLES and actual_work > options(p)[-1][1]*reduced_fraction(p)+1e-8:
        return []
    n = len(p["exercises"])
    sets = [count//n + (i < count % n) for i in range(n)]
    result = []
    introductory = p["key"] == "INTRO" or evidence.get("strength_period") in {"RE_ENTRY", "TRANSITION"}
    for index, (exercise, total_sets) in enumerate(zip(p["exercises"], sets)):
        for number in range(total_sets):
            rir = 4 if introductory or p["key"] == "POWER" or role == "ACTIVATION" else 3 if role in REDUCED_ROLES or number < total_sets-1 else 2
            effort = ("Умерено съпротивление и максимално намерение за бърза концентрична фаза; "
                      "прекрати при отчетлива загуба на скорост или техника." if p["key"] == "POWER" else
                      "Подбери тежест за предписаните повторения и резерв; запази амплитудата и техниката.")
            seconds = exercise["reps"]*p["seconds_per_rep"]
            b = block("WORK", f"{exercise['name']} · серия {number+1}/{total_sets} · {exercise['reps']} повторения",
                      "STR", seconds/60, None, f"{exercise['muscle_groups']}. {effort} Резерв: поне {rir} повторения. Времето е ориентир за планиране; водят повторенията.")
            b.update(strength_type=p["strength_type"], exercise_id=exercise["id"],
                     exercise_bank_id=p["exercise_bank_id"], repetitions=exercise["reps"],
                     set_number=number+1, exercise_sets=total_sets, reserve_repetitions=rir,
                     duration_is_estimate=True)
            result.append(b)
            if number < total_sets-1:
                rest = block("RECOVERY", "Почивка между сериите", "STR", p["rest_seconds"]/60, None,
                             "Използвай цялата предписана почивка; не добавяй упражнения.")
                rest.update(strength_type=p["strength_type"], passive_rest=True)
                result.append(rest)
        if index < n-1:
            rest = block("TRANSITION", "Почивка и смяна на упражнението", "STR", p["transition_seconds"]/60,
                         None, "Подготви следващото упражнение; паузата се отчита веднъж.")
            rest.update(strength_type=p["strength_type"], passive_rest=True)
            result.append(rest)
    return result


def summary(blocks, method):
    p = method["strength_profile"]
    work = [b for b in blocks if b["kind"] == "WORK" and b["zone"] == "STR"]
    return {"version": VERSION, "exercise_bank_id": p["exercise_bank_id"], "main_sport": p["main_sport"],
            "method": p["key"], "exercises": [{**e, "sets": sum(b.get("exercise_id") == e["id"] for b in work)} for e in p["exercises"]],
            "total_sets": len(work), "building_total_sets": p["building_total_sets"],
            "dose_fraction": sum(b["repetitions"] for b in work)/(p["sets"]*sum(e["reps"] for e in p["exercises"])),
            "dose_basis": p["dose_basis"], "reserve_definition": RESERVE,
            "continuity": CONTINUITY, "progression": PROGRESSION,
            "sources": p["sources"], "validation": p["validation"]}
