# HRmod Lab v3 — morphology-aware HR-only преместване на площта

## Статус и предназначение

Активният default модел е `hrmod_wave_area_shift_v3`, с versioned конфигурация
`hrmod_config_v3`. `v2_legacy` остава изрично избираем само за диагностично
сравнение. Това е изолиран, offline и експериментален модул за завършени
TCX активности. Той не е част от production onFlows, не чете Intervals.icu или
база данни и не добавя production navigation entry.

Моделът разделя HR вълните по морфология. Compact формите запазват v2
rise–peak–fall преместването. В преходния 30–45 s band нееднозначната форма
намалява v2 площта плавно чрез `v2_fade_out`, вместо да я прекъсва рязко.
Sustained формите изискват устойчив hold и потвърден остър terminal fall; donor
е само този terminal fall, а hold target ограничава receiver добавянето. От
45 s нататък нееднозначните дълги форми се пропускат fail-closed.
Добавената и отнетата площ винаги са еднакви. Това е времево преразпределение
на наблюдавания HR отговор, а не оценка на механична мощност.

> HRmod v3 експериментално преразпределя във времето част от наблюдавания HR отговор. Ако кратко усилие не остави различим HR отговор, HR-only моделът не може да го възстанови. При HR-derived receiver фаза до 30 s той запазва compact v2, между 30 и 45 s използва плавен преход/fade, а от 45 s коригира само при устойчив hold и потвърден остър краен спад; нееднозначните дълги форми се пропускат. Моделът не може да знае реалното механично натоварване без независим reference канал. Резултатът е HR-еквивалентна хипотеза, а не измерена мощност.

## Непроменима HR-only граница

Публичният core приема само:

- timezone-aware `timestamp`;
- измерен HR и HR quality flags;
- реалното `dt` между пробите;
- индивидуални `HRmax`, `HR_floor` и пет HR зони;
- сериализируемата HR-only v3 конфигурация.

Speed, power, grade, altitude, distance, cadence, TCX laps, sport metadata и
ръчни annotations нямат представяне в core input schema. Те не могат да влияят
върху cleaning, `h_detect`, wave detection, local baseline, receiver/donor
границите, преместената площ, зонирането или `hr_input_hash`.

TCX adapter-ът създава две физически различни структури:

1. `hr_input_samples` — единственият вход към `compute_hrmod_hr_only`;
2. `reference_channels` — недостъпен за core контейнер за последваща оценка.

Следователно еднакви timestamps, HR и HR-only config дават числено идентичен
core резултат и hash при различни, липсващи или премахнати reference канали.

Raw ski speed е само контекст: високата скорост при спускане може да съвпада с
ниско двигателно усилие и не се преобразува автоматично в intensity или power.

## Подготовка на HR сигнала

`raw_hr_bpm` остава непроменен. Deterministic preprocessing маркира артефакти,
прави само допустимата кратка интерполация и създава `clean_hr_bpm`. Крайният
`hrmod_bpm` винаги започва от `clean_hr_bpm`.

`h_detect_bpm` е отделна, леко и робастно изгладена time-based серия само за
тенденция и граници. Тя не е измерен пулс и никога не заменя `clean_hr_bpm` при
изчисляване на donor area, receiver capacity или крайния сигнал. Наклонът
`trend_bpm_per_s` също се използва само от detector-а.

Изчисленията използват действителните timestamps и `dt_s`; не се предполага
sampling през 1 s. Gap над `long_gap_threshold_s` започва нов независим segment.
Вълна и HR площ никога не пресичат такава граница.

## Deterministic HR morphology state machine

Всяка вълна съдържа локален baseline `B`, начало на rise `s`, действителен peak
`p` и край `e`. Sustained формата добавя начало/край на hold `u/h`, hold target
`H` и начало/край на terminal fall `d/f`.

### 1. Търсене на устойчив rise

