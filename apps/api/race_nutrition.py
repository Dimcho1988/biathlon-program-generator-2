"""Dated, reviewed nutrition overrides. No model/plan mutation or AI write.

NUTRITION subrecords reuse the existing actor authorization, revision checks and
prepared-page invalidation. The event fingerprint makes moved/cancelled races
ineligible even if an old acceptance is still retained in the audit history.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import Field
from .response_monitoring import InputModel
from .response_service import ResponseStore

VERSION = "nutrition-race-loading-v1"
PREFIX = "race-loading:"
REFERENCE = "https://doi.org/10.1249/MSS.0000000000000852"


class Decision(InputModel):
    event_id: str = Field(min_length=1, max_length=160)
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: Literal["ACCEPTED", "DECLINED"]


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def is_decision(row):
    return row.get("kind") == "NUTRITION" and (
        row.get("entry_key", "").startswith(PREFIX)
        or row.get("payload", {}).get("schema_version") == VERSION)


def events(repository, alias):
    reader = getattr(repository, "athlete_planning_calendar", None)
    value = reader(alias) if callable(reader) else None
    value = value.to_payload() if hasattr(value, "to_payload") else value
    return (value or {}).get("events", [])


def fingerprint(event):
    return digest({k: event.get(k) for k in (
        "event_id", "event_type", "name", "start_date", "end_date", "race_course")})


def eligible(event, today):
    if event.get("event_type") not in {"MAIN_RACE", "CONTROL_RACE"}:
        return False
    start = date.fromisoformat(event["start_date"])
    if not 1 <= (start - today).days <= 3 or event.get("end_date") != str(start):
        return False
    course = event.get("race_course") or {}
    duration = course.get("expected_duration_min")
    if duration is not None:
        return len(duration) == 2 and duration[0] > 90
    # Only a clearly named running marathon (or explicit road marathon course)
    # can qualify without a duration. Do not infer ski/half-marathon durations.
    name = event.get("name", "").casefold()
    marathon = bool(re.search(r"\b(?:маратон|marathon)\b", name))
    ambiguous = bool(re.search(r"\b(?:half|полу|ски|ski|skiing|swim|плуване)\b", name))
    explicit_road = course.get("surface") == "ROAD" and course.get("distance_m") == 42195
    return explicit_road or (marathon and not ambiguous and not course)


def latest_decisions(entries):
    result = {}
    for row in entries:
        if is_decision(row):
            key = row["payload"].get("event_id")
            if key and row.get("revision", 0) >= result.get(key, {}).get("revision", -1):
                result[key] = row
    return result


def active_decisions(entries, calendar, today):
    current = {e["event_id"]: fingerprint(e) for e in calendar}
    return [r for r in latest_decisions(entries).values()
            if r["payload"].get("status") == "ACCEPTED"
            and (current.get(r["payload"]["event_id"]) == r["payload"].get("event_fingerprint")
                 or r["payload"].get("race_date", "9999") < str(today))]


def loading_target(base, weight):
    """Keep protein; add CHO as needed, with a transparent 20% fat policy.

    20% is a temporary application allocation, not a biochemical minimum.
    A high existing target/CHO amount is never reduced to reach 10 g/kg.
    """
    carbohydrate = max(base["carbohydrate_g"], 10 * weight)
    kcal = max(base["kcal"], (4 * base["protein_g"] + 4 * carbohydrate) / .8)
    fat = (kcal - 4 * base["protein_g"] - 4 * carbohydrate) / 9
    return {**base, "kcal": kcal, "carbohydrate_g": carbohydrate, "fat_g": fat,
            "energy_kcal_per_kg": kcal / weight, "fat_energy_fraction": fat * 9 / kcal,
            "purpose": "RACE_LOADING",
            "allocation_note": "Временно въглехидратно зареждане за посочения ден. "
            "Начален ориентир 10 g/kg; белтъчините се запазват от базовата цел. "
            "Мазнините са 20–25% от енергията. Храната, геловете и напитките участват в общия прием."}


def proposals(calendar, entries, today, target_for_day):
    decisions = latest_decisions(entries)
    active = active_decisions(entries, calendar, today)
    result = []
    for event in sorted(calendar, key=lambda e: (e["start_date"], e["event_id"])):
        if not eligible(event, today):
            continue
        race_day = date.fromisoformat(event["start_date"])
        record = decisions.get(event["event_id"], {})
        saved = record.get("payload", {})
        same = saved.get("event_fingerprint") == fingerprint(event)
        status = saved.get("status", "PENDING") if same else "PENDING"
        rows = []
        reason = None
        for offset in (2, 1):
            day = race_day - timedelta(days=offset)
            if day < today:
                continue
            base, basis = target_for_day(day)
            if not base or not base.get("kcal") or not basis.get("weight_kg"):
                reason = "Необходими са тегло, пълнолетие и завършена история за базова хранителна цел."
                continue
            rows.append({"day": str(day), "base_targets": base,
                         "targets": loading_target(base, basis["weight_kg"]),
                         "weight_kg": basis["weight_kg"],
                         "food_preferences": basis["food_preferences"]})
        if same and status == "ACCEPTED":
            rows = deepcopy(saved["days"])
        overlaps = any(r["payload"]["event_id"] != event["event_id"]
                       and {d["day"] for d in rows} & {d["day"] for d in r["payload"]["days"]}
                       for r in active)
        if overlaps:
            reason = "Има прието зареждане за същите дни. Първо го откажете, за да изберете друго."
        preview = {"event_id": event["event_id"], "event_name": event["name"],
                   "event_fingerprint": fingerprint(event), "race_date": str(race_day),
                   "days": rows, "evaluated_on": str(today), "version": VERSION}
        result.append({**preview, "preview_hash": digest(preview), "status": status,
                       "revision": record.get("revision", 0), "can_accept": bool(rows) and not reason,
                       "reason": reason, "late": (race_day-today).days == 1,
                       "reference": REFERENCE})
    return result


def apply_accepted(days, entries, calendar, today):
    # One dated override, never a sum of overlapping acceptances. The nearest
    # race wins deterministically if concurrent clients accepted two events.
    accepted = sorted(active_decisions(entries, calendar, today),
                      key=lambda r: (r["payload"]["race_date"], r["payload"]["event_id"]))
    for day in days:
        for record in accepted:
            saved = next((d for d in record["payload"]["days"] if d["day"] == day["day"]), None)
            if saved:
                day["base_targets"] = deepcopy(day["targets"])
                day["targets"] = deepcopy(saved["targets"])
                day["nutrition_basis"]["race_loading"] = True
                day["nutrition_basis"]["notes"].append(
                    "За този ден важи прието въглехидратно зареждане; калориите са от одобреното предложение.")
                break


def local_today(repository, alias, now=None):
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409, "Липсват настройки на спортиста.")
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(settings.timezone)).date()


def reminders(repository, alias, now=None):
    today = local_today(repository, alias, now)
    decisions = latest_decisions(ResponseStore(repository).entries(alias))
    return {"events": [{"event_id": e["event_id"], "name": e["name"], "race_date": e["start_date"]}
                       for e in events(repository, alias) if eligible(e, today)
                       and decisions.get(e["event_id"], {}).get("payload", {}).get("event_fingerprint") != fingerprint(e)],
            "today": str(today)}


def save_decision(repository, alias, body, actor, now=None):
    from .prepared_views import read
    today = local_today(repository, alias, now)
    report = read(repository, alias, "energy")
    proposal = next((r for r in report.get("race_preparation", []) if r["event_id"] == body.event_id), None)
    calendar = events(repository, alias)
    event = next((e for e in calendar if e["event_id"] == body.event_id), None)
    if (not proposal or proposal["preview_hash"] != body.preview_hash
            or proposal["revision"] != body.expected_revision
            or proposal["evaluated_on"] != str(today) or not event or not eligible(event, today)
            or fingerprint(event) != proposal["event_fingerprint"]):
        raise HTTPException(409, "Състезанието или предложението е променено. Презаредете.")
    if body.action == "ACCEPTED" and (not proposal["can_accept"] or proposal["status"] == "ACCEPTED"):
        raise HTTPException(409, "Прегледайте актуалното предложение.")
    entries = ResponseStore(repository).entries(alias)
    if body.action == "ACCEPTED" and any(
            r["payload"]["event_id"] != body.event_id
            and {d["day"] for d in proposal["days"]} & {d["day"] for d in r["payload"]["days"]}
            for r in active_decisions(entries, calendar, today)):
        raise HTTPException(409, "Вече има прието зареждане за тези дни.")
    payload = {"schema_version": VERSION, "status": body.action,
               **{k: proposal[k] for k in ("event_id", "event_name", "event_fingerprint", "race_date", "days", "preview_hash")}}
    return ResponseStore(repository).save(alias, "NUTRITION", PREFIX + body.event_id,
                                          str(today), payload, body.expected_revision, str(actor))
