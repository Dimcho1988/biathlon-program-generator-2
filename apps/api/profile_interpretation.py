"""Canonical profile interpretation shared by screens and conversational reads."""

def profile_availability(payload):
    """Explain saved day values with the planner's existing resolution rules.

    This is read metadata, separate from editable profile fields. It adds no
    history read, budget calculation or replacement training prescription.
    """
    from biathlon.planning_history import availability, availability_mode
    profile = payload
    while isinstance(profile, dict) and "profile" in profile:
        profile = profile["profile"]
    if not isinstance(profile, dict) or not isinstance(profile.get("available_minutes"), (list, tuple)):
        return None
    mode = availability_mode(profile)
    if mode not in {"AUTO_HISTORY", "MANUAL"}:
        return None
    effective = availability(profile)
    manual = mode == "MANUAL"
    return {
        "stored_mode": profile.get("availability_mode"),
        "effective_mode": mode,
        "resolution_basis": "EXPLICIT_MODE" if profile.get("availability_mode") else
            "LEGACY_TEMPLATE_AUTO_HISTORY" if not manual else "SAVED_MANUAL_LIMITS",
        "stored_available_minutes_role": "MANUAL_DAY_LIMITS" if manual else "INACTIVE_IN_AUTO_HISTORY",
        "profile_allowed_weekdays": [index for index, minutes in enumerate(effective) if minutes > 0],
        "weekday_index": "Понеделник=0, неделя=6; допустим ден не означава назначена тренировка.",
        "manual_daily_limits_minutes": list(effective) if manual else None,
        "manual_weekly_availability_minutes": sum(effective) if manual else None,
        "interpretation": (
            "available_minutes са записаните дневни ограничения, приложени само за допустимите training_days. "
            "Това са горни граници, а не назначен тренировъчен обем."
            if manual else
            "В AUTO_HISTORY available_minutes са неактивни записани стойности и не налагат дневни или седмични времеви ограничения. "
            "Нулата в стария шаблон не забранява тренировка в този ден. Допустимите дни по този профил следват training_days. "
            "Обемът следва историята и действащите треньорски цели при ограниченията на алгоритъма. "
            "Други активни настройки също могат да ограничават обема и дните на плана."
        ),
    }