Кандидатът започва при `trend_bpm_per_s >= rise_threshold_bpm_s`. Условието
трябва да продължи поне `min_sustained_rise_s`, а общото покачване трябва да
достигне `min_rise_bpm`. След потвърждение `s` се връща до първата проба на
устойчивото покачване, не остава при късния момент на потвърждение.

### 2. Локален baseline

`B` е робастната медиана на валидния `clean_hr_bpm` в time-based прозореца
`baseline_lookback_s` непосредствено преди `s`. Нужни са поне
`baseline_min_points`. Не се използват глобален resting HR или reference данни.

Недостатъчната pre-rise история прави вълната incomplete; default edge policy
не я коригира. При нов rise преди пълно възстановяване се използва нов baseline
около новия локален trough, а не baseline от предишната вълна.

### 3. Peak и потвърден fall

След `s` state machine следи максимума на `h_detect_bpm`. Fall се потвърждава,
когато тенденцията е поне `fall_threshold_bpm_s` в отрицателна посока за
`min_sustained_fall_s` и спадът достига `min_fall_bpm`. `p` е действителният
максимум между `s` и потвърждението, а не моментът на късното потвърждение.

### 4. Край на donor частта

Donor започва от първата проба след `p` и завършва в най-ранното допустимо `e`:

1. `h_detect_bpm` се задържи в `return_tolerance_bpm` над `B` за
   `return_sustain_s`;
2. нов устойчив rise затвори текущия fall в непосредствения локален trough;
3. наклонът стане неутрален в `neutral_slope_tolerance_bpm_s` за
   `neutral_trough_timeout_s`;
4. бъде достигнат `max_wave_duration_s`;
5. възникне long gap или свърши файлът.

Първите три причини могат да затворят complete вълна при изпълнени минимални
rise, fall, receiver и donor критерии. Max duration, gap, file end без надеждно
затваряне или липсващ fall дават incomplete/skipped wave по default. Продължителен
plateau не се включва като donor само защото HR остава над `B`.

## V3 morphology решение

Morphology границите и допустимостта в `v3_auto` използват HR-only
`h_detect_bpm`, `trend_bpm_per_s`, detection support и elapsed time. Hold target
`H` и самото area преразпределение използват `clean_hr_bpm`; външни канали не
участват:

- `compact`: receiver продължителност до `compact_full_v2_s` (default 30 s)
  запазва пълната v2 allocation;
- `transition`: между `compact_full_v2_s` и `sustained_full_v3_s` (default
  45 s) `transition_weight` нараства непрекъснато от 0 до 1 — няма hard switch
  при 30 s;
- `sustained`: изисква hold поне `min_hold_duration_s`, ограничен от
  `hold_slope_tolerance_bpm_s` и `hold_band_bpm`, последван от terminal fall;
- `ambiguous` в 30–45 s band: липсва надежден hold/terminal fall и v2 площта се
  fade-ва непрекъснато към нула;
- `ambiguous` от 45 s: няма достатъчно еднозначна sustained форма и площ не се
  премества.

Terminal fall започва при устойчив отрицателен наклон
`terminal_fall_threshold_bpm_s`, минимална продължителност
`terminal_fall_min_duration_s` и спад `terminal_fall_min_drop_bpm`. Краят му се
потвърждава след `terminal_fall_release_s` при release slope
`terminal_fall_release_slope_bpm_s`; `terminal_fall_max_duration_s` е защитен
лимит. `correction_strategy` е един от `v2_full_tail`, `v3_transition`,
`v3_terminal_fall`, `v2_fade_out` или `none`.

За sustained вълна donor е само `[d,f]`. Receiver може да обхваща rise и
поддържащата част, но свободният му капацитет е само до робастно оценения `H`,
не до HRmax. Пробите, които вече са около `H`, не се повдигат. Така платото е
референция/таван, а не област, която задължително получава корекция.

`v2_legacy` изключва morphology решението и възпроизвежда пълната v2
rise–peak–fall allocation за диагностично сравнение. Streamlit изчислява само
избрания variant; отделните изпълнени конфигурации се пазят в session cache.

