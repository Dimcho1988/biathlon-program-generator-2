"""Generate read-only UI scenarios using the actual estimator, never athlete data."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path

from biathlon import individual_learning as model
from biathlon.constants import COMPONENTS
from apps.api.management_projection import public_learning


def generate():
    today = date(2026, 9, 27)
    config = {"mode": "CONTROL", "exploration_enabled": True,
              "max_volume_step_percent": 5., "max_intensity_step": .02}
    current = {"latest_day": str(today), "coverage": 60., "families": 3,
               "history_usable": True, "recovered": True, "stress_score": 45.}
    episodes = []
    for i in range(16):
        end = today-timedelta(days=14*(16-i))
        x = (-2., -1., 1., 2.)[i % 4]
        episodes.append({"id": f"synthetic-{i}", "start": str(end-timedelta(days=13)), "end": str(end),
                         "observed_on": str(end), "quality": 1., "dose_change": {z: x*model.VOLUME_SCALE if z == "Z1" else 0. for z in COMPONENTS},
                         "intensity_change": {z: 0. for z in COMPONENTS if z != "STR"},
                         "response": {"recovered": True, "burden_delta": 0., "recovery_days": 2},
                         "outcomes": [{"scope": "GLOBAL", "change_percent": 2*x, "weight": 1., "source": "TEST"}]})
    negative = deepcopy(episodes)
    for e in negative:
        e["outcomes"][0]["change_percent"] *= -1
    cases = [
        ("missing", "Оскъдни данни", [], {**current, "coverage": 20., "families": 1}, config),
        ("probe", "Малка пробна промяна", episodes[:2], current, config),
        ("increase", "Добър ефект и възстановяване", episodes, current, config),
        ("decrease", "По-добър ефект при намаление", negative, current, config),
        ("caution", "Нов сигнал за преглед", episodes, {**current, "illness_hold": True}, config),
        ("shadow", "Наблюдение без промяна на плана", episodes, current, {**config, "mode": "SHADOW"}),
    ]
    result = []
    for identity, label, observations, state, settings in cases:
        report = model.decide(models=model.fit(observations, today), episodes=observations,
            current=state, today=today, config=settings, allowed_components=["Z1"], phase="GENERAL_PREPARATION",
            dose_reference={z: {"weekly_q": 100., "weekly_minutes": 100.} for z in COMPONENTS})
        report["limitations"] = ["Примерни данни. Реалните предложения зависят от индивидуалните наблюдения и ограниченията на плана."]
        result.append({"id": identity, "label": label, "report": public_learning(report)})
    return result


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1]/"apps/web/lib/learning-examples.json"
    target.write_text(json.dumps(generate(), ensure_ascii=False, indent=2)+"\n")
    print(f"Generated {target.name}")
