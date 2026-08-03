# onFlows — Intervals.icu Data Inspector (TEST ONLY)

Това е изолиран изследователски Streamlit интерфейс за проверка на данните,
достъпни чрез read-only OAuth grant на един Intervals.icu спортист. Той не
променя основното приложение, не записва в Supabase и не използва webhooks.

## Граници за сигурност

- Използва се само вече регистрираното onFlows OAuth приложение. Не се
  създава нов OAuth клиент.
- Заявяват се единствено `ACTIVITY:READ`, `WELLNESS:READ`,
  `SETTINGS:READ` и `CALENDAR:READ`.
- Access token-ът и временните API данни остават само в паметта на текущата
  Streamlit сесия. Няма файлово, базово или глобално cache съхранение.
- Pending OAuth state се пази между Streamlit сесии само като SHA-256 digest
  в процесната памет, с максимална валидност 10 минути, и се премахва атомарно
  преди еднократната обмяна на authorization code.
- Изпълняват се само документирани GET заявки. Не се изтеглят оригинални
  FIT/TCX/GPX файлове.
- Структурният export съдържа само имена/пътища, типове и статистика за
  покритие. Отделният stream-quality export съдържа само агрегирани counts,
  min/median/max, coverage, относителни продължителности и gap статистика.
  Наличието на GPS/`latlng` stream може да се отчете, но реални координати,
  други стойности, маршрути, бележки и удостоверителни данни се отстраняват.
- „Прекрати връзката“ изчиства цялото състояние на текущата сесия.

## Необходими настройки

Конфигурацията се подава само чрез Streamlit Secrets:

- `INTERVALS_CLIENT_ID`
- `INTERVALS_CLIENT_SECRET`
- `INTERVALS_REDIRECT_URI`
- `OAUTH_STATE_SECRET`
- `INSPECTOR_ACCESS_PASSWORD`

Не записвайте стойностите им в Git, примерни файлове, shell history или
логове. `OAUTH_STATE_SECRET` трябва да е независимо, криптографски случайно
секретно значение. Client ID трябва да е този на съществуващото onFlows
приложение, а не новорегистриран клиент.
За този пилот `INTERVALS_CLIENT_ID` е точно `618`.

При липсваща настройка интерфейсът показва само нейното име.

## Локална проверка

Копирайте безопасния шаблон
`intervals_inspector/.streamlit/secrets.toml.example` като локален
`intervals_inspector/.streamlit/secrets.toml` и попълнете стойностите лично.
Реалният файл е изключен от Git. Задайте като `INTERVALS_REDIRECT_URI`
точния разрешен localhost callback за избрания порт, след което от корена на
хранилището изпълнете например:

```bash
python -m pip install -r intervals_inspector/requirements.txt
python -m streamlit run intervals_inspector/app.py --server.port 8517
```

След вход с паролата на инспектора използвайте „Свържи Intervals.icu“.
Authorization code се обменя веднага след callback и се премахва от URL.

## Отделен Streamlit Community Cloud deployment

Използвайте отделно приложение със следните настройки:

```text
Repository: Dimcho1988/biathlon-program-generator-2
Branch: codex/real-data-shadow-diagnostics
Main file path: intervals_inspector/app.py
```

Не променяйте съществуващото Streamlit приложение. Новият deployment използва
същите пет имена на Secrets. `INTERVALS_CLIENT_ID` и
`INTERVALS_CLIENT_SECRET` са за съществуващия onFlows OAuth client;
`INTERVALS_REDIRECT_URI` трябва да е новият точен canonical URL, а
`OAUTH_STATE_SECRET` е добре да бъде ново независимо случайно значение.
`INSPECTOR_ACCESS_PASSWORD` може да следва същата политика за достъп.

Публичната навигация на отделния pilot deployment има следните стабилни
маршрути, които не изискват паролата на инспектора или OAuth:

```text
About onFlows: https://onflows-pilot.streamlit.app/about
Privacy Policy: https://onflows-pilot.streamlit.app/privacy-policy
```

За регистрацията на съществуващото OAuth приложение в Intervals.icu използвайте
точно втория адрес като Privacy Policy URL. OAuth callback-ът продължава да е
кореновият адрес, зададен чрез `INTERVALS_REDIRECT_URI`.

Преди издаване на OAuth state инспекторът изисква три отделни, неотметнати по
подразбиране потвърждения: прочит и съгласие с политиката, изрично съгласие за
wellness/health-related данни и навършени 18 години. Версията и времето на
потвържденията се свързват само с краткотрайния pending state и след успешен
callback остават в текущата сесия; не се поддържа постоянен consent регистър.

Production Redirect URL не трябва да се предполага предварително. След като
новото приложение бъде deploy-нато и окончателният му canonical HTTPS адрес е
избран:

1. копирайте точния коренов HTTPS URL с крайна `/`;
2. задайте същия низ като `INTERVALS_REDIRECT_URI` в Secrets на новото
   приложение;
3. в Intervals.icu отворете `Settings` → `Manage App` за съществуващото
   onFlows приложение и добавете ръчно абсолютно същия URL;
4. направете нов OAuth grant и проверете callback-а.

При промяна на Streamlit subdomain-а трябва да се актуализират и двете места.

## Официално потвърдени read-only маршрути

- `GET /api/v1/athlete/{id}`
- `GET /api/v1/athlete/{athleteId}/sport-settings`
- `GET /api/v1/athlete/{id}/activities`
- `GET /api/v1/athlete/{id}/wellness`
- `GET /api/v1/athlete/{id}/events`
- `GET /api/v1/athlete/{id}/events?category=WORKOUT`
- `GET /api/v1/activity/{id}?intervals=false`
- `GET /api/v1/activity/{id}/streams.json`

При athlete маршрутите `{id}` е `athlete.id`, върнат при OAuth token
exchange. При activity detail и streams `{id}` е ID на изрично избраната
активност.

Сверено с:

- `https://forum.intervals.icu/t/intervals-icu-oauth-support/2759`
- `https://forum.intervals.icu/t/intervals-icu-api-integration-cookbook/80090`
- `https://intervals.icu/api-docs.html`

Списъкът с активности започва с 7 дни и може да бъде избран за 14, 30, 60 или
90 дни. Само този списък се разширява над 30 дни; wellness, calendar и planned
workout проверките остават ограничени до максимум 30 дни. Detail endpoint-ът и
streams се извикват само on-demand за изрично избрана активност.
Бъдещият shadow-model слой е ограничен до максимум 90 дни и е описан в
`PILOT_ARCHITECTURE_BG.md`; настоящият инспектор не изпълнява модели и не
променя планове.

## Тестове

Тестовете използват само фиктивни стойности и mock HTTP отговори:

```bash
python -m pytest intervals_inspector/tests -q
python -m compileall -q intervals_inspector
```