## Точно преместване на HR площта — compact/v2 allocation

За complete валидна вълна receiver прозорецът е `R = [s, p]`, а donor
прозорецът е `T = (p, e]`. Те не се припокриват. Нека `h_i` е
`clean_hr_bpm`, а `dt_i` е реалната продължителност на пробата в секунди.

Локалната donor граница е:

\[
F = \max(B, HR_{floor})
\]

Допустимият donor излишък и общата donor площ са:

\[
q_i = \max(0, h_i-F), \quad i\in T
\]

\[
N = \sum_{i\in T}q_i\,dt_i
\]

`q_i` е в bpm, а `N` е в bpm·s. При `alpha \in [0,1]` заявената площ е:

\[
M^* = \alpha N
\]

Свободният receiver капацитет до индивидуалния HRmax е:

\[
u_i = \max(0, HR_{max}-h_i), \quad i\in R
\]

\[
C = \sum_{i\in R}u_i\,dt_i
\]

Реално преместената площ е:

\[
M = \min(M^*, C)
\]

При нулеви `N` или `C` няма корекция и причината се записва. Ако `C < M*`,
вълната е `capacity_limited`; добавянето и отнемането се намаляват симетрично.

Optional per-sample caps са изключени по default. Ако бъдат включени, нека
`q'_i = min(q_i, max_removal_bpm)` и
`u'_i = min(u_i, max_addition_bpm)` за съответния активен cap; при изключен cap
съответната форма остава непроменена (`q'=q` или `u'=u`). Ефективните площи са
`N_remove = sum(q'_i dt_i)` и `C_add = sum(u'_i dt_i)`, а реалното преместване
става `M = min(M*, N_remove, C_add)`. Така cap-ът може допълнително да ограничи
`M`, но добавената и отнетата площ пак се намаляват симетрично и се маркира
`capacity_limited`.

При default режима без caps от donor частта се отнема пропорционално на
излишъка:

\[
r_i = q_i\frac{M}{N}, \quad i\in T
\]

В receiver частта се добавя пропорционално на свободния HRmax капацитет:

\[
a_i = u_i\frac{M}{C}, \quad i\in R
\]

При включени caps същото пропорционално разпределение използва capped формите:
`r_i = q'_i M/N_remove` и `a_i = u'_i M/C_add`.

Това естествено дава по-голямо добавяне на ранните проби с повече свободен
капацитет. Не е нужен отделен параметър за front loading.

Крайният сигнал е:

\[
HRmod_i =
\begin{cases}
h_i+a_i, & i\in R\\
h_i-r_i, & i\in T\\
h_i, & i\notin R\cup T
\end{cases}
\]

За всяка коригирана вълна численият инвариант е:

\[
\sum_{i\in R}a_i dt_i
=
\sum_{i\in T}r_i dt_i
=M
\]

и следователно:

\[
\sum_{i\in R\cup T}(HRmod_i-h_i)dt_i=0
\]

Floating-point остатъкът се отчита като `area_balance_error_bpm_s` и се
сравнява с versioned tolerance. Добавянето не преминава HRmax, а отнемането не
преминава под `F`.

### Значение на alpha

- `alpha = 0`: `hrmod_bpm == clean_hr_bpm`;
- `alpha = 0.5`: заявява 50% от допустимата donor площ;
- `alpha = 1`: заявява цялата допустима descending площ над `F`;
- при недостатъчен receiver капацитет `M` се ограничава до `C`;
- `alpha` никога не може да е над 1.

При `alpha = 1` и достатъчен капацитет donor пробите достигат точно `F`. Това е
очакваното агресивно поведение на експерименталния модел.

## Основни настройки

Главният UI показва `model_variant` и тези четири настройки:

| Поле | Default | Единица | Значение |
|---|---:|---|---|
| `alpha` | `1.0` | дял | заявен дял от допустимата donor площ |
| `rise_threshold_bpm_s` | `0.15` | bpm/s | праг за устойчиво начало на rise |
| `min_rise_bpm` | `5.0` | bpm | минимално общо покачване |
| `smoothing_window_s` | `5.0` | s | time-based smoothing само за detection |

Defaults са exploratory, централизирани, versioned и сериализируеми. Те не са
физиологично валидирани константи.

V3 morphology safeguards са в отделен затворен панел:

| Поле | Default | Роля |
|---|---:|---|
| `compact_full_v2_s` | `30.0` | край на пълната compact v2 тежест |
| `sustained_full_v3_s` | `45.0` | начало на пълната sustained v3 тежест |
| `min_hold_duration_s` | `12.0` | минимален устойчив hold |
| `hold_slope_tolerance_bpm_s` | `0.08` | допустим абсолютен hold trend |
| `hold_band_bpm` | `3.0` | робастен hold band около target `H` |
| `terminal_fall_threshold_bpm_s` | `0.20` | минимален отрицателен terminal trend |
| `terminal_fall_min_duration_s` | `3.0` | минимална продължителност на fall |
| `terminal_fall_min_drop_bpm` | `4.0` | минимален общ terminal спад |
| `terminal_fall_release_slope_bpm_s` | `0.05` | release slope праг |
| `terminal_fall_release_s` | `2.0` | устойчив release период |
| `terminal_fall_max_duration_s` | `30.0` | защитен максимум на donor прозореца |
| `terminal_recovery_min_s` | `3.0` | минимален нисък recovery престой след острия спад преди следващ rise |
| `terminal_rebound_guard_s` | `10.0` | прозорец, в който връщане към предишния detect-domain hold прави спада нееднозначен |

При вълна, затворена от нов rise, тези две защити различават терминален спад с
нисък recovery престой от временен dip с бързо връщане към предишния hold. Нееднозначният
случай fail-closed остава некоригиран и се маркира с `morphology_reason=ambiguous_terminal_vs_transient_dip`.

## Advanced detection safeguards

Панелът е затворен по default. Всички стойности се записват в export config.

| Поле | Default | Роля |
|---|---:|---|
| `min_sustained_rise_s` | `3.0` | минимално устойчиво rise време |
| `fall_threshold_bpm_s` | `0.10` | абсолютен праг за отрицателен trend |
| `min_sustained_fall_s` | `3.0` | минимално устойчиво fall време |
| `min_fall_bpm` | `3.0` | минимален общ спад |
| `baseline_lookback_s` | `20.0` | pre-rise baseline прозорец |
| `baseline_min_points` | `3` | минимум валидни baseline проби |
| `return_tolerance_bpm` | `2.0` | допустимо връщане над baseline |
| `return_sustain_s` | `3.0` | устойчивост при връщане |
| `neutral_slope_tolerance_bpm_s` | `0.05` | неутрален trend band |
| `neutral_trough_timeout_s` | `8.0` | timeout за trough/plateau |
| `min_receiver_duration_s` | `3.0` | минимален receiver |
| `min_donor_duration_s` | `3.0` | минимален donor |
| `max_wave_duration_s` | `600.0` | защитна максимална вълна |
| `max_interpolation_gap_s` | `3.0` | максимална кратка HR интерполация |
| `long_gap_threshold_s` | `10.0` | segment boundary |
| `edge_wave_policy` | `skip_incomplete` | incomplete вълните не се коригират |
| `max_addition_bpm` | `null` | optional per-sample cap, изключен |
| `max_removal_bpm` | `null` | optional per-sample cap, изключен |

Останалите технически defaults са:

| Поле | Default |
|---|---:|
| `smoothing_method` | `robust_local_linear` |
| `smoothing_min_points` | `3` |
| `smoothing_robust_iterations` | `2` |
| `artifact_min_hr_bpm` | `25.0` |
| `artifact_max_hr_bpm` | `250.0` |
| `artifact_max_rate_bpm_per_s` | `20.0` |
| `artifact_spike_deviation_bpm` | `12.0` |
| `sampling_regularity_tolerance_s` | `0.25` |
| `area_conservation_tolerance_bpm_s` | `0.000001` |

