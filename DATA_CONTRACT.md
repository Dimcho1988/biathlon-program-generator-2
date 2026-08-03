# Нормализиран договор за бъдещ реален вход

> Legacy snapshot, запазен за съвместимост. Каноничният и актуален договор е
> `docs/DATA_CONTRACT.md`.

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

## Потвърдени структурни находки от реални streams

Проверени са две реални обезличени активности. В repository-то не се добавят
техните JSON payload-и, идентификатори, timestamps, координати или сурови
стойности.

- При първата активност са налични 2822 точки във всеки от осемте streams:
  `altitude`, `distance`, `heartrate`, `latlng`, `respiration`, `temp`, `time`
  и `velocity_smooth`. Структурната оценка за `estimated_frequency_hz` е
  `null`, а activity detail съдържа поле `recording_stops`.
- При Run активността са налични 2994 точки във всеки от 16 streams и е
  потвърдена честота `1.0 Hz`. Налични са `heartrate`, `time`,
  `velocity_smooth`, `cadence`, `altitude`, `fixed_altitude` и running-dynamics
  streams. Activity detail не съдържа `recording_stops`.

Тези находки потвърждават само структура и реални варианти на времевата
решетка. Те още не фиксират правила за нормализиране, moving status, единици,
gap/pause интерпретация или избор между raw/fixed streams. На този етап не се
изчисляват `T`, `k`, `Q`, `E`, `7/40`, `Tref`, зони или readiness.

Диагностичният договор за избрана активност е aggregate-only: stream имена и
брой точки, числови min/median/max и coverage, `dt_sec` статистика, gaps,
сравнение на относителни продължителности, HR/скорост/optional coverage и само
наличие/брой на `recording_stops`. `latlng` може да се отчете само като stream
име и брой точки; `data`, `data2` и GPS координати не се включват в резултата.
При липса на експлицитен надежден `moving` stream статусът остава unavailable и
не се извежда от `velocity_smooth`.

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
