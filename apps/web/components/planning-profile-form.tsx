import Link from "next/link";
import {
  WEEKDAYS,
  type MesocycleAccentPreferencesResponse,
  type PlanningMethodology,
  type PlanningProfile,
} from "../lib/planning-profile";
import { MesocycleAccentEditor } from "./mesocycle-accent-editor";

function WeekdayChoices({
  name,
  selected = [],
}: {
  name: string;
  selected?: number[];
}) {
  return <div className="weekday-grid">
    {WEEKDAYS.map((label, day) => <label key={label}>
      <input name={name} type="checkbox" value={day} defaultChecked={selected.includes(day)} />
      <span>{label}</span>
    </label>)}
  </div>;
}

function WeekdaySelect({
  name,
  selected,
}: {
  name: string;
  selected?: number;
}) {
  return <select name={name} defaultValue={selected ?? ""} required>
    <option value="" disabled>Изберете ден</option>
    {WEEKDAYS.map((label, day) => <option key={label} value={day}>{label}</option>)}
  </select>;
}

export function PlanningProfileForm({
  profile,
  methodology,
  accentPreferences,
  notice,
}: {
  profile: PlanningProfile | null;
  methodology: PlanningMethodology;
  accentPreferences: MesocycleAccentPreferencesResponse;
  notice?: string;
}) {
  return <main className="state-page settings-page planning-page">
    <p className="eyebrow">Индивидуални входове за планиране</p>
    <h1>Профил за планиране</h1>
    <p className="muted">
      Тук се задават само личните цели и седмичната структура. Общите научни
      коефициенти остават versioned в модела и не се копират в профила.
    </p>
    {notice && <p className="connection-notice">{notice}</p>}
    {!profile && <p className="planning-unconfigured">
      Профилът още не е конфигуриран. Стойностите не се предполагат автоматично.
    </p>}
    <section className="methodology-card" aria-labelledby="methodology-title">
      <div>
        <p className="eyebrow">Вградена методология</p>
        <h2 id="methodology-title">onFlows canonical</h2>
        <p className="muted">
          Общата методика е versioned и не се копира в профила на спортиста.
          Плановият snapshot записва точната версия и източник.
        </p>
      </div>
      <dl>
        <div><dt>Версия</dt><dd>{methodology.methodology_version}</dd></div>
        <div><dt>Източник</dt><dd>Вградена в onFlows</dd></div>
        <div><dt>Базова вълна</dt><dd>{methodology.mesocycle_pattern.map((value) => `${Math.round(value * 100)}%`).join(" · ")}</dd></div>
        <div><dt>Акценти</dt><dd>{methodology.default_accent_limit} стандартно · до {methodology.maximum_accent_limit}</dd></div>
        <div><dt>Режими</dt><dd>{methodology.supported_accent_modes.join(" · ")}</dd></div>
        <div><dt>Стресов мезоцикъл</dt><dd>Проектиран · неактивен до одобрена доза</dd></div>
      </dl>
      <p className="methodology-note">
        HYBRID запазва ръчно избраните компоненти и допълва свободните места
        автоматично. Външна методология по-късно ще се приема само като
        валидиран versioned файл след треньорски преглед, без AI при всяко отваряне.
      </p>
    </section>
    <MesocycleAccentEditor
      response={accentPreferences}
      methodology={methodology}
      profileConfigured={profile !== null}
    />
    <form className="planning-profile-form" action="/api/athlete/planning-profile" method="post">
      <input type="hidden" name="schema_version" value="planning-profile-v1" />

      <fieldset>
        <legend>Сезонна цел</legend>
        <div className="planning-grid three-columns">
          <label><span>Начало на сезона</span><input name="season_start" type="date" defaultValue={profile?.season_start} required /></label>
          <label><span>Край на сезона</span><input name="season_end" type="date" defaultValue={profile?.season_end} required /></label>
          <label><span>Целеви обем · часове</span><input name="annual_target_hours" type="number" min="50" max="1500" step="1" defaultValue={profile?.annual_target_hours} required /></label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Седмична структура</legend>
        <div className="planning-grid three-columns">
          <label><span>Сесии седмично</span><input name="sessions_per_week" type="number" min="1" max="14" step="1" defaultValue={profile?.sessions_per_week} required /></label>
          <label><span>Дълга аеробна тренировка</span><WeekdaySelect name="long_session_day" selected={profile?.long_session_day} /></label>
          <label><span>Максимум ключови сесии</span><input name="max_key_sessions_per_week" type="number" min="0" max="8" step="1" defaultValue={profile?.max_key_sessions_per_week} required /></label>
        </div>
        <div className="planning-choice-group"><h2>Дни за пълна почивка</h2><WeekdayChoices name="rest_days" selected={profile?.rest_days} /></div>
        <div className="planning-choice-group"><h2>Разрешени дни с две сесии</h2><WeekdayChoices name="double_session_days" selected={profile?.double_session_days} /></div>
        <div className="planning-choice-group"><h2>Предпочитани интензивни дни</h2><WeekdayChoices name="intensity_days" selected={profile?.intensity_days} /></div>
        <div className="planning-choice-group"><h2>Предпочитани силови дни</h2><WeekdayChoices name="strength_days" selected={profile?.strength_days} /></div>
      </fieldset>

      <fieldset>
        <legend>Мезоцикъл</legend>
        <div className="planning-grid three-columns">
          <label><span>Опорна дата</span><input name="mesocycle_anchor_date" type="date" defaultValue={profile?.mesocycle_anchor_date} required /></label>
          <label><span>Продължителност · седмици</span><input name="mesocycle_length_weeks" type="number" min="2" max="6" step="1" defaultValue={profile?.mesocycle_length_weeks} required /></label>
          <label><span>Автоматични лагерни акценти</span><input name="camp_default_accent_limit" type="number" min="1" max="6" step="1" defaultValue={profile?.camp_default_accent_limit} required /></label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Двойна прагова тренировка</legend>
        <label className="boolean-choice">
          <input name="double_threshold_enabled" type="checkbox" value="true" defaultChecked={profile?.double_threshold_enabled} />
          <span>Разрешена за този профил</span>
        </label>
        <div className="planning-grid two-columns">
          <label><span>Предпочитан ден</span><WeekdaySelect name="double_threshold_day" selected={profile?.double_threshold_day} /></label>
          <div className="planning-choice-group compact">
            <h2>Компоненти</h2>
            <div className="component-choices">
              {(["Z3", "Z4"] as const).map((component) => <label key={component}>
                <input name="double_threshold_components" type="checkbox" value={component} defaultChecked={profile?.double_threshold_components.includes(component)} />
                <span>{component}</span>
              </label>)}
            </div>
          </div>
        </div>
      </fieldset>

      <button className="action-button" type="submit">Запази профила за планиране</button>
    </form>
    <p className="state-help">
      Записът не генерира програма самостоятелно и не променя текущия анализ.
    </p>
    <Link className="text-action" href="/">Към тренировъчния анализ</Link>
  </main>;
}
