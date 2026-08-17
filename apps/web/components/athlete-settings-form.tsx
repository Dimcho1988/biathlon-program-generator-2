const boundaryFields = [
  ["z1_low", "Начало Z1"],
  ["z2_low", "Начало Z2"],
  ["z3_low", "Начало Z3"],
  ["z4_low", "Начало Z4"],
  ["z5_low", "Начало Z5"],
  ["z5_high", "Край Z5"],
] as const;

type AthleteSettingsFormProps = {
  notice?: string;
  initialBounds?: [number, number, number, number, number, number] | null;
  initialTimezone?: string | null;
  editing?: boolean;
};

export function AthleteSettingsForm({
  notice,
  initialBounds = null,
  initialTimezone = null,
  editing = false,
}: AthleteSettingsFormProps) {
  return (
    <main className="state-page settings-page">
      <p className="eyebrow">Индивидуална конфигурация</p>
      <h1>{editing ? "Настройки на профила" : "Настройте пулсовите зони"}</h1>
      <p className="muted">
        {editing
          ? "Променете утвърдените граници или часовата зона. Новите стойности ще се използват при следващото обновяване."
          : "Новият профил е свързан, но не може да наследи границите на друг спортист. Въведете шестте утвърдени граници; приложението няма да ги предполага от една тренировка."}
      </p>
      {notice && <p className="connection-notice">{notice}</p>}
      <form className="athlete-settings-form" action="/api/athlete/settings" method="post">
        <fieldset>
          <legend>HR граници (уд/мин)</legend>
          <div className="boundary-grid">
            {boundaryFields.map(([name, label], index) => (
              <label key={name}>
                <span>{label}</span>
                <input name={name} type="number" min="30" max="240" required inputMode="numeric" defaultValue={initialBounds?.[index]} />
              </label>
            ))}
          </div>
        </fieldset>
        <label className="timezone-field">
          <span>Часова зона</span>
          <input name="timezone" type="text" defaultValue={initialTimezone ?? "Europe/Sofia"} maxLength={64} required autoComplete="off" />
        </label>
        <button className="action-button" type="submit">Запази настройките</button>
      </form>
      <p className="state-help">Версиите на Tref, intra-zone и recovery модела остават общи и одобрени за всички профили.</p>
    </main>
  );
}
