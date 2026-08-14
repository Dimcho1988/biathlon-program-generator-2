# HRmod Lab v4 — mirror area shift

## Статус

HRmod Lab е самостоятелна offline лаборатория за завършени TCX активности.
Единственият активен модел е `hrmod_mirror_area_shift_v4` с конфигурация
`hrmod_config_v4`. Няма selector за алтернативни модели, production navigation, database или
Intervals.icu интеграция.

> Резултатът е експериментална HR-еквивалентна хипотеза, не измерена мощност.
> Ако усилието не остави различим HR отговор, HR-only моделът не може да го
> възстанови.

## Строга HR-only граница

Core приема само timezone-aware timestamp, HR/quality flags, индивидуални
`HR_floor`, `HRmax`, пет зони и HR-only конфигурацията. Speed, power, grade,
altitude, distance, cadence и laps не могат да влияят на cleaning, detection,
candidate, зоните или `hr_input_hash`.

TCX parser-ът разделя физически:

1. `hr_input_samples` — единствен core input;
2. `reference_channels` — post-core terrain/reference данни.

## Единствената v4 логика

Detector-ът използва отделен изгладен `h_detect`, но площта винаги се изчислява
от незакръгления `clean_hr` и реалното `dt_s`.

За complete вълна с начало `s`, пик `p`, край `e` и локален baseline `B`:

- receiver е целият възход `[s,p]`;
- donor е целият спад `(p,e]`;
- donor excess е `q_i=max(0, clean_hr_i-B)`;
- наличната площ е `N=sum(q_i*dt_i)`;
- заявената площ е `alpha*N`;
- receiver capacity е ограничен от `HRmax-clean_hr`.

Следпиковите donor проби се оглеждат по нормализирана фаза: ранният donor се
добавя близо до пика, а късният tail — близо до началото на възхода. Алокаторът
прилага HRmax, локален donor floor и optional per-sample caps. За всяка
коригирана вълна:

`sum(added_bpm*dt_s) == sum(removed_bpm*dt_s)`.

Вълната е допустима само ако:

- е complete;
- HR пикът е поне `mirror_min_peak_fraction_hrmax * HRmax`;
- продължителността `e-s` е максимум `mirror_max_wave_duration_s`;
- receiver/donor имат минимална продължителност и площ.

Defaults са 80% HRmax, 180 s и `alpha=1`. Те са регулируеми критерии върху HR
формата, не доказателство за действителната продължителност на усилието.

## Теренен donor филтър

`terrain_downhill_donor_exclusion_v4` се изпълнява само след immutable HR-only candidate.
Default устойчиво спускане е изгладен grade `<= -3%` за поне 5 s.

При v4 филтърът:

- изключва само downhill пробите в donor `(p,e]`;
- не изключва receiver проби при изкачване;
- преизчислява същата вълна със същия exact-area allocator;
- не променя candidate, wave detection или `hr_input_hash`;
- пази отделни `terrain_input_hash` и `final_result_hash`.

Transition buffer е изключен по подразбиране. При липсващ или ненадежден grade
филтърът fail-closed връща `final=candidate`, без да измисля grade=0.

## Опростен UI

Главният екран има четири настройки:

| Параметър | Default | Значение |
|---|---:|---|
| Сила на модулацията | 1.0 | дял от допустимата donor площ |
| Минимален пик | 80% HRmax | eligibility праг |
| Максимална вълна | 180 s | eligibility праг за `e-s` |
| Изглаждане | 5 s | само за detection |

Всички видими параметри имат `?` help. Допълнителните HR и terrain safeguards
са в затворени панели. Готовият core резултат се кешира в session state; смяна
на изглед или terrain параметър не стартира HR core повторно.

Изгледите са условни, не eager tabs:

- **HR-only сигнали** — raw, candidate, final, grade и grouped wave regions;
- **HR вълни** — само избраната вълна с малък padding, `s/p/e`, baseline,
  receiver/donor и final added/removed;
- **HR зони** — raw, clean, candidate и terrain-final времена;
- diagnostics, reference validation и downloads.

Overview използва постоянен малък брой grouped traces/shapes. SVG е default за
браузъри без WebGL; WebGL е изричен opt-in.

## Основни параметри

Detection safeguards: `rise_threshold_bpm_s`, `min_rise_bpm`,
`min_sustained_rise_s`, `fall_threshold_bpm_s`, `min_fall_bpm`,
`min_sustained_fall_s`, `baseline_lookback_s`, `return_tolerance_bpm`,
`max_interpolation_gap_s`, `long_gap_threshold_s`, `max_addition_bpm` и
`max_removal_bpm`.

Terrain safeguards: `downhill_threshold_pct`, `min_sustained_downhill_s`,
`use_transition_buffer`, `terrain_transition_buffer_s`,
`grade_smoothing_window_s`, `derived_grade_window_m`,
`derived_min_distance_span_m`, `min_grade_coverage_fraction` и
`max_terrain_sample_gap_s`.

## Експорти

Core и terrain артефактите остават отделни:

- `processed_hr_timeseries.csv`, `wave_summary.csv`, `zone_summary.csv`;
- configuration и diagnostics JSON;
- `terrain_gated_timeseries.csv`, `terrain_wave_summary.csv`,
  `terrain_zone_summary.csv`, `terrain_result.json`;
- reference/annotations само след отделна post-hoc оценка.

Лични TCX файлове не се commit-ват или export-ват в repository.

## Локално стартиране

```powershell
python -m pip install -r requirements.txt
python -m streamlit run hrmod_lab_app.py
```

## Методологично ограничение

HR площта не е закон за запазване на механична работа. Настройването трябва да
се валидира върху независими reference канали и hold-out активности, без тези
канали да влизат в HR-only core. Визуално по-правдоподобни зони сами по себе си
не доказват физиологична валидност.
