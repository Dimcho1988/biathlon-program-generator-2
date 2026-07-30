"""Public, configuration-free pages for the onFlows pilot."""

from __future__ import annotations

import streamlit as st


PILOT_PUBLIC_BASE_URL = "https://onflows-pilot.streamlit.app"
ABOUT_URL_PATH = "about"
PRIVACY_URL_PATH = "privacy-policy"
PRIVACY_POLICY_URL = f"{PILOT_PUBLIC_BASE_URL}/{PRIVACY_URL_PATH}"
PRIVACY_POLICY_VERSION = "1.0"
PRIVACY_POLICY_EFFECTIVE_DATE_EN = "30 July 2026"
PRIVACY_POLICY_EFFECTIVE_DATE_BG = "30 юли 2026 г."

COMPANY_NAME_EN = "DataCape Ltd"
COMPANY_NAME_BG = "ДейтаКейп ЕООД"
COMPANY_EIK = "207038991"
COMPANY_ADDRESS_BG = (
    "София 1404, район Витоша, ж.к. „Манастирски ливади“, бл. 60А"
)
COMPANY_EMAIL = "office@datacape.eu"


def render_about() -> None:
    """Render the public About page without reading secrets or session data."""

    st.title("About onFlows")
    st.caption(
        "A developing platform for endurance training analysis and "
        "coach-led planning / Развиваща се платформа за анализ на "
        "тренировките и планиране, водено от треньора"
    )

    english, bulgarian = st.tabs(["English", "Български"])

    with english:
        st.markdown(
            """
            ## A coach-led platform for endurance performance

            onFlows is being developed as a platform for integrating training
            and wellness information into one coherent view. It is designed to
            turn those data into individual analysis and planning support while
            keeping the coach's methodology, judgment and decisions at the
            centre of the process.

            ## What onFlows is designed to support

            - integration of training and wellness data;
            - individual analysis of training load by training zone and within
              each zone;
            - assessment of training stress;
            - zone-specific modelling of recovery and readiness;
            - tracking changes across microcycles and mesocycles;
            - adaptive planning guided by the coach's methodology and
              decisions;
            - reports designed for athletes, coaches and teams; and
            - an initial application in biathlon, with future expansion to
              other endurance sports.

            onFlows is designed as a decision-support platform. It is not
            intended to replace the coach's professional judgment or medical
            advice.

            ## Current pilot status

            The current pilot is a focused validation environment. It:

            - validates the read-only OAuth integration with Intervals.icu;
            - checks the availability and structure of real training and
              wellness data; and
            - prepares their safe connection to the analytical models being
              developed for onFlows.

            The broader platform capabilities described above are being
            developed and are not presented as fully completed features.

            ## Operator

            - **DataCape Ltd / ДейтаКейп ЕООД**
            - UIC (ЕИК): **207038991**
            - Address: **София 1404, район Витоша, ж.к. „Манастирски ливади“,
              бл. 60А**
            - Email: **office@datacape.eu**
            """
        )

    with bulgarian:
        st.markdown(
            """
            ## Платформа, развивана около решенията на треньора

            onFlows се разработва като платформа, която обединява тренировъчни
            и wellness данни в единна картина. Тя е предназначена да превръща
            тези данни в индивидуален анализ и подкрепа за планирането, като
            методиката, професионалната преценка и решенията на треньора остават
            в центъра на процеса.

            ## Какво е предназначена да подпомага платформата onFlows

            - интегриране на тренировъчни и wellness данни;
            - индивидуален анализ на натоварването по тренировъчни зони и вътре
              в самите зони;
            - оценяване на тренировъчния стрес;
            - моделиране на възстановяването и готовността по отделни зони;
            - проследяване на динамиката в микроцикли и мезоцикли;
            - адаптивно планиране според методиката и решенията на треньора;
            - създаване на отчети за спортисти, треньори и отбори; и
            - първоначално приложение в биатлона с бъдещо разширяване към други
              спортове за издръжливост.

            onFlows е замислена като платформа за подпомагане на решенията. Тя
            не е предназначена да замества професионалната преценка на треньора
            или медицински съвет.

            ## Статус на пилотната версия

            Настоящият пилот е фокусирана среда за валидиране. Той:

            - валидира read-only OAuth интеграцията с Intervals.icu;
            - проверява наличността и структурата на реалните тренировъчни и
              wellness данни; и
            - подготвя безопасното им свързване с аналитичните модели, които се
              разработват за onFlows.

            По-широките възможности на платформата, описани по-горе, са в
            процес на разработване и не се представят като напълно завършени
            функции.

            ## Оператор

            - **ДейтаКейп ЕООД / DataCape Ltd**
            - ЕИК: **207038991**
            - Адрес: **София 1404, район Витоша, ж.к. „Манастирски ливади“,
              бл. 60А**
            - Имейл: **office@datacape.eu**
            """
        )

    st.divider()
    st.page_link(
        "views/privacy-policy.py",
        label="Privacy Policy / Политика за поверителност",
        icon="🔐",
    )


