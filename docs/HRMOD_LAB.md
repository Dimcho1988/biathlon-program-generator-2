# HRmod Lab v1 — HR-only експериментален модел

## Статус и предназначение

HRmod Lab е самостоятелна лабораторна програма за офлайн анализ на вече завършена активност от TCX файл. Тя не е част от продукционната навигация на onFlows, не чете или записва продукционната база данни и не се включва в Intervals.icu потока.

`HRmod` не е измерен пулс, механична мощност, кислороден дълг или доказана физиологична величина. Той е експериментален, синтетичен HR-еквивалентен сигнал на времево реконструирано натоварване. Конкретният inverse модел и консервативното преразпределяне на площ са хипотеза за бъдеща калибрация и независима валидация.

> **Основно научно ограничение:** ако кратко усилие не остави различим HR отговор, HR-only моделът не може надеждно да го възстанови. Моделът преразпределя наблюдавания HR отговор и не трябва да измисля ненаблюдавана мощност.

## Непроменима HR-only граница

Core моделът приема само:

- timezone-aware timestamp;
- измерен HR и прозрачни HR quality/provenance флагове; `clean_hr` се създава вътре в core preprocessing-а;
- индивидуални `HRmax`, `HR_floor` и пет HR зони;
- сериализируема конфигурация на HR кинетичния модел.

Speed, power, distance, altitude, grade, cadence, TCX laps и ръчни markers/intervals физически не са аргументи на core API. Те не участват в smoothing, inverse kinetics, episode detection, площта или размера на корекцията. Parser adapter-ът ги връща в отделна `reference_channels` структура. Reference оценката се изпълнява само след готов и hash-нат core резултат.

Тази граница е съществена и за ски бягането: суровата скорост зависи от спускане, терен, сняг, вятър и техника. Висока скорост може да съвпадне с ниска двигателна мощност. Затова raw ski speed е единствено контекст, не се превръща автоматично в интензивност и никога не се нарича „реална мощност“.

## Научна хипотеза и точни формули

Измереният HR се разглежда като забавен и изгладен first-order отговор на латентен HR-еквивалентен demand. Обработката е по независими непрекъснати сегменти; дълъг data gap никога не се пресича. Реалните timestamp разлики задават `dt_s` — не се приема, че всяка стъпка е точно 1 s.

За `clean_hr` \(h_i\) при време \(t_i\), отделната robust-smoothed серия \(\tilde h(t)\) се използва само за производната и inverse kinetics. При look-ahead/dead time \(\delta\), в секунди:

\[
g_i = \tilde h(t_i + \delta)
\]

Интерполацията за \(g_i\) е само в същия непрекъснат HR сегмент. Производната е в bpm/s:

\[
\dot g_i = \frac{d\tilde h}{dt}(t_i + \delta)
\]

Времевата константа е в секунди и зависи от знака на производната:

\[
\tau_i =
\begin{cases}
\tau_{on}, & \dot g_i \ge 0 \\
\tau_{off}, & \dot g_i < 0
\end{cases}
\]

Предварителният латентен demand и суровата корекция са в bpm:

\[
d_i^{raw} = g_i + \tau_i \dot g_i
\]

\[
c_i^{raw} = d_i^{raw} - \tilde h(t_i)
\]

Look-ahead означава, че алгоритъмът използва бъдещи спрямо \(t_i\) HR проби. Следователно v1 е **офлайн алгоритъм**, а не real-time оценка.

## HR-only response episodes

Episode detector-ът е детерминистична state machine върху формата на \(c_i^{raw}\), не детектор на реални работни интервали. Той:

1. прилага `correction_deadband_bpm`;
2. отхвърля lobes под `min_lobe_duration_s` или `min_lobe_area_bpm_s`;
3. започва episode от значима положителна lobe;
4. свързва последващи положителни и отрицателни lobes, включително повторни HR вълни;
5. завършва след отрицателна фаза и баланс в tolerance или след достатъчно дълъг neutral gap;
6. прекъсва при long gap или `max_episode_duration_s`;
7. маркира incomplete start/end/gap episodes.

