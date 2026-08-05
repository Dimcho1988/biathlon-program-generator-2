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
решетка. Те сами по себе си не фиксират moving status, единици или избор между
raw/fixed streams. Следващият диагностичен слой използва консервативните
правила по-долу само за експериментално HR зониране; той не изчислява `T`, `k`,
`Q`, `E`, `7/40`, `Tref`, training load или readiness.

Диагностичният договор за избрана активност е aggregate-only: stream имена и
брой точки, числови min/median/max и coverage, `dt_sec` статистика и
percentiles, gaps, recording-сегментни агрегати, reconciliation на
относителните продължителности и HR/скорост/optional coverage. `dt_sec` е
разликата между два съседни offsets в оригиналния `time` stream; не се свързват
точки през липсващ или невалиден offset.

Установената API структура на `recording_stops` е плосък списък от цели числа
(`List<Integer>`), а не списък от именовани start/end обекти. Семантиката на
отделното число не е достатъчно документирана, затова диагностиката приема
интерпретация само ако всички или част от маркерите могат да се съпоставят с
реални gaps в `time` stream-а. В резултата влизат само брой, статус на
съпоставянето и агрегирани продължителности; самите числа и суровата структура
не се връщат и не се експортират. `icu_recording_time` е доставената от
Intervals.icu продължителност с премахнати recording gaps над 30 секунди; това
описание не се превръща автоматично в правило за нормализация.

`latlng` и всички други location streams се изключват преди анализа и дори
имената и броят им не попадат в stream списъка или export-а. Диагностиката
отчита само общ брой изключени location streams. При липса на експлицитен
надежден `moving` stream статусът остава unavailable и не се извежда от
`velocity_smooth`. Recording сегменти се разделят само по stop gaps, които са
реално съпоставени; не се експортира масив с отделни сегменти.

## Консервативен interval-aware normalizer v1

Първата версия на normalizer-а приема само временен, минимизиран вход:
относителни `time` offsets, разрешени числови stream показатели, безопасно
съпоставени относителни граници на recording stops, агрегатите `elapsed_time`,
`icu_recording_time` и `moving_time`, и незадължителен безопасен sport code.
Преди извикването се изключват GPS/location streams, абсолютни timestamps,
дати, имена, athlete/activity ID, OAuth данни и останалата част от API payload-а.
Входният обект не се променя.

Основният резултат е временна interval-aware структура, а не материализиран
1 Hz поток. Всеки интервал реферира валидираните си крайни точки и съдържа
начален/краен относителен offset, реално `dt_sec`, класификация,
interpolation-eligibility/source, confidence, quality flags и идентификатор на
активния recording сегмент. Подреденият нормален път е линеен. Само при
разместен вход се изпълнява еднократно stable sorting по offset; това е
неизбежният `O(n log n)` fallback.

Предварителната валидация отстранява отрицателни, нечислови, `NaN` и infinite
offsets. Duplicate offsets се обединяват детерминирано: за всеки показател
печели последната валидна стойност в стабилния оригинален входен ред. `NaN`,
infinite, нечислови и структурно невъзможни отрицателни стойности се заменят с
липсваща стойност и се броят по показател. Не се прилагат окончателни
физиологични или sport-specific outlier граници.

Консервативните правила са централизирани във версирана конфигурация:

- `dt = 1 s` → `original_1hz`, без интерполация;
- `1 < dt <= 5 s` → `smart_recording`, разрешена линейна интерполация с
  `interpolated_short`;
- `5 < dt <= 10 s` → `smart_recording` с `interpolated_extended` само при
  валидна положителна speed и в двата края; иначе `uncertain_gap`;
- `10 < dt <= 30 s` → `uncertain_gap`, прекъсва сегмента и не се запълва;
- `dt > 30 s` → `recording_stop` при надеждно съвпадение, `probable_pause` при
  нулева крайна speed или достатъчно duration-reconciliation evidence, иначе
  `technical_or_unexplained_gap`;
- всяко надеждно stop съвпадение прекъсва сегмента независимо от размера на
  `dt` и никога не се интерполира.

Линейната интерполация се извършва само между две валидни крайни стойности.
Липсващ край не се замества, стойности не се пренасят сляпо и няма
extrapolation преди първата или след последната точка. Вътрешните source/quality
flags включват `original`, `interpolated_short`, `interpolated_extended`,
`missing_value` и `invalid_source_value`.

`materialize_1hz` е отделно изрично извикване. То създава само временен изглед
в паметта върху разрешените активни сегменти и не създава точки в stops,
pauses, uncertain или unexplained gaps. При чист подреден 1 Hz поток без
дубликати, невалидни offsets, stops или gaps fast path запазва original
endpoints и изричният 1 Hz изглед ги реферира без resampling или копиране.
Interval structures и 1 Hz точки не се показват, експортират или записват във
файл, база или Supabase. Safe JSON съдържа само агрегатни summaries, включително
отделни времена и приблизителна временна памет за interval-aware и изрично
поискания materialization режим.

## Диагностично interval-aware време по HR зони v1

Общият калкулатор `hr-zone-time-interval-aware-v1` не зависи от
Intervals.icu. Той приема временния `IntervalAwareResult` и външно подаден
произволен брой именувани, подредени и непресичащи се HR диапазони. Границите
имат изрична inclusivity семантика; невалидни, дублирани или пресичащи се зони
се отказват преди изчислението.

Activity-specific adapter-ът поддържа потвърдената структура:
`icu_hr_zones` е подреден числов масив с максималния BPM за всяка зона, а
`icu_hr_zone_times`, когато е наличен и валиден, е масив със секунди в същия
ред. Adapter-ът създава `Z1..Zn` като `(предишен максимум, текущ максимум]`, с
долна граница `0` за Z1. Така HR точно върху максимална граница принадлежи на
по-ниската зона. Непозната структура, липсващ HR stream или липсващи валидни
граници връща ясна unavailable причина, без crash. `icu_hr_zone_times` се
показва само като сравнителна референция и никога не участва в onFlows
изчислението.

Изчислението обхожда само `original_1hz` и `smart_recording` интервалите като
полуотворени времеви диапазони `[start, end)`. При валиден HR в двата края HR
се разглежда като линейна функция и интервалът се разделя точно при всяка
пресечена зонова граница, без вътрешно закръгляне до цели секунди. Ако някой HR
край липсва, не е число, не е положителен или е извън поддържания диапазон,
целият интервал става `unclassified_hr_sec`; няма forward-fill, extrapolation
или измислена зона. `recording_stop`, `probable_pause`, `uncertain_gap` и
`technical_or_unexplained_gap` получават нула зонално време и се отчитат
отделно като изключена продължителност.

Задължителният invariant е:

```text
sum(zone_seconds) + unclassified_hr_sec == active_duration_sec
```

в рамките на малък числов tolerance. Резултатът съдържа само секунди/минути и
процент по зона, classified/unclassified HR време, HR coverage, активно и
изключено време, comparison с Intervals и версията на алгоритъма.
`materialize_1hz()` не е част от този път. Safe JSON съдържа само агрегатния
`zone_analysis`; interval objects, 1 Hz samples, сурови HR точки, GPS, IDs,
абсолютни timestamps и API payload-и не се задържат или експортират.

Това е диагностичен слой, а не окончателният onFlows модел за физиологични
зони. Той не изчислява training load, `T`, `k`, `Q`, `E`, `7/40`, `Tref` или
readiness и не е worker или persistence слой.

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