## Индивидуален профил и зони

Потребителят задава `HR_floor`, `HRmax` и точно пет строго нарастващи,
ненаслагващи се зони в тези граници. HRmax не се извежда по възраст или от
observed maximum. `raw_hr`, `clean_hr` и `hrmod` се класифицират по
незакръглените floating-point стойности.

Приет `clean_hr` извън зададените HR граници се показва като явен проблем за
профила или артефакт. Няма тихо clipping, което да го прикрие.

## Публичен API

```python
from hrmod_lab.hrmod_core import compute_hrmod_hr_only

hrmod_result = compute_hrmod_hr_only(
    hr_samples=parsed.hr_input_samples,
    athlete_profile=athlete_hr_profile,
    config=hrmod_config,
)
```

Core не приема generic TCX dataframe, reference columns, Streamlit state,
файлове, environment variables или база данни.

Едва след готовия immutable core резултат може да се извика:

```python
from hrmod_lab.reference_validation import evaluate_against_reference

validation_result = evaluate_against_reference(
    hrmod_result=hrmod_result,
    reference_channels=parsed.reference_channels,
    reference_config=reference_config,
    optional_annotations=annotations,
)
```

Reference evaluator-ът не мутира `hrmod_result`. Overlay или annotation промяна
не стартира нов core run; ново изчисление има само при изрично натискане на
бутона **Изчисли HRmod (само HR)**.

### Terrain gate v1 — отделен post-processing слой

`terrain_gate_v1` работи единствено след готовия immutable HR-only резултат.
Grade, altitude и distance никога не влизат в wave detection, candidate
корекцията или `hr_input_hash`. Слоят пази три отделни серии: `raw_hr`,
`hrmod_candidate` (непромененият избран HR-only core резултат) и `hrmod_final`.

```python
from hrmod_lab.terrain_gate import TerrainGateConfig, apply_terrain_gate

terrain_result = apply_terrain_gate(
    hrmod_result=hrmod_result,
    reference_channels=parsed.reference_channels,
    config=TerrainGateConfig(),
)
```

Default спускане е изгладен grade `<= -3.0%`, устойчив поне 5 s, с 5 s
transition buffer. Grade се използва директно, когато е надеждно наличен; иначе
може да се изведе линейно от качествени altitude/distance проби. Единичен
отрицателен spike не е спускане и при липсващ/ненадежден grade не се измисля
`grade=0`: резултатът е `terrain_gate_unavailable`, candidate остава видим и
не се представя като филтриран.

| Terrain настройка | Default | Роля |
|---|---:|---|
| `terrain_gate_enabled` | `true` | включва само post-core gate |
| `downhill_threshold_pct` | `-3.0` | inclusive downhill праг |
| `min_sustained_downhill_s` | `5.0` | отхвърля кратки grade spikes |
| `terrain_transition_buffer_s` | `5.0` | пази преходите към/от спускане |
| `grade_smoothing_window_s` | `7.0` | centred running median за готов grade |
| `derived_grade_window_m` | `30.0` | centred distance span за derived grade |
| `derived_min_distance_span_m` | `10.0` | минимум надежден distance span |
| `min_grade_coverage_fraction` | `0.80` | минимум валидно покритие |
| `max_terrain_sample_gap_s` | `5.0` | прекъсва downhill continuity при по-голяма липса на terrain проби |

При derived grade първо се прилага 5-point median върху altitude, след което
наклонът се изчислява от altitude промяната върху centred distance span. Това е
векторизиран/линеен спрямо броя проби процес, без samples × waves вложен цикъл.
Устойчивият downhill интервал използва полуотворени sample интервали и никога не
се пренася през terrain data gap, по-голям от `max_terrain_sample_gap_s`.