По подразбиране incomplete edge episodes се маркират, но не се коригират (`skip_incomplete`). Episode е математически response window и не доказва Z5, механична работа или протоколен интервал.

## Консервативно запазване на HR площта

В завършен episode \(E_j\):

\[
p_i = \max(0,c_i^{raw}), \qquad n_i = \max(0,-c_i^{raw})
\]

Наличните площи, в bpm·s, са:

\[
P_j = \sum_{i \in E_j} p_i \Delta t_i, \qquad
N_j = \sum_{i \in E_j} n_i \Delta t_i
\]

При \(\alpha \in [0,1]\) желаната балансирана площ е:

\[
M_j^* = \alpha \min(P_j,N_j)
\]

Капацитетът до индивидуалните HR граници е:

\[
C_j^+ = \sum_{i \in E_j,p_i>0}\max(0,HR_{max}-h_i)\Delta t_i
\]

\[
C_j^- = \sum_{i \in E_j,n_i>0}\max(0,h_i-HR_{floor})\Delta t_i
\]

Реално преместваната площ е:

\[
M_j = \min(M_j^*,C_j^+,C_j^-)
\]

Deterministic capped water-filling разпределя добавката \(a_i\) по формата на \(p_i\), а отнемането \(r_i\) по \(n_i\), при:

\[
0 \le a_i \le HR_{max}-h_i, \qquad
0 \le r_i \le h_i-HR_{floor}
\]

\[
\sum a_i\Delta t_i = \sum r_i\Delta t_i = M_j
\]

Крайният сигнал е:

\[
HRmod_i = h_i + a_i-r_i
\]

и за всеки коригиран завършен episode важи инвариантът:

\[
\sum_{i \in E_j}(HRmod_i-h_i)\Delta t_i = 0
\]

`alpha=0` връща точно `hrmod == clean_hr`. `alpha=1` използва най-голямата балансирана корекция, позволена от provisional сигнала и HR границите; стойност над 1 не е допустима в conservative v1.

## Конфигурация и начални стойности

Всички стойности са versioned, сериализируеми и видими в UI и run export. Те са некалибрирани лабораторни настройки, а не физиологично потвърдени константи. Каноничният източник на defaults е `hrmod_lab.schemas.HRmodConfig`; UI не поддържа второ скрито копие.

| Параметър | Единица | Начална стойност | Роля |
|---|---:|---:|---|
| `alpha` | дял | `1.0` | дял от наличната балансирана площ |
| `delay_s` | s | `5` | HR look-ahead/dead time |
| `tau_on_s` | s | `30` | first-order on константа |
| `tau_off_s` | s | `45` | first-order off константа |
| `smoothing_method` | — | `robust_local_linear` | robust local-linear smoothing/differentiation |
| `smoothing_window_s` | s | `15` | времеви прозорец за derivative support |
| `smoothing_min_points` | samples | `5` | минимална локална опора |
| `smoothing_robust_iterations` | iterations | `2` | robust reweighting итерации |
| `correction_deadband_bpm` | bpm | `0.5` | неутрална лента около нулева корекция |
| `min_lobe_duration_s` | s | `3` | минимална продължителност на lobe |
| `min_lobe_area_bpm_s` | bpm·s | `5` | минимална площ на lobe |
| `episode_neutral_gap_s` | s | `15` | neutral gap за приключване |
| `episode_balance_tolerance_bpm_s` | bpm·s | `5` | episode balance tolerance |
| `max_episode_duration_s` | s | `900` | предпазен максимум за episode |
| `max_interpolation_gap_s` | s | `3` | най-дълга разрешена кратка интерполация |
| `long_gap_threshold_s` | s | `10` | граница за независим HR сегмент |
| `edge_episode_policy` | — | `skip_incomplete` | политика за incomplete episode |
| `max_addition_bpm` | bpm | disabled | допълнителен локален cap |
| `max_removal_bpm` | bpm | disabled | допълнителен локален cap |
| `artifact_min_hr_bpm` | bpm | `25` | долна artifact граница |
| `artifact_max_hr_bpm` | bpm | `250` | горна artifact граница |
| `artifact_max_rate_bpm_per_s` | bpm/s | `20` | максимална допустима HR скорост |
| `artifact_spike_deviation_bpm` | bpm | `12` | локална spike-deviation граница |
| `sampling_regularity_tolerance_s` | s | `0.25` | tolerance около очакваната sampling стъпка |
| `area_conservation_tolerance_bpm_s` | bpm·s | `1e-6` | числен tolerance за conservation check |