def render_privacy_policy() -> None:
    """Render the complete bilingual public privacy policy."""

    st.title("Privacy Policy / Политика за поверителност")
    st.caption(
        f"Version {PRIVACY_POLICY_VERSION} · "
        f"{PRIVACY_POLICY_EFFECTIVE_DATE_EN} / "
        f"{PRIVACY_POLICY_EFFECTIVE_DATE_BG}"
    )
    st.info(
        "This policy covers only the separate onFlows Intervals.icu pilot. / "
        "Тази политика се отнася само за отделния onFlows Intervals.icu пилот."
    )

    english, bulgarian = st.tabs(["English", "Български"])

    with english:
        st.markdown(
            f"""
            ## 1. Controller and contact

            The controller for personal data processed by this onFlows pilot is
            **{COMPANY_NAME_EN} / {COMPANY_NAME_BG}**, UIC (ЕИК)
            **{COMPANY_EIK}**, address **{COMPANY_ADDRESS_BG}**.

            Privacy requests: **{COMPANY_EMAIL}**.

            ## 2. Scope and eligibility

            This policy covers the separate Intervals.icu OAuth pilot hosted at
            **onflows-pilot.streamlit.app**. It does not cover the existing
            production onFlows application. The pilot is available only to
            people aged 18 or over.

            Connecting is voluntary. You may read the public About and Privacy
            pages without the inspector password, without OAuth and without
            providing Intervals.icu data.

            ## 3. Data the pilot may receive

            If you choose to connect, Intervals.icu may provide the following
            data within the read-only scopes you authorize:

            - OAuth and account data: athlete ID, profile name, granted scopes
              and an access token;
            - profile and sport settings: timezone, locale, sport settings,
              heart-rate, power and pace zones;
            - activity data for a selected 7-, 14- or 30-day period, such as
              activity ID, name, type, dates, duration, distance, training
              load, calories, heart rate, power, speed, cadence, elevation,
              perceived exertion and feel;
            - details and streams for one activity you explicitly select,
              including available time/moving, heart-rate, speed, power,
              cadence, altitude, GPS/latitude-longitude and other stream fields;
            - wellness or health-related data made available by Intervals.icu,
              which can include weight, resting heart rate, HRV, sleep,
              soreness, fatigue, stress, mood or motivation, SpO₂, VO₂ max,
              respiration and menstrual-related fields;
            - calendar events and planned workouts.

            Intervals.icu controls which fields are available. The pilot does
            not download your complete history by default or download original
            FIT, TCX or GPX files.

            Streamlit Community Cloud and its operator, Snowflake, may also
            process technical service data such as IP address, browser or
            device metadata, access/security/error logs and usage events under
            their own notices.

            ## 4. Purposes and legal bases

            We process the connected data to:

            - complete OAuth authentication and maintain your current session;
            - test which read-only Intervals.icu endpoints and fields are
              available;
            - create a value-free structural inventory of field names, data
              types, record counts and endpoint status;
            - assess how the available fields could later map to onFlows model
              inputs in a separate shadow-mode implementation; and
            - operate and secure the pilot.

            The basis for ordinary pilot data is your consent under Article
            6(1)(a) GDPR. Health and wellness data are processed only with your
            separate explicit consent under Article 9(2)(a) GDPR.
            Provider-generated technical data are handled under the hosting
            provider's own published privacy notice and legal bases.

            Before an OAuth connection can start, you must actively confirm
            that you have read this policy and consent to the ordinary pilot
            processing, separately give explicit consent for wellness and
            health-related data, and confirm that you are at least 18. The
            boxes are not preselected. Because the pilot has no database, it
            keeps only a short-lived record containing the policy version,
            timestamp and three consent booleans, keyed by a digest of the
            protected OAuth state, and then a record in your current session.
            This record contains no athlete ID, authorization code, access
            token or secret. The pilot does not maintain a permanent consent
            ledger.

            You can refuse or withdraw consent without affecting the lawfulness
            of processing before withdrawal. If you do not consent to the
            wellness processing, do not connect: wellness inspection is an
            essential part of this limited pilot.

            ## 5. Read-only operation and storage

            Apart from the OAuth token exchange POST, the pilot sends only
            read-only GET requests to the documented Intervals.icu API routes.
            It does not write data back to Intervals.icu, activate webhooks,
            write to Supabase or another database, or use application-level
            persistent file storage.

            Raw Intervals.icu response data are processed transiently in the
            Streamlit server memory. The access token, connected profile
            identity, granted scopes, structural reports and a limited list of
            activity IDs/dates/sports remain only in the current Streamlit
            session. Raw stream points, including GPS coordinates, are not
            included in the structural report or displayed by the current
            inspector.

            A downloaded technical report contains structural metadata rather
            than raw values or credentials. Once downloaded, that copy is
            stored on and controlled by your device.

            ## 6. Recipients, hosting and international processing

            Intervals.icu receives the OAuth authorization interaction and
            supplies the data you authorize. Its own processing is governed by
            the [Intervals.icu Privacy Policy](https://www.intervals.icu/privacy-policy/).

            The pilot is hosted on Streamlit Community Cloud, operated by
            Snowflake. Hosting infrastructure can process connection,
            security, diagnostic and usage information. Snowflake states that
            data may be processed in the United States and other countries.
            See [Streamlit Trust and Security](https://docs.streamlit.io/deploy/streamlit-community-cloud/get-started/trust-and-security)
            and the [Snowflake Privacy Notice](https://www.snowflake.com/en/legal/privacy/privacy-policy/).

            The application deliberately does not print OAuth codes, state
            values, client secrets, access tokens, passwords or raw API
            response bodies to its own logs. We cannot promise that the hosting
            provider produces no independent connection, security or
            diagnostic metadata.

            ## 7. Retention

            - A pending OAuth state is valid for at most 10 minutes. Process
              memory contains its SHA-256 digest and issue time plus the policy
              version, confirmation time and three consent booleans. It
              contains no athlete ID, authorization code, token or secret. The
              entry is removed when consumed; after expiry it is physically
              pruned on the next store operation or process restart.
            - The authorization code is exchanged once and the OAuth callback
              parameters are then cleared from the URL. The application does
              not persist the code.
            - Session data remain until you use “Disconnect”, the Streamlit
              session ends, or the application process is restarted or
              redeployed. The code does not define a fixed inactivity period.
            - The Intervals.icu authorization remains at Intervals.icu until
              you revoke it there; clearing the local session alone does not
              revoke the provider-side grant.
            - Streamlit/Snowflake may retain provider-generated technical logs
              and analytics under their published retention criteria. No fixed
              Community Cloud log-retention period has been confirmed, so this
              policy does not invent one.

            ## 8. Disconnecting, withdrawal and deletion

            “Disconnect” clears the access token and all other onFlows pilot
            data from the current Streamlit session. To terminate the OAuth
            authorization at its source, also revoke the onFlows application in
            your Intervals.icu authorized-app settings.

            To withdraw consent or request access, correction or deletion of
            personal data under our control, email **{COMPANY_EMAIL}**. The
            pilot currently has no persistent user database to delete. We will
            assess any relevant records under our control and explain where a
            provider-controlled request must be made directly to the provider.

            ## 9. Your GDPR rights

            Subject to the conditions in the GDPR, you may request information
            and access, rectification, erasure, restriction, data portability
            or object to processing. You may withdraw consent at any time and
            lodge a complaint with the Bulgarian Commission for Personal Data
            Protection (CPDP) or another competent supervisory authority. See
            the [CPDP complaint procedure](https://cpdp.bg/en/lodging-complaints-and-alerts/).

            ## 10. Automated decisions

            The current inspector does not execute the onFlows models, change
            training plans, write results to another service or make solely
            automated decisions with legal or similarly significant effects.
            Any future shadow output will be experimental and advisory.

            ## 11. Changes

            Material changes to the data, purposes, storage or recipients will
            require this policy and, where necessary, the consent flow to be
            updated before the changed processing begins.

            **Version {PRIVACY_POLICY_VERSION} — effective
            {PRIVACY_POLICY_EFFECTIVE_DATE_EN}.**
            """
        )

    with bulgarian:
        st.markdown(
            f"""
            ## 1. Администратор и контакт

            Администратор на личните данни, обработвани от този onFlows пилот,
            е **{COMPANY_NAME_BG} / {COMPANY_NAME_EN}**, ЕИК
            **{COMPANY_EIK}**, адрес **{COMPANY_ADDRESS_BG}**.

            За искания относно поверителността: **{COMPANY_EMAIL}**.

            ## 2. Обхват и допустими потребители

            Политиката се отнася за отделния Intervals.icu OAuth пилот,
            публикуван на **onflows-pilot.streamlit.app**. Тя не се отнася за
            съществуващото production приложение на onFlows. Пилотът е само за
            лица, навършили 18 години.

            Свързването е доброволно. Можете да прочетете публичните страници
            About и Privacy без паролата на инспектора, без OAuth и без да
            предоставяте Intervals.icu данни.

            ## 3. Данни, които пилотът може да получи

            Ако изберете да се свържете, Intervals.icu може да предостави
            следните данни в рамките на разрешените от Вас read-only права:

            - OAuth и профилни данни: athlete ID, име на профила, предоставени
              права и access token;
            - профил и спортни настройки: часова зона, locale, настройки за
              спорт и зони за пулс, мощност и темпо;
            - данни за активности за избран период от 7, 14 или 30 дни, като
              ID, име, тип, дати, продължителност, разстояние, тренировъчно
              натоварване, калории, пулс, мощност, скорост, каданс, денивелация,
              субективно усилие и усещане;
            - детайли и потоци за една изрично избрана активност, включително
              наличните време/движение, пулс, скорост, мощност, каданс,
              височина, GPS/географски координати и други stream полета;
            - wellness или свързани със здравето данни, предоставени от
              Intervals.icu, които могат да включват тегло, пулс в покой, HRV,
              сън, мускулна болезненост, умора, стрес, настроение или
              мотивация, SpO₂, VO₂ max, дишане и полета, свързани с менструален
              цикъл;
            - календарни събития и планирани тренировки.

            Intervals.icu определя кои полета са налични. Пилотът не изтегля
            цялата Ви история по подразбиране и не изтегля оригинални FIT, TCX
            или GPX файлове.

            Streamlit Community Cloud и неговият оператор Snowflake могат да
            обработват и технически данни за услугата, като IP адрес, данни за
            браузър или устройство, логове за достъп/сигурност/грешки и събития
            за използване съгласно собствените им политики.

            ## 4. Цели и правни основания

            Обработваме свързаните данни, за да:

            - завършим OAuth удостоверяването и поддържаме текущата Ви сесия;
            - проверим кои read-only Intervals.icu endpoints и полета са
              достъпни;
            - създадем структурен опис без реални стойности, съдържащ имена на
              полета, типове данни, брой записи и статус на endpoint;
            - оценим как достъпните полета биха могли по-късно да се свържат с
              входовете на onFlows модели в отделна shadow-mode реализация; и
            - експлоатираме и защитаваме пилота.

            Основанието за обикновените данни в пилота е Вашето съгласие по
            член 6, параграф 1, буква „а“ от GDPR. Данните за здраве и wellness
            се обработват само с отделното Ви изрично съгласие по член 9,
            параграф 2, буква „а“ от GDPR. Създадените от хостинг доставчика
            технически данни се обработват съгласно неговата публикувана
            политика за поверителност и правни основания.

            Преди започване на OAuth връзката трябва активно да потвърдите, че
            сте прочели политиката и сте съгласни с обикновеното обработване в
            пилота, отделно да дадете изрично съгласие за wellness и свързани
            със здравето данни и да потвърдите, че сте навършили 18 години.
            Полетата не са предварително отметнати. Тъй като пилотът няма база
            данни, той пази само краткотраен запис с версията на политиката,
            времето и трите булеви потвърждения, свързан чрез digest на
            защитения OAuth state, а след това — запис в текущата Ви сесия.
            Записът не съдържа athlete ID, authorization code, access token или
            secret. Не се поддържа постоянен регистър на съгласията.

            Можете да откажете или оттеглите съгласие, без това да засяга
            законосъобразността на обработването преди оттеглянето. Ако не сте
            съгласни с wellness обработването, не свързвайте профила:
            wellness проверката е съществена част от този ограничен пилот.

            ## 5. Read-only работа и съхранение

            Освен POST заявката за OAuth token exchange, пилотът изпраща само
            read-only GET заявки към документираните Intervals.icu API
            маршрути. Той не записва обратно в Intervals.icu, не активира
            webhooks, не записва в Supabase или друга база данни и не използва
            постоянно файлово съхранение на ниво приложение.

            Суровите Intervals.icu отговори се обработват временно в паметта на
            Streamlit сървъра. Access token-ът, идентичността на свързания
            профил, предоставените права, структурните отчети и ограничен
            списък с ID/дати/спорт на активности остават само в текущата
            Streamlit сесия. Суровите точки от потоците, включително GPS
            координатите, не се включват в структурния отчет и не се показват
            от настоящия инспектор.

            Изтегленият технически отчет съдържа структурни метаданни, а не
            сурови стойности или удостоверителни данни. След изтеглянето копието
            се съхранява на и се контролира от Вашето устройство.

            ## 6. Получатели, хостинг и международно обработване

            Intervals.icu получава OAuth взаимодействието и предоставя данните,
            които разрешите. Неговото собствено обработване се урежда от
            [Intervals.icu Privacy Policy](https://www.intervals.icu/privacy-policy/).

            Пилотът се хоства в Streamlit Community Cloud, управляван от
            Snowflake. Хостинг инфраструктурата може да обработва информация за
            връзката, сигурността, диагностиката и използването. Snowflake
            посочва, че данни могат да се обработват в САЩ и други държави.
            Вижте [Streamlit Trust and Security](https://docs.streamlit.io/deploy/streamlit-community-cloud/get-started/trust-and-security)
            и [Snowflake Privacy Notice](https://www.snowflake.com/en/legal/privacy/privacy-policy/).

            Приложението умишлено не извежда OAuth code, state, client secret,
            access token, пароли или сурови API отговори в собствените си
            логове. Не можем да обещаем, че хостинг доставчикът не създава
            самостоятелни метаданни за връзка, сигурност или диагностика.

            ## 7. Срокове за съхранение

            - Pending OAuth state е валиден максимум 10 минути. Паметта на
              процеса съдържа неговия SHA-256 digest и времето на издаване,
              както и версията на политиката, времето и трите булеви
              потвърждения. Записът не съдържа athlete ID, authorization code,
              token или secret. Той се премахва при използване; след изтичане
              се чисти физически при следваща операция със store-а или при
              restart на процеса.
            - Authorization code се обменя еднократно, след което OAuth
              параметрите се изчистват от URL. Кодът не се запазва постоянно от
              приложението.
            - Данните в сесията остават до натискане на „Прекрати връзката“,
              край на Streamlit сесията или restart/redeploy на приложението.
              Кодът не определя фиксиран срок за неактивност.
            - Intervals.icu разрешението остава при Intervals.icu, докато не го
              отнемете там; изчистването само на локалната сесия не отнема
              разрешението при доставчика.
            - Streamlit/Snowflake може да пази създадени от доставчика технически
              логове и аналитика съгласно публикуваните си критерии за
              съхранение. Не е потвърден фиксиран срок за Community Cloud
              логовете, затова тази политика не измисля такъв.

            ## 8. Прекъсване, оттегляне и изтриване

            „Прекрати връзката“ изчиства access token-а и останалите данни на
            onFlows пилота от текущата Streamlit сесия. За да прекратите OAuth
            разрешението при източника, отнемете и достъпа на onFlows
            приложението в настройките за оторизирани приложения в
            Intervals.icu.

            За да оттеглите съгласие или да поискате достъп, коригиране или
            изтриване на лични данни под наш контрол, пишете на
            **{COMPANY_EMAIL}**. Пилотът в момента няма постоянна потребителска
            база за изтриване. Ще проверим приложимите записи под наш контрол и
            ще обясним кога искане за контролирани от доставчик данни трябва да
            се подаде директно до него.

            ## 9. Вашите права по GDPR

            При условията на GDPR можете да поискате информация и достъп,
            коригиране, изтриване, ограничаване, преносимост или да възразите
            срещу обработването. Можете по всяко време да оттеглите съгласието
            си и да подадете жалба до Комисията за защита на личните данни
            (КЗЛД) или друг компетентен надзорен орган. Вижте
            [процедурата за подаване на жалби до КЗЛД](https://cpdp.bg/en/lodging-complaints-and-alerts/).

            ## 10. Автоматизирани решения

            Настоящият инспектор не изпълнява onFlows моделите, не променя
            тренировъчни планове, не записва резултати в друга услуга и не взема
            изцяло автоматизирани решения с правни или сходни значими последици.
            Всеки бъдещ shadow резултат ще е експериментален и консултативен.

            ## 11. Промени

            Съществени промени в данните, целите, съхранението или получателите
            ще изискват актуализиране на политиката и, когато е необходимо, на
            потока за съгласие преди началото на промененото обработване.

            **Версия {PRIVACY_POLICY_VERSION} — в сила от
            {PRIVACY_POLICY_EFFECTIVE_DATE_BG}.**
            """
        )

    st.divider()
    st.markdown(
        f"**Direct policy URL / Директен адрес:** "
        f"[{PRIVACY_POLICY_URL}]({PRIVACY_POLICY_URL})"
    )
    st.page_link(
        "views/about.py",
        label="About onFlows",
        icon="ℹ️",
    )


__all__ = [
    "ABOUT_URL_PATH",
    "PILOT_PUBLIC_BASE_URL",
    "PRIVACY_POLICY_EFFECTIVE_DATE_BG",
    "PRIVACY_POLICY_EFFECTIVE_DATE_EN",
    "PRIVACY_POLICY_URL",
    "PRIVACY_POLICY_VERSION",
    "PRIVACY_URL_PATH",
    "render_about",
    "render_privacy_policy",
]
