# Нормализиран договор за бъдещ реален вход

Текущата версия няма Strava интеграция. Ядрото е организирано така, че бъдещ адаптер да преобразува външни данни към следните структури, без да променя физиологичните и плановите функции.

## Активност — метаданни

Минимални полета:

```text
activity_id, athlete_id, date, start_time, sport, moving_min, source
```

За обобщения MVP използва и:

```text
real_Z1 ... real_Z5
real_STR_STAB, real_STR_END, real_STR_MAX, real_STR_PLY
real_STR, q_STR_STAB, q_STR_END, q_STR_MAX, q_STR_PLY, q_STR
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

## Структурирано лагерно задание

Календарът остава общ и backwards-compatible. Изчислителните настройки само
за събития от тип `CAMP` се пазят в отделна таблица, свързана чрез `event_id`:

```text
event_id, athlete_id, schema_version, mesocycle_type,
mesocycle_length_weeks, accent_mode, accent_limit,
accent_Z1, accent_Z2, accent_Z3, accent_Z4, accent_Z5, accent_STR,
volume_factor, stress_factor, maintenance_factor,
post_camp_behavior, post_camp_recovery_weeks, note
```

Ключът е съставен: `(athlete_id, event_id)`. `mesocycle_type` приема `AUTO`,
`BUILD`, `MAINTAIN` или `RECOVERY`. `RECOVERY` е валиден само с непразна
обяснителна `note`; иначе нормализаторът използва безопасен `AUTO`.
`accent_Z*` е нормализирана ръчна сила `[0, 1]`; при `AUTO` компонентите се
избират по фазовата крива. `volume_factor` управлява избраните Z1–Z3,
`stress_factor` — избраните Z4–Z5 и силата, а `maintenance_factor` —
неакцентните компоненти. При частична седмица факторът се прилага
пропорционално на лагерните дни. Свободната бележка е одитно обяснение и не се
интерпретира като машинна команда.

`post_camp_behavior` приема `AUTO`, `RECOVERY` или `COMPLEMENT`.
Невалидни, празни, `NaN` или безкрайни числови стойности се заменят с
документирания default преди ограничаване. CAMP без ред в тази таблица остава
валиден: scheduler-ът използва `AUTO → MAINTAIN`, а числовите му множители
остават legacy до изрично записване на експертно задание.

## Принцип за бъдещ адаптер

1. Изтегля суровите записи.
2. Валидира времето, дублирането и липсващите стойности.
3. Преобразува ги към горните таблици.
4. Предава нормализираните DataFrame обекти към съществуващия `analyze_athlete` pipeline.
5. Пази суровия източник отделно за одит, без да смесва импорта с физиологичните формули.

## Сезонни и седмични предпочитания

```text
season_start, season_end, annual_target_hours, annual_goal_influence,
min_volume_factor, max_volume_factor, sessions_per_week, rest_days,
double_session_days, long_session_day, intensity_days, strength_days,
max_key_sessions_per_week, double_threshold_enabled,
mesocycle_anchor_date, mesocycle_length_weeks,
camp_default_accent_limit,
double_threshold_day, double_threshold_components,
double_threshold_min_readiness, double_threshold_phase_min,
double_threshold_phase_max, between_sessions_recovery_days
```

Дните от седмицата се пазят като цели числа `0..6`, където `0 = понеделник`.

## Ръчен дневен импорт

Минималният CSV формат е:

```text
date, sport, rpe, Z1, Z2, Z3, Z4, Z5, STR_STAB, STR_END, STR_MAX, STR_PLY, note
```

Всеки ред е сумарен реален дневен обем. Силовите колони са реални минути; коефициентите 0.8/1.0/1.2/1.4 се прилагат автоматично. При импорт се добавят нормализирани полета `source`, `status`, `moving_min`, `elapsed_min`, `quality_score`, `real_Zx`, `pos_Zx` и уникален `activity_id`.

## Бърз седмичен onboarding

```text
week_start, sessions, Z1, Z2, Z3, Z4, Z5, STR_STAB, STR_END, STR_MAX, STR_PLY, rpe, note
```

Седмичните тотали се разпределят до дневни/сесийни редове според избраните почивни, двусесийни, интензивни, силови и дълги дни. Източникът се записва като `manual_weekly_distribution`, за да не се смесва с реално измерена едносекундна история.


## Обратна съвместимост на силовия вход

Стар файл с една колона `STR` или `real_STR` остава валиден. При липса на новите четири колони системата отнася тези реални минути към `STR_END` (обща силова издръжливост, `k=1.0`). Ако има и старо поле `strength_k`, старият директен `q_STR` се запазва при анализа.
