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
- Експортите съдържат само имена/пътища, типове и статистика за покритие.
  Реални стойности, GPS/маршрути, бележки и удостоверителни данни се
  отстраняват.
- „Прекрати връзката“ изчиства цялото състояние на текущата сесия.

## Необходими настройки

Конфигурацията се подава само чрез Streamlit Secrets или едноименни
environment variables:

- `INTERVALS_CLIENT_ID`
- `INTERVALS_CLIENT_SECRET`
- `INTERVALS_REDIRECT_URI`
- `OAUTH_STATE_SECRET`
- `INSPECTOR_ACCESS_PASSWORD`

Не записвайте стойностите им в Git, примерни файлове, shell history или
логове. `OAUTH_STATE_SECRET` трябва да е независимо, криптографски случайно
секретно значение. Client ID трябва да е този на съществуващото onFlows
приложение, а не новорегистриран клиент.

При липсваща настройка интерфейсът показва само нейното име.

## Локална проверка

Разрешеният localhost callback за тази конфигурация е точно:

```text
http://localhost:8501/
```

Задайте същия низ като `INTERVALS_REDIRECT_URI`, след което от корена на
хранилището изпълнете:

```bash
python -m pip install -r intervals_inspector/requirements.txt
python -m streamlit run intervals_inspector/app.py --server.port 8501
```

След вход с паролата на инспектора използвайте „Свържи Intervals.icu“.
Authorization code се обменя веднага след callback и се премахва от URL.

## Отделен Streamlit Community Cloud deployment

Използвайте отделно приложение със следните настройки:

```text
Repository: Dimcho1988/biathlon-program-generator-2
Branch: codex/intervals-oauth-inspector
Main file path: intervals_inspector/app.py
```

Не променяйте съществуващото Streamlit приложение. Добавете петте настройки
по-горе само в Secrets на новия deployment.

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
- `GET /api/v1/activity/{id}/streams.json`

`id` винаги е `athlete.id`, върнат при OAuth token exchange.

Сверено с:

- `https://forum.intervals.icu/t/intervals-icu-oauth-support/2759`
- `https://forum.intervals.icu/t/intervals-icu-api-integration-cookbook/80090`
- `https://intervals.icu/api/v1/docs`

## Тестове

Тестовете използват само фиктивни стойности и mock HTTP отговори:

```bash
python -m pytest intervals_inspector/tests -q
python -m compileall -q intervals_inspector
```
