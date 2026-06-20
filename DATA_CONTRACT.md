# Нормализиран договор за бъдещ реален вход

Текущата версия няма Strava интеграция. Ядрото е организирано така, че бъдещ адаптер да преобразува външни данни към следните структури, без да променя физиологичните и плановите функции.

## Активност — метаданни

Минимални полета:

```text
activity_id, athlete_id, date, start_time, sport, moving_min, source
```

За обобщения MVP използва и:

```text
real_Z1 ... real_Z5, real_STR
pos_Z1 ... pos_Z5
```

`pos_Zx` е нормализирана средна позиция в зоната `[0, 1]`.

## Едносекунден поток

```text
timestamp, offset_sec, hr, valid
```

По-късно могат да се добавят `speed`, `power`, `cadence`, `altitude`, `distance`, `temperature` и GPS полета. Функцията `analyze_activity_stream` използва само нормализираните колони и индивидуалния профил на зоните.

## Дневен мониторинг

```text
athlete_id, date, sleep_quality, fatigue, soreness_legs, soreness_upper,
stress, motivation, pain, illness, morning_hr, hrv, sleep_hours,
weight_kg, session_rpe, execution_quality, source, reliability, note
```

## Контролен тест

```text
test_id, athlete_id, date, test_code, protocol_version,
primary_value, secondary_value, valid, comparability, conditions, note
```

## Календар

```text
event_id, athlete_id, type, name, start_date, end_date,
priority, goal, locked, note
```

## Принцип за бъдещ адаптер

1. Изтегля суровите записи.
2. Валидира времето, дублирането и липсващите стойности.
3. Преобразува ги към горните таблици.
4. Предава нормализираните DataFrame обекти към съществуващия `analyze_athlete` pipeline.
5. Пази суровия източник отделно за одит, без да смесва импорта с физиологичните формули.