Ако buffered sustained-downhill интервал засегне receiver, donor или граница
на candidate wave, цялата корекция се маркира `terrain_confounded`.
`hrmod_final` се връща към raw HR за цялата вълна и
`moved_area_final_bpm_s=0`; другите неприпокриващи се вълни не се променят.
Terrain provenance е отделен в `terrain_input_hash`/`final_result_hash`.

## Изходна схема

`timeseries` съдържа поне:

- `timestamp`, `elapsed_s`, `dt_s`;
- `raw_hr_bpm`, `clean_hr_bpm`, `h_detect_bpm`, `trend_bpm_per_s`;
- `segment_id`, `wave_id`, `wave_state`, `local_baseline_hr_bpm`;
- `receiver_flag`, `donor_flag`, `added_bpm`, `removed_bpm`, `hrmod_bpm`;
- raw/clean/hrmod zone labels;
- quality и model flags.

В transition случая `receiver_flag` и `donor_flag` маркират support-а на
крайните ефективни signed deltas след blend/cancellation, а не едновременно
двата концептуални branch прозореца.

`wave_summary` съдържа `morphology`, `correction_strategy`, `transition_weight`,
границите `s/p/e` и optional `u/h/d/f`, `hold_target_hr_bpm`, status/end/skip
reason, baseline и donor floor, rise/fall, receiver/donor durations,
donor/requested/capacity/moved площи, added/removed площи, balance error,
capacity limitation и per-wave
`raw_zone_seconds`, `clean_zone_seconds`, `hrmod_zone_seconds`,
`hrmod_minus_raw_zone_seconds`, `hrmod_minus_clean_zone_seconds`.

`zone_summary` съдържа seconds, percent и `hrmod - clean_hr` за всяка зона за
цялата активност.

Diagnostics показва HR coverage, sampling regularity, artifacts, interpolation,
gaps, detection support, detected/complete/incomplete/corrected/skipped waves,
donor/requested/capacity/moved/added/removed площи, capacity-limited площ и
брой, skip-reason distribution и area-conservation error/pass.

Отделният terrain result добавя `raw_hr_bpm`, `hrmod_candidate_bpm`,
`hrmod_final_bpm`, `smoothed_grade_pct`, `downhill_mask`,
`buffered_downhill_mask` и per-sample `terrain_status`. Terrain wave таблицата
съдържа `terrain_status`, `terrain_rejection_reason`, `downhill_overlap_s`,
`downhill_overlap_fraction`, `min_smoothed_grade_pct`,
`moved_area_candidate_bpm_s` и `moved_area_final_bpm_s`.

Terrain result има и отделен `zone_summary`: `raw_seconds`/`raw_percent`,
`hrmod_candidate_seconds`/`hrmod_candidate_percent`,
`hrmod_final_seconds`/`hrmod_final_percent` и
`final_minus_candidate_seconds` за всяка HR зона. Candidate е immutable
HR-only core резултатът, а final е post-gate резултатът. При изключен или
недостъпен terrain gate final е равен на candidate. Core `zone_summary` и
`zone_summary.csv` остават непроменени HR-only артефакти.

## Streamlit workflow и визуализации

1. Качете `.tcx` с HR.
2. Прегледайте quality отчета и информативния observed maximum.
3. Задайте индивидуалния профил и петте зони.
4. Изберете `v3_auto` (default) или `v2_legacy` за диагностично сравнение и
   проверете четирите главни настройки; при нужда отворете collapsed safeguards.
5. Натиснете **Изчисли HRmod (само HR)**.
6. Включете/изключете **Enable terrain gate** и задайте видимия **Downhill
   threshold**. Продължителността, transition buffer и smoothing са в затворения
   **Advanced terrain settings** панел. Тези промени преизчисляват само terrain
   слоя и никога HR-only core.
7. Изберете изглед **HR-only сигнали**. Overview графиката различава raw HR,
   HRmod candidate и HRmod final; по желание покажете тънката detection-only линия `h_detect`.
   Съвместимият SVG renderer е включен по подразбиране и не изисква WebGL.
   **WebGL ускорение** е отделна opt-in настройка само за браузъри с работещ WebGL.
