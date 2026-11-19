"use client";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import type { Energy, EnergyHistory } from "../lib/energy";
const macros = [
  ["carbohydrate", "Въглехидрати", "#159b8d"],
  ["fat", "Мазнини", "#cc8a30"],
  ["protein", "Белтъчини", "#7c78c8"],
] as const;
const n = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : v.toLocaleString("bg-BG", { maximumFractionDigits: d });
function MacroTable({
  activity,
  outside,
  total,
}: {
  activity: Energy;
  outside: Energy | null;
  total: Energy | null;
}) {
  return (
    <div className="energy-table">
      <table>
        <caption>Енергийни еквиваленти на окислените субстрати</caption>
        <thead>
          <tr>
            <th>Източник</th>
            <th>Общо kcal</th>
            {macros.map(([k, l]) => (
              <th key={k}>{l} · g / kcal</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[
            ["В активности (покрити участъци)", activity],
            ["Извън активности", outside],
            ["Целият ден", total],
          ].map(([label, e]) => (
            <tr key={String(label)}>
              <th>{String(label)}</th>
              <td>{n((e as Energy | null)?.kcal)}</td>
              {macros.map(([k]) => (
                <td key={k}>
                  {n((e as Energy | null)?.[`${k}_g`], 1)} /{" "}
                  {n((e as Energy | null)?.[`${k}_kcal`])}
                </td>
              ))}
            </tr>
          ))}
          {total && (
            <tr>
              <th>Дял от активности</th>
              <td>{n((100 * activity.kcal) / total.kcal, 1)}%</td>
              {macros.map(([k]) => (
                <td key={k}>
                  {total[`${k}_kcal`] > 0
                    ? `${n((100 * activity[`${k}_kcal`]) / total[`${k}_kcal`], 1)}%`
                    : "—"}
                </td>
              ))}
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
function ProfileForm({
  data,
  canEdit,
}: {
  data: EnergyHistory;
  canEdit: boolean;
}) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const p = data.profile;
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const effective = String(f.get("effective_from"));
    const payload = {
      effective_from: effective,
      sex: f.get("sex"),
      weight_kg: Number(f.get("weight_kg")),
      height_cm: f.get("height_cm") ? Number(f.get("height_cm")) : null,
      birth_year: Number(f.get("birth_year")),
      resting_kcal: f.get("resting_kcal")
        ? Number(f.get("resting_kcal"))
        : null,
      outside_factor: Number(f.get("outside_factor")),
      outside_cho_fraction: Number(f.get("outside_cho_fraction")) / 100,
      preferences: String(f.get("preferences") || ""),
      allergies: String(f.get("allergies") || ""),
      expected_revision:
        data.profiles.find((r) => r.effective_from === effective)?.revision ??
        0,
    };
    setBusy(true);
    setMessage("");
    try {
      const r = await fetch("/api/athlete/energy/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const v = await r.json();
      if (!r.ok || !v.saved)
        throw new Error(v.error || "Записът не е потвърден.");
      setMessage(
        "Настройките са записани. Отчетът се преизчислява във фонов режим.",
      );
      router.refresh();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Записът не успя.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="energy-panel" open={!p}>
      <summary>
        Лични данни и допускания за оценката
        {!p ? " · необходимо попълване" : ""}
      </summary>
      <p>
        Началната дата определя от кога се прилагат стойностите. По-новите
        измервания на тегло от дневника се използват за съответните дни.
      </p>
      <form onSubmit={save} className="energy-form">
        <fieldset disabled={!canEdit || busy}>
          <div className="energy-fields">
            <label>
              В сила от
              <input
                name="effective_from"
                type="date"
                max={data.today}
                required
                defaultValue={p?.effective_from ?? data.period_start}
              />
            </label>
            <label>
              Регресионна група
              <select name="sex" required defaultValue={p?.sex ?? ""}>
                <option value="" disabled>
                  Изберете
                </option>
                <option value="MALE">Мъже</option>
                <option value="FEMALE">Жени</option>
              </select>
            </label>
            <label>
              Тегло · kg
              <input
                name="weight_kg"
                type="number"
                min="21"
                max="250"
                step="0.1"
                required
                defaultValue={p?.weight_kg}
              />
            </label>
            <label>
              Година на раждане
              <input
                name="birth_year"
                type="number"
                min="1900"
                max={Number(data.today.slice(0, 4))}
                required
                defaultValue={p?.birth_year}
              />
            </label>
            <label>
              Ръст · cm
              <input
                name="height_cm"
                type="number"
                min="100"
                max="230"
                step="0.1"
                defaultValue={p?.height_cm ?? ""}
              />
            </label>
            <label>
              Разход в покой · kcal/24 h
              <input
                name="resting_kcal"
                type="number"
                min="500"
                max="4000"
                defaultValue={p?.resting_kcal ?? ""}
              />
              <small>
                По избор. Без стойност се оценява по Mifflin–St Jeor при наличен
                ръст и възраст ≥18 г.
              </small>
            </label>
            <label>
              Фактор извън тренировки
              <input
                name="outside_factor"
                type="number"
                min="1"
                max="1.8"
                step="0.05"
                required
                defaultValue={p?.outside_factor ?? 1.2}
              />
              <small>
                1,0: само покой; 1,2: начален ориентир за спокойно ежедневие.
                Включва обичайното движение и термичния ефект на храната.
              </small>
            </label>
            <label>
              Въглехидрати извън активности · %
              <input
                name="outside_cho_fraction"
                type="number"
                min="0"
                max="100"
                step="1"
                required
                defaultValue={(p?.outside_cho_fraction ?? 0.5) * 100}
              />
              <small>
                Допускане за небелтъчната енергия; не е измерена стойност.
              </small>
            </label>
            <label>
              Алергии и непоносимости
              <textarea
                name="allergies"
                maxLength={1000}
                defaultValue={p?.allergies ?? ""}
                placeholder="Посочете и ако няма известни."
              />
            </label>
            <label>
              Хранителни предпочитания
              <textarea
                name="preferences"
                maxLength={1000}
                defaultValue={p?.preferences ?? ""}
                placeholder="Предпочитани/изключени храни, брой хранения…"
              />
            </label>
          </div>
          {canEdit && (
            <button className="action-button" disabled={busy}>
              {busy ? "Записване…" : "Запази настройките"}
            </button>
          )}
        </fieldset>
        <p role="status">{message}</p>
      </form>
    </details>
  );
}
export function EnergyDashboard({
  data,
  canEdit,
}: {
  data: EnergyHistory;
  canEdit: boolean;
}) {
  const [selected, setSelected] = useState(
    data.days.filter((d) => d.total).at(-1)?.day ?? data.days.at(-1)?.day ?? "",
  );
  const [unit, setUnit] = useState<"kcal" | "g">("kcal");
  const day = data.days.find((d) => d.day === selected) ?? data.days.at(-1);
  const quantity = (e: Energy) =>
    unit === "kcal" ? e.kcal : macros.reduce((v, [k]) => v + e[`${k}_g`], 0);
  const max = Math.max(
    1,
    ...data.days.map((d) => quantity(d.total ?? d.accounted)),
  );
  const prompt = day
    ? `Прочети енергийния отчет за ${day.day}. На база на хранителните цели и предпочитанията ми предложи конкретно меню с грамажи и сбор на kcal, въглехидрати, белтъчини и мазнини. Провери алергиите и обясни непълните данни.`
    : "";
  return (
    <>
      <ProfileForm data={data} canEdit={canEdit} />
      <section className="energy-panel">
        <form method="get" className="energy-period">
          <label>
            От
            <input
              type="date"
              name="start"
              required
              defaultValue={data.period_start}
            />
          </label>
          <label>
            До
            <input
              type="date"
              name="end"
              max={data.today}
              required
              defaultValue={data.period_end}
            />
          </label>
          <button className="action-button secondary">Покажи</button>
        </form>
        <div className="energy-heading">
          <h2>Дневна динамика</h2>
          <label>
            Мярка
            <select
              value={unit}
              onChange={(e) => setUnit(e.target.value as "kcal" | "g")}
            >
              <option value="kcal">kcal</option>
              <option value="g">Грамове</option>
            </select>
          </label>
        </div>
        <p>
          Плътните колони са пълни моделни оценки. Щрихованите показват само
          отчетената част; липсващ разход не означава нула.
        </p>
        <div
          className="energy-bars"
          role="group"
          aria-label="Избери ден от графиката"
        >
          {data.days.map((d) => (
            <button
              key={d.day}
              type="button"
              className={`energy-daybar ${d.total ? "" : "partial"} ${selected === d.day ? "selected" : ""}`}
              aria-label={`${d.day}: ${n(quantity(d.total ?? d.accounted))} ${unit}, ${d.total ? "моделна оценка" : "частични данни"}`}
              aria-pressed={selected === d.day}
              onClick={() => setSelected(d.day)}
            >
              <span
                className="energy-stack"
                style={{
                  height: `${Math.max(2, (160 * quantity(d.total ?? d.accounted)) / max)}px`,
                }}
              >
                {macros.map(([k, , color]) => (
                  <i
                    key={k}
                    style={{
                      background: color,
                      flex: (d.total ?? d.accounted)[`${k}_${unit}`],
                    }}
                  />
                ))}
              </span>
              <small>{d.day.slice(8)}</small>
            </button>
          ))}
        </div>
        <div className="energy-legend">
          {macros.map(([k, l, c]) => (
            <span key={k}>
              <i style={{ background: c }} />
              {l}
            </span>
          ))}
        </div>
      </section>
      {day && (
        <>
          <section className="energy-panel">
            <div className="energy-heading">
              <h2>{day.day}</h2>
              <label>
                Избери ден
                <select
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  {data.days.map((d) => (
                    <option key={d.day}>{d.day}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="energy-cards">
              <article>
                <span>Общ дневен разход</span>
                <strong>
                  {n(day.total?.kcal)} <small>kcal</small>
                </strong>
                <p>
                  {day.total ? "Моделна оценка" : "Няма пълна дневна оценка"}
                </p>
              </article>
              <article>
                <span>По време на активности</span>
                <strong>
                  {n(day.activity.kcal)} <small>kcal</small>
                </strong>
                <p>
                  {day.total
                    ? `${n((100 * day.activity.kcal) / day.total.kcal, 1)}% от деня`
                    : "Само покритите участъци"}{" "}
                  · покритие {n(day.coverage_percent, 1)}%
                </p>
              </article>
              <article>
                <span>Извън активности</span>
                <strong>
                  {n(day.outside?.kcal)} <small>kcal</small>
                </strong>
                <p>
                  {day.total && day.outside
                    ? `${n((100 * day.outside.kcal) / day.total.kcal, 1)}% от деня`
                    : "Оценка по отделни допускания"}
                </p>
              </article>
            </div>
            {day.reason && (
              <p className="energy-notice">
                {day.reason} Отчетена част: {n(day.accounted.kcal)} kcal.
              </p>
            )}
            <MacroTable
              activity={day.activity}
              outside={day.outside}
              total={day.total}
            />
            <p className="energy-caption">
              Това е изчислен разход. Белтъчният дял и разпределението извън
              тренировките са приблизителни. Грамовете не са препоръка за прием.
            </p>
          </section>
          <section className="energy-panel">
            <div className="energy-heading">
              <h2>Участъци от активностите</h2>
              <label>
                Мярка
                <select
                  value={unit}
                  onChange={(e) => setUnit(e.target.value as "kcal" | "g")}
                >
                  <option value="kcal">kcal / участък</option>
                  <option value="g">g / участък</option>
                </select>
              </label>
            </div>
            <p>
              Показани са сборове за 5 минути; изчислението използва
              оригиналните интервали на записа.
            </p>
            {day.activities.length === 0 ? (
              <p>Няма записани активности за деня.</p>
            ) : (
              day.activities.map((a) => {
                const m = Math.max(
                  1,
                  ...a.timeline.map((t) =>
                    macros.reduce((s, [k]) => s + t[`${k}_${unit}`], 0),
                  ),
                );
                return (
                  <details key={a.activity_ref}>
                    <summary>
                      {a.sport} · {n(a.duration_s / 60)} min · {n(a.kcal)} kcal
                      · покритие {n(a.coverage_percent, 1)}%
                    </summary>
                    <p>
                      Измерен пулс:{" "}
                      {n((a.source_seconds?.measured_hr ?? 0) / 60, 1)} min;
                      относителна интензивност:{" "}
                      {n((a.source_seconds?.relative_intensity ?? 0) / 60, 1)}{" "}
                      min.
                    </p>
                    <div className="energy-bars energy-segments">
                      {a.timeline.map((t) => (
                        <span
                          key={t.start_s}
                          className="energy-segment"
                          title={`${n(t.start_s / 60)} min: ${macros.map(([k, l]) => `${l} ${n(t[`${k}_${unit}`], 1)} ${unit}`).join(", ")}`}
                        >
                          <span
                            className="energy-stack"
                            style={{
                              height: `${(130 * macros.reduce((s, [k]) => s + t[`${k}_${unit}`], 0)) / m}px`,
                            }}
                          >
                            {macros.map(([k, , c]) => (
                              <i
                                key={k}
                                style={{
                                  background: c,
                                  flex: t[`${k}_${unit}`],
                                }}
                              />
                            ))}
                          </span>
                          <small>{n(t.start_s / 60)}</small>
                        </span>
                      ))}
                    </div>
                    <div className="energy-table">
                      <table>
                        <thead>
                          <tr>
                            <th>Минути</th>
                            <th>Общо kcal</th>
                            {macros.map(([k, l]) => (
                              <th key={k}>
                                {l} · {unit}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {a.timeline.map((t) => (
                            <tr key={t.start_s}>
                              <td>
                                {n(t.start_s / 60)}–{n((t.start_s + 300) / 60)}
                              </td>
                              <td>{n(t.kcal)}</td>
                              {macros.map(([k]) => (
                                <td key={k}>{n(t[`${k}_${unit}`], 1)}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                );
              })
            )}
          </section>
          <section className="energy-panel">
            <h2>Хранителни цели и меню</h2>
            {day.targets ? (
              <>
                <div className="energy-cards">
                  <article>
                    <span>Ориентир за прием</span>
                    <strong>
                      {n(day.targets.kcal)} <small>kcal</small>
                    </strong>
                  </article>
                  {macros.map(([k, l]) => (
                    <article key={k}>
                      <span>{l}</span>
                      <strong>
                        {n(day.targets![`${k}_g`])} <small>g</small>
                      </strong>
                    </article>
                  ))}
                </div>
                <p>{day.targets.allocation_note}</p>
              </>
            ) : (
              <p>
                За количествена дневна цел са необходими завършен ден, лични
                данни и достатъчно покритие. Непълният отчет остава достъпен за
                обсъждане с асистента.
              </p>
            )}
            <Link
              className="action-button"
              href={`/assistant?energy_date=${encodeURIComponent(day.day)}`}
            >
              Обсъди меню с ИИ
            </Link>
            <p className="energy-caption">
              Асистентът прочита същия отчет, проверява алергиите и
              предпочитанията и предлага храни и грамажи. Предложеното меню не
              се записва като изядена храна.
            </p>
            <details>
              <summary>Какво изпращаме към асистента</summary>
              <p>{prompt}</p>
            </details>
          </section>
        </>
      )}
      <details className="energy-panel">
        <summary>Методика и ограничения</summary>
        <ul>
          {data.assumptions.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
        <p>
          Кубичната регресия е проверена вътрешно върху 24 спортисти. Няма
          независима валидация на целия дневен модел.
        </p>
        <ul>
          {data.references.map((h) => (
            <li key={h}>
              <a href={h} target="_blank" rel="noreferrer">
                {h}
              </a>
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}
