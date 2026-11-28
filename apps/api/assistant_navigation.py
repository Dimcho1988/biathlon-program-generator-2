"""Canonical handoffs for actions owned by the authenticated web session.

These are links to existing forms, never a claim that an action was performed.
Account roles and OAuth state are intentionally not inferred from athlete grants.
"""
from copy import deepcopy

from fastapi import HTTPException


_DESTINATIONS = {
    "account": {
        "label": "Моят акаунт", "href": "/account",
        "instruction": "В акаунта са личните данни, ролите, поканите и достъпните спортисти.",
    },
    "profile": {
        "label": "Име на акаунта", "href": "/account",
        "instruction": "Промени полето „Име“ и избери „Запази името“.",
    },
    "password": {
        "label": "Промяна на паролата", "href": "/account#password-guidance",
        "instruction": "Въведи новата парола само в защитения формуляр на акаунта. Не изпращай пароли в чата.",
    },
    "organizations": {
        "label": "Създаване на отбор", "href": "/account#create-team-title",
        "instruction": "Попълни името на отбора в „Създай отбор“. Ако липсва име на акаунта, първо го запази.",
    },
    "invitations": {
        "label": "Покани и отбори", "href": "/account",
        "instruction": "Чакащите покани се приемат от акаунта. Формулярът „Покани участник“ е достъпен в отборите, за които имаш право на управление.",
    },
    "assignments": {
        "label": "Възлагане на спортисти", "href": "/account",
        "instruction": "В съответния отбор използвай „Възложи спортист“. Формулярът се показва само при право на управление на този отбор.",
    },
    "athletes": {
        "label": "Достъпни профили", "href": "/account#athletes-title",
        "instruction": "Избери „Отвори“ до желания спортист. След смяната асистентът започва отделен разговор за избрания профил.",
    },
    "link": {
        "label": "Свързване на личния профил", "href": "/account",
        "instruction": "Ако текущият Intervals профил още не е свързан, акаунтът показва „Свържи текущия Intervals профил“.",
    },
    "connect": {
        "label": "Свързване с Intervals.icu", "href": "/api/integrations/intervals/connect",
        "instruction": "Отвори връзката и разреши достъпа в Intervals.icu. Удостоверяването се извършва в защитения поток; не изпращай пароли или ключове в чата.",
    },
    "refresh": {
        "label": "Обновяване на тренировъчните данни", "href": "/",
        "instruction": "На началния екран избери „Обнови данните“. Заявката се изпълнява във фонов режим и екранът показва състоянието ѝ.",
    },
    "logout": {
        "label": "Изход от устройството", "href": "/account",
        "instruction": "В акаунта избери „Изход от това устройство“.",
    },
}


def read(access, key=""):
    """Return only known destinations; the web form rechecks account authority."""
    if not (access.get("can_view_plan") is True or access.get("can_view_recovery") is True):
        raise HTTPException(403, "Нямаш достъп до този разговор.")
    can_refresh = access.get("can_view_recovery") is True or (
        access.get("can_view_plan") is True and access.get("can_edit_plan") is True)
    if key is None or key == "":
        selected = ("account", "athletes", "connect") + (("refresh",) if can_refresh else ())
    elif isinstance(key, str) and key in _DESTINATIONS:
        selected = (key,)
    else:
        raise HTTPException(422, "Неподдържана връзка към приложението.")
    if key == "refresh" and not can_refresh:
        raise HTTPException(403, "Текущият достъп позволява преглед на програмата, без обновяване на тренировъчните данни.")
    links = [{"key": name, **deepcopy(_DESTINATIONS[name])} for name in selected]
    if access.get("can_view_recovery") is not True:
        for item in links:
            if item["key"] == "refresh":
                item.update(href="/management",
                    instruction="В „Седмична програма“ избери „Обнови тренировките от Intervals“. Заявката се изпълнява във фонов режим.")
    return {
        "status": "HANDOFF_REQUIRED",
        "performed": False,
        "links": links,
        "account_permissions": "CHECKED_BY_AUTHENTICATED_WEB_FORM",
        "instruction": (
            "Дай конкретната връзка и необходимото действие. Това е отваряне на формуляр; "
            "не е извършена промяна, изпратена покана, смяна на профила или обновяване. "
            "Не искай пароли, кодове за вход или API ключове. Правото за редакция на тренировките "
            "не означава право за управление на отбор."
        ),
    }