8. Receiver, donor, sustained-downhill и terrain-confounded областите са
   различно оцветени; вертикалните `s/p/e` и optional `u/h/d/f` markers,
   локалният baseline `B` и hold target `H` показват границите.
9. Изберете изглед **HR вълни**, после wave от selector-а. Графиката зарежда само
   избраната вълна с малък времеви padding и показва къде е добавено и къде е отнето.
10. Прегледайте wave таблицата, terrain summary, time-in-zone и diagnostics.
11. Едва тогава използвайте отделния изглед **Reference validation**.

Само избраният резултатен изглед се изгражда при даден Streamlit rerun. Готовият
immutable HR-only резултат остава в session state, така че превключването между
overview и wave zoom не стартира core повторно. Изпълнените v3/v2 config варианти
се кешират поотделно; variant се преизчислява само след изрично submit-ване, ако
още не е кеширан. Receiver/donor областите, morphology маркерите и baseline/hold
сегментите са групирани в постоянен малък брой Plotly traces.
Downhill интервалите и отхвърлените вълни също са по един групиран trace с
`None` разделители; не се създават per-wave Plotly shapes.

## Reference validation без leakage

Reference join се прави след core result и core hash. Допустимата интерпретация
е ограничена:

- измерена/оценена power е количествена референция само при известен произход и
  индивидуални power zones;
- контролирана treadmill speed с проверен grade може да е protocol reference;
- outdoor running speed е само контекст без валидиран sport/grade model;
- raw ski speed винаги е само контекст;
- laps и manual markers са post-hoc annotations.

Reference данните не настройват автоматично `alpha` или detection параметрите.
Correlation сама по себе си не доказва валидност.

## Експорти и възпроизводимост

Изгледът **Downloads** предоставя:

- `processed_hr_only_timeseries.csv`;
- `wave_summary.csv`;
- `zone_summary.csv`;
- `run_configuration.json`;
- `diagnostics.json`;
- `terrain_gated_timeseries.csv` с raw/candidate/final HRmod, smoothed grade,
  downhill mask и terrain status;
- `terrain_wave_summary.csv` и `terrain_result.json` с решенията, hashes и
  terrain diagnostics;
- `terrain_zone_summary.csv` с raw, HRmod candidate и terrain-final seconds,
  percent и final-minus-candidate delta по зони;
- отделен reference comparison CSV/JSON само след такава оценка;
- annotations CSV/JSON при налични annotations;
- ZIP със същите артефакти.

За възпроизводим run се пазят `model_version`, `config_version`, пълната config,
профилът, parser config, `hr_input_hash` и TCX provenance. Лични TCX файлове не
се commit-ват. Core export-ите не съдържат speed/power/lap колони.

## Локално стартиране

От repository root:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run hrmod_lab_app.py
```

Това е единственият standalone entry point; не се създава отделно приложение.

## Ограничения и бъдеща калибрация

HR-only входът не съдържа информация за усилие, което не е оставило различим HR
отговор, нито за продължаващо усилие при спадащ HR. Wave detector-ът може да
пропусне физиологично значими промени или да интерпретира HR-only форма, която
има друга причина. Capacity limit и incomplete/skipped waves са наблюдения за
математическата приложимост, не физиологична диагноза.

Defaults не трябва да се настройват визуално по един спортист или по speed.
Следващата научна стъпка е предварително регистриран лабораторен протокол с
отделни calibration и hold-out cohorts, надежден независим criterion, повторни
активности, measurement-error/uncertainty анализ и предварително зададени primary
metrics. Failure cases и parameter sensitivity трябва да се докладват наред с
резултатите. Ако `alpha=1` е недостатъчен, това е доказателство за границата на
HR-only реконструкцията, не разрешение да се създава ненаблюдавана HR площ.