Точните сериализирани стойности за конкретно изпълнение са в `run_configuration.json`. Това е правилният provenance артефакт при сравняване на лабораторни runs.

`config_version="hrmod_config_v1"` и `kernel_model="first_order_inverse"` са version identifiers, а не свободни калибрационни параметри. V1 приема `smoothing_method` `robust_local_linear` или `local_linear`, и `edge_episode_policy` `skip_incomplete` или експерименталното `correct_if_balanced`.

Parser-only defaults са: `max_bytes=67108864`, `long_gap_threshold_s=30`, `regularity_target_s=1`, `regularity_tolerance_s=0.25` и `assume_naive_timestamps_utc=true`. В Lab UI preview/run long-gap диагностиката се синхронизира с избраната `HRmodConfig.long_gap_threshold_s`; parser-ът само маркира provenance и не изглажда или интерполира HR.

Reference validation има отделна конфигурация, която никога не влияе на HRmod: `join_tolerance_s=0.51`, `sport=null`, quantitative power и controlled treadmill opt-in са изключени, външните зони са празни, `use_annotation_zones=false`, high labels са `Z4,Z5`, `max_lag_s=120` и `lag_step_s=1`. Quantitative power изисква изрично `power_source` и ненаслагващи се power zones; treadmill speed изисква изрично потвърден grade и protocol speed zones.

## Индивидуален HR профил и зони

Потребителят задава изрично `HR_floor`, `HRmax` и четири вътрешни граници, които образуват пет нарастващи, неприпокриващи се зони. Не се използва формула по възраст и observed maximum от файла не се приема автоматично за HRmax; той се показва само информативно.

`raw_hr`, `clean_hr` и `hrmod` се класифицират по незакръглените floating-point стойности. Zone summary съдържа секунди, процент и разликата между `hrmod` и `clean_hr` за всяка зона. Продукционни onFlows модели като `T_eq` не участват.

## TCX workflow и входни схеми

1. TCX се parse-ва безопасно, с namespace и extension поддръжка и без external-entity обработка.
2. Timestamps се нормализират timezone-aware, сортират се детерминистично и се deduplicate-ват.
3. Parser-ът създава две физически различни структури.
4. `hr_input_samples` минава през cleaning и се подава към core.
5. `reference_channels` остава извън core до готов `hrmod_result`.

Точната публична HR-only parser/core-input схема е умишлено тясна:

| Поле | Тип/единица | Значение |
|---|---|---|
| `timestamp` | timezone-aware datetime | време на пробата |
| `heart_rate_bpm` | float/null, bpm | оригинално измерен HR; missing остава explicit null |
| `quality_flags` | tuple[string] | parser provenance като duplicate/missing/gap markers |

`HRSample`/`HRInputSample` няма полета за `clean_hr`, `elapsed_s`, `dt_s`, speed, power, grade, distance, cadence, laps или annotations. Cleaning, кратката интерполация, real `dt_s` и segment assignment са детерминистични core preprocessing стъпки под `HRmodConfig` и се появяват в output timeseries, без да променят оригиналната HR стойност.

Reference структурата може да съдържа `distance_m`, `speed_mps`, `power_w`, `grade`, `altitude_m`, `cadence`, laps и markers. Наличието или стойностите им не променят `hr_input_hash` и никоя core стойност.

## Публични API договори

Core API е тесен по дизайн:

```python
from hrmod_lab.hrmod_core import compute_hrmod_hr_only

hrmod_result = compute_hrmod_hr_only(
    hr_samples=parsed.hr_input_samples,
    athlete_profile=athlete_hr_profile,
    config=hrmod_config,
)
```

Той не приема generic TCX dataframe и няма параметри за intervals, laps, speed, power, grade, cadence или annotations. Core не чете Streamlit state, файлове, environment variables, база данни или Intervals.icu.

Само след core резултата може да се извика отделният договор:

```python
from hrmod_lab.reference_validation import evaluate_against_reference

validation_result = evaluate_against_reference(
    hrmod_result=hrmod_result,
    reference_channels=parsed.reference_channels,
    reference_config=reference_config,
    optional_annotations=annotations,
)
```

Reference evaluator-ът не мутира `hrmod_result`. UI пази core резултата в session state; включване/изключване на overlay или редакция на annotation не извиква повторно core. Нов core run има само при изрично натискане на бутона за изчисление.

### Integration contract за бъдещ onFlows adapter

Бъдещата интеграция трябва да адаптира onFlows activity stream до същата HR-only sample схема, да валидира индивидуалния профил и да извика тесния core API. Едва след получаването и записването на `model_version`, `hr_input_hash`, config и diagnostics може отделен adapter да join-не reference данни за отчет. Production adapter не трябва да разширява core signature или да внася reference колони в HR samples.

Текущата лабораторна задача умишлено не реализира този production adapter, navigation entry, database migration, deployment или merge.

## Изходна схема

Core резултатът съдържа:

- `timeseries` — `timestamp`, `elapsed_s`, `dt_s`, `raw_hr_bpm`, `clean_hr_bpm`, `smoothed_hr_bpm`, `derivative_bpm_per_s`, `lookahead_hr_bpm`, `provisional_demand_bpm`, `raw_correction_bpm`, `added_correction_bpm`, `removed_correction_bpm`, `hrmod_bpm`, `segment_id`, `episode_id`, `episode_state`, трите zone labels, `quality_flags` и `model_flags`;
- `episode_summary` — граници, статус, lobe/area/capacity и conservation диагностика;
- `zone_summary` — seconds, percent и `hrmod - clean_hr` по сигнал и зона;
- `diagnostics` — quality, gap, derivative, episode, capacity и conservation показатели и флагове;
- пълна сериализируема `config`;
- `hr_input_hash`;
- `model_version = "hrmod_inverse_kinetics_conservative_v1"`.

Reference-aligned timeseries и метрики са отделен резултат и отделен export; никога не се смесват в core hash-а.

## Локално стартиране

