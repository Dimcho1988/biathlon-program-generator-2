# HRmod Lab v2 — HR-only преместване на площта на вълната

## Статус и предназначение

Активният модел е `hrmod_wave_area_shift_v2`, с versioned конфигурация
`hrmod_config_v2`. Това е изолиран, offline и експериментален модул за завършени
TCX активности. Той не е част от production onFlows, не чете Intervals.icu или
база данни и не добавя production navigation entry.

Моделът тества проста хипотеза: когато наблюдаваният HR образува устойчива
rise–peak–fall вълна, част от площта в по-късния спад може да се премести в
по-ранното покачване. Добавената и отнетата площ са еднакви. Това е времево
преразпределение на наблюдавания HR отговор, а не оценка на механична мощност.

> HRmod v2 преразпределя във времето част от наблюдавания HR отговор. Ако кратко усилие не остави различим HR отговор, HR-only моделът не може да го възстанови. Ако HR спада по време на продължаващо реално усилие, моделът не може да го знае без независим reference канал. Затова резултатът е експериментална HR-еквивалентна оценка, а не измерена мощност.

## Непроменима HR-only граница

Публичният core приема само:

- timezone-aware `timestamp`;
- измерен HR и HR quality flags;
- реалното `dt` между пробите;
- индивидуални `HRmax`, `HR_floor` и пет HR зони;
- сериализируемата HR-only v2 конфигурация.

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

## Deterministic rise–peak–fall state machine

Една вълна съдържа локален baseline `B`, начало на rise `s`, действителен peak
`p` и край на установения fall `e`.

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

## Точно преместване на HR площта

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

Главният UI показва само тези четири настройки:

| Поле | Default | Единица | Значение |
|---|---:|---|---|
| `alpha` | `1.0` | дял | заявен дял от допустимата donor площ |
| `rise_threshold_bpm_s` | `0.15` | bpm/s | праг за устойчиво начало на rise |
| `min_rise_bpm` | `5.0` | bpm | минимално общо покачване |
| `smoothing_window_s` | `5.0` | s | time-based smoothing само за detection |

Defaults са exploratory, централизирани, versioned и сериализируеми. Те не са
физиологично валидирани константи.

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

## Изходна схема

`timeseries` съдържа поне:

- `timestamp`, `elapsed_s`, `dt_s`;
- `raw_hr_bpm`, `clean_hr_bpm`, `h_detect_bpm`, `trend_bpm_per_s`;
- `segment_id`, `wave_id`, `wave_state`, `local_baseline_hr_bpm`;
- `receiver_flag`, `donor_flag`, `added_bpm`, `removed_bpm`, `hrmod_bpm`;
- raw/clean/hrmod zone labels;
- quality и model flags.

`wave_summary` съдържа границите `s/p/e`, status/end/skip reason, baseline и
donor floor, rise/fall, receiver/donor durations, donor/requested/capacity/moved
площи, added/removed площи, balance error, capacity limitation и per-wave
`raw_zone_seconds`, `clean_zone_seconds`, `hrmod_zone_seconds`,
`hrmod_minus_raw_zone_seconds`, `hrmod_minus_clean_zone_seconds`.

`zone_summary` съдържа seconds, percent и `hrmod - clean_hr` за всяка зона за
цялата активност.

Diagnostics показва HR coverage, sampling regularity, artifacts, interpolation,
gaps, detection support, detected/complete/incomplete/corrected/skipped waves,
donor/requested/capacity/moved/added/removed площи, capacity-limited площ и
брой, skip-reason distribution и area-conservation error/pass.

## Streamlit workflow и визуализации

1. Качете `.tcx` с HR.
2. Прегледайте quality отчета и информативния observed maximum.
3. Задайте индивидуалния профил и петте зони.
4. Проверете четирите главни v2 настройки; при нужда отворете collapsed advanced
   safeguards.
5. Натиснете **Изчисли HRmod (само HR)**.
6. В overview графиката сравнете `clean_hr` и `hrmod`; по желание покажете тънката
   detection-only линия `h_detect`.
7. Receiver и donor областите са различно оцветени; вертикалните `s`, `p`, `e`
   markers и локалният baseline показват границите.
8. Изберете wave от selector-а. Отделната графика автоматично zoom-ва до нея и
   показва къде е добавено и къде е отнето.
9. Прегледайте wave таблицата, цялостното time-in-zone сравнение и diagnostics.
10. Едва тогава използвайте отделния **Reference validation** tab.

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

Downloads tab предоставя:

- `processed_hr_only_timeseries.csv`;
- `wave_summary.csv`;
- `zone_summary.csv`;
- `run_configuration.json`;
- `diagnostics.json`;
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
