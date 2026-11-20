"""Dated intake estimates from completed expenditure days, never food intake.

The seven-day window and symmetric 0.25 correction are application policy, not
a validated physiological recovery coefficient. No training load is changed.
"""

from datetime import timedelta

from . import energy_model as model

VERSION = "nutrition-seven-day-quarter-v1"
CORRECTION = 0.25

MENU_GUIDANCE = """За меню прочети energy за датата на препоръката (date), включително утре.
Използвай days[].targets и nutrition_basis за тази дата, а не разхода за същия ден.
Целта = среден разход за предходните 7 завършени календарни дни + 0,25 ×
(разход през последния завършен ден − средното). Корекцията е симетрична.
Периодът, използваните дни, средното и корекцията са в nutrition_basis.
При LIMITED_HISTORY посочи кратко броя налични дни; липсващите дни не са нули.
За утре, докато днес не е завършил, целта е предварителна и използва историята до вчера.
Ако последният завършен ден няма пълна оценка, няма дневна корекция; използва се
средното на наличните пълни дни в същия седемдневен прозорец. Не измисляй липсващи данни.
Ако targets.kcal липсва, не задавай количествена калорийна или въглехидратна/мастна цел.
Разграничавай оценени окислени субстрати от хранителен прием; това е предложена цел,
не отчетена храна, измерен дефицит или доказан енергиен баланс. Коефициентът 0,25
и разпределението по средното окисление са работни допускания, не доказана точност.
Не добавяй тренировъчните калории втори път към целта и не въвеждай автоматичен дефицит.
Провери nutrition_basis.food_preferences за датираните алергии, непоносимости и предпочитания.
Не питай повторно за вече посоченото; при неуточнени алергии попитай преди лично меню.
Когато има достъп до плана, прочети day за същата дата и съобрази часовете и
подкрепянето преди/по време/след тренировка. Базовата цел не отчита бъдещия план:
ако предстоящото натоварване изисква друго, обясни го като отделно предложение;
не представяй промяната като резултат на записаната формула.
Дай храни, порции, сурово/сготвено тегло и приблизителни kcal и макронутриенти по
хранения и общо. Храната и напитките по време на тренировка участват в дневния сбор.
Провери сборовете спрямо targets. Няма автоматична проверка по хранителна база;
не представяй генерираните количества като лабораторно или програмно проверени.
references са източници за описаните допускания, не доказателство за валидиране на
целия алгоритъм. Няма включено търсене или база с допълнителна литература; не
твърди, че си прочел нови публикации. За лица под 18 г. няма автоматични диети.
"""


def recommend(day, today, days, profile):
    """Return auditable inputs and targets; an unfinished day cannot be a donor."""
    last = min(day - timedelta(days=1), today - timedelta(days=1))
    first = last - timedelta(days=6)
    used, missing = [], []
    for i in range(7):
        key = (first + timedelta(days=i)).isoformat()
        row = days.get(key) or {}
        total = row.get("total")
        if not total or not model.number(total.get("kcal")) or total["kcal"] <= 0:
            missing.append(key)
            continue
        used.append({"day": key, **total,
                     "estimated_activity_seconds": row.get("estimated_activity_seconds", 0),
                     "coverage_percent": row.get("coverage_percent")})
    mean = sum(r["kcal"] for r in used) / len(used) if used else None
    previous = next((r for r in used if r["day"] == last.isoformat()), None)
    difference = previous["kcal"] - mean if previous else None
    correction = CORRECTION * difference if difference is not None else 0.0
    kcal = mean + correction if mean is not None else None
    carbohydrate = sum(r["carbohydrate_kcal"] for r in used)
    fat = sum(r["fat_kcal"] for r in used)
    fraction = carbohydrate / (carbohydrate + fat) if carbohydrate + fat > 0 else 0.5
    weight = profile.get("weight_kg")
    adult = bool(profile.get("birth_year") and day.year - profile["birth_year"] - 1 >= 18)
    targets = model.dietary_targets(kcal, weight, 0, adult, fraction)
    if targets:
        targets["protein_basis"] = "SEVEN_DAY_ENERGY_RULE" if kcal is not None else "INCOMPLETE_DAY_FALLBACK"
        targets["allocation_note"] = (
            "Цел за прием: среден дневен разход за предходните 7 завършени дни плюс 25% от отклонението на последния ден. "
            "Белтъчини: 1,6–1,9 g/kg според целевите kcal/kg. Останалата енергия следва средното оценено съотношение въглехидрати/мазнини. "
            "Работна препоръка, без индивидуална валидация; не отчита автоматично предстоящата тренировка."
            if kcal is not None else
            "Няма завършени дни с пълна оценка в предходния седемдневен период. Резервна белтъчна цел 1,8 g/kg."
        )
    status = "AVAILABLE" if len(used) == 7 else "LIMITED_HISTORY" if used else "UNAVAILABLE"
    notes = []
    if day > today:
        notes.append(f"Предварителна цел: днешният ден не е завършен; историята е до {last.isoformat()}.")
    if missing:
        notes.append(f"Използвани са {len(used)} от 7 дни с пълна оценка; липсващите дни не се приемат за нула.")
    if previous is None:
        notes.append("Последният завършен ден няма пълна оценка; дневна корекция не се прилага.")
    return targets, {
        "version": VERSION, "status": status, "target_day": day.isoformat(),
        "provisional": day > today, "window_start": first.isoformat(),
        "window_end": last.isoformat(), "available_days": len(used),
        "missing_days": missing, "mean_expenditure_kcal": mean,
        "last_day_expenditure_kcal": previous["kcal"] if previous else None,
        "deviation_kcal": difference,
        "deviation_percent": 100 * difference / mean if difference is not None and mean else None,
        "correction_factor": CORRECTION,
        "correction_kcal": correction if mean is not None else None,
        "nonprotein_carbohydrate_fraction": fraction if used else None,
        "weight_kg": weight, "history": used, "notes": notes,
        "food_preferences": {k: profile.get(k, "") for k in ("preferences", "allergies")},
    }
