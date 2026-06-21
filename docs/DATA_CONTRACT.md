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

## Сезонни и седмични предпочитания

```text
season_start, season_end, annual_target_hours, annual_goal_influence,
min_volume_factor, max_volume_factor, sessions_per_week, rest_days,
double_session_days, long_session_day, intensity_days, strength_days,
max_key_sessions_per_week, double_threshold_enabled,
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