От корена на repository:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run hrmod_lab_app.py
```

След това:

1. качете `.tcx` файл с HR;
2. прегледайте quality отчета и observed maximum;
3. задайте HR профила и всички експериментални параметри;
4. натиснете **Изчисли HRmod (само HR)**;
5. прегледайте HR-only графиката, episodes, zones и diagnostics;
6. едва след готов резултат използвайте tab **Reference validation** за overlays/annotations;
7. свалете отделните CSV/JSON файлове или общия ZIP.

## Reference validation без leakage

HRmod се изчислява и hash-ва преди timestamp join. Reference слоят може да показва:

- измерена/оценена power серия, когато произходът и индивидуалните power зони са известни;
- контролирана treadmill speed плюс проверен grade като протоколна референция;
- outdoor running speed само като контекст без валидиран sport/grade модел;
- raw ski speed само като контекст;
- laps и ръчни markers само като post-hoc annotations и summaries.

При изрично зададени external zones могат да се изчисляват confusion matrix, time-in-zone agreement, sensitivity за високите external зони, подобрение спрямо clean HR и lag diagnostics. Корелация сама по себе си не доказва валидност. v1 не auto-fit-ва HRmod параметрите по speed или power.

Anti-leakage инвариантът е: два TCX входа с идентични timestamps и HR, но произволно различни reference стойности, дават numerically identical HRmod и еднакъв `hr_input_hash`. Премахването на всички reference канали също не променя core резултата.

## Diagnostics и флагове

UI и export-ите разграничават HR coverage, sampling regularity, interpolation/artifact fraction, derivative support, complete/incomplete episodes, paired/unpaired площ, capacity ratio, area conservation, edge/gap effects и налична parameter-sensitivity информация.

Минималният flag речник включва:

- `HR_ARTIFACTS_PRESENT`;
- `INTERPOLATED_HR`;
- `LONG_GAP`;
- `INSUFFICIENT_DERIVATIVE_SUPPORT`;
- `INCOMPLETE_EPISODE_START`;
- `INCOMPLETE_EPISODE_END`;
- `UNPAIRED_POSITIVE_AREA`;
- `UNPAIRED_NEGATIVE_AREA`;
- `CAPACITY_LIMITED`;
- `HR_FLOOR_LIMITED`;
- `HRMAX_LIMITED`;
- `AREA_BALANCE_FAILED`;
- `REFERENCE_NOT_SUITABLE_FOR_INTENSITY`;
- `RAW_SKI_SPEED_CONTEXT_ONLY`.

Flag е диагностично наблюдение, не автоматична физиологична интерпретация.

## Експорти и възпроизводимост

HRmod Lab предоставя:

- processed HR-only timeseries CSV;
- episode summary CSV;
- zone summary CSV;
- reference-aligned comparison CSV само при налична reference оценка;
- annotations CSV/JSON при налични annotations;
- `run_configuration.json`;
- `diagnostics.json`;
- ZIP със същите артефакти.

За възпроизводим run пазете TCX provenance извън repository, core `hr_input_hash`, `model_version`, configuration JSON и версията на кода. Не commit-вайте лични TCX файлове.

## Бъдеща калибрация и независима валидация

Defaults не трябва да се оптимизират върху един спортист или да се избират по визуално съвпадение със speed. Предвиденият следващ етап е предварително регистриран лабораторен протокол с отделни calibration и hold-out cohorts, надежден external criterion (например измерена power при подходящ модалитет), повторни активности, uncertainty/measurement-error анализ и предварително определени primary metrics. Трябва да се докладват failure cases, sensitivity към параметри и сравнение с прост baseline, не само correlation.

Калибрацията може да оцени общи и индивидуални `delay_s`, `tau_on_s`, `tau_off_s`, smoothing и episode параметри, но резултатът трябва да се потвърди върху независими данни. Ако `alpha=1` остава недостатъчен, това е доказателство срещу достатъчността на HR-only reconstruction за съответния сценарий, а не разрешение за създаване на HR площ.

## Extension points

- **Second-order/two-component HR kernel:** бъдеща стратегия зад същия HR-only kinetics interface. Тя трябва да запази реалните timestamps, segment boundaries, diagnostics и conservation договора; v1 умишлено остава first-order.
- **Независим power model:** бъдещ модел може да прогнозира или анализира механична power, но трябва да е отделен downstream модул. Той не трябва да се добавя като вход към `compute_hrmod_hr_only`.
- **Sport/grade reference adapters:** могат да подобрят външната оценка, без да променят core резултата.
- **Calibration runner:** може да сравнява предварително зададени HR-only конфигурации върху отделен dataset, без reference auto-fit в интерактивния v1 UI.

## Научни отправни точки

- Zakynthinaki, *Modelling Heart Rate Kinetics*, PLOS ONE (2015), [doi:10.1371/journal.pone.0118263](https://doi.org/10.1371/journal.pone.0118263).
- Spörri et al., *Heart Rate Dynamics Identification and Control in Cycle Ergometer Exercise* (2022), [doi:10.3389/fcteg.2022.894180](https://doi.org/10.3389/fcteg.2022.894180).

Публикациите подкрепят използването на динамични on/off представяния на HR отговора. Те не валидират автоматично конкретното inverse преобразуване, episode detector-а или conservative area balancing в HRmod v1.
