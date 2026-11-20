"use client";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import type { Energy, EnergyDay, EnergyHistory } from "../lib/energy";
const macros = [
  ["carbohydrate", "Въглехидрати", "#159b8d"],
  ["fat", "Мазнини", "#cc8a30"],
  ["protein", "Белтъчини", "#7c78c8"],
] as const;
const n = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : v.toLocaleString("bg-BG", { maximumFractionDigits: d });
const dailyValue = (d: EnergyDay): Energy | null =>
  d.total ?? (d.outside || d.activity.kcal > 0 ? d.accounted : null);
const dailyLabel = (d: EnergyDay, today: string) =>
  d.total ? "Целият ден · моделна оценка" : !dailyValue(d)
    ? "Няма оценка" : d.day === today ? "До момента · частична сума" : "Частична сума";
function MacroTable({
  activity,
  outside,
  total,
  totalLabel,
}: {
  activity: Energy | null;
  outside: Energy | null;
  total: Energy | null;
  totalLabel: string;
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
            ["В активности (оценена част)", activity],
            ["Извън активности", outside],
            [totalLabel, total],
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
          {total && total.kcal > 0 && activity && (
            <tr>
              <th>Активности · дял от показаната сума</th>
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
      strength_met: Number(f.get("strength_met")),
      strength_cho_fraction: Number(f.get("strength_cho_fraction")) / 100,
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
              Силова тренировка · средни MET
              <input name="strength_met" type="number" min="1" max="12" step="0.1"
                required defaultValue={p?.strength_met ?? 3.5} />
              <small>Начално 3,5 MET за сесия с почивките; 6 MET е ориентир за интензивна силова работа. Настройката важи от избраната дата.</small>
            </label>
            <label>
              Въглехидрати при сила · %
              <input name="strength_cho_fraction" type="number" min="0" max="100" step="1"
                required defaultValue={(p?.strength_cho_fraction ?? 0.5) * 100} />
              <small>Допускане за небелтъчната енергия, начално 50%. MET не определя разпределението по субстрати.</small>
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
    data.days.filter((d) => d.day < data.today && dailyValue(d)).at(-1)?.day ?? data.days.at(-1)?.day ?? "",
  );
  const [unit, setUnit] = useState<"kcal" | "g">("kcal");
  const day = data.days.find((d) => d.day === selected) ?? data.days.at(-1);
  const shown = day ? dailyValue(day) : null;
  const activityShown = day && (day.activity.kcal > 0 || (day.outside && !day.activities.length))
    ? day.activity : null;
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
              aria-label={`${d.day}: ${dailyValue(d) ? `${n(quantity(dailyValue(d)!))} ${unit}` : "няма оценка"}, ${dailyLabel(d, data.today)}`}
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
        <div className="energy-table energy-daily-table" tabIndex={0} aria-label="Дневни суми по макронутриенти">
          <table>
            <caption>За всеки ден · kcal и грамове по макронутриенти</caption>
            <thead><tr>
              <th>Дата</th><th>Общо kcal</th>
              {macros.map(([k, l]) => <th key={k}>{l} · g / kcal</th>)}
              <th>Обхват на сумата</th>
            </tr></thead>
            <tbody>{[...data.days].reverse().map((d) => {
              const e = dailyValue(d);
              return <tr key={d.day} className={selected === d.day ? "selected" : undefined}>
                <th scope="row"><button type="button" aria-pressed={selected === d.day}
                  onClick={() => setSelected(d.day)}>{d.day}</button></th>
                <td>{n(e?.kcal)}</td>
                {macros.map(([k]) => <td key={k}>{n(e?.[`${k}_g`], 1)} / {n(e?.[`${k}_kcal`])}</td>)}
                <td>{dailyLabel(d, data.today)}{(d.estimated_activity_seconds ?? 0) > 0 ? " · включва сила по MET" : ""}</td>
              </tr>;
            })}</tbody>
          </table>
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
                <span>{day.total ? "Общ дневен разход" : "Сбор на оценения разход"}</span>
                <strong>
                  {n(shown?.kcal)} <small>kcal</small>
                </strong>
                <p>
                  {day.total ? "Моделна оценка" : shown ? dailyLabel(day, data.today) : "Няма пълна дневна оценка"}
                </p>
              </article>
              <article>
                <span>По време на активности</span>
                <strong>
                  {n(activityShown?.kcal)} <small>kcal</small>
                </strong>
                <p>
                  {day.total
                    ? `${n((100 * day.activity.kcal) / day.total.kcal, 1)}% от деня`
                    : "Сбор на оценените участъци"}{" "}
                  {day.coverage_percent != null && <>· по пулс/скорост: {n(day.coverage_percent, 1)}%</>}
                  {(day.estimated_activity_seconds ?? 0) > 0 && <> · по MET: {n(day.estimated_activity_seconds! / 60, 1)} min</>}
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
                {day.reason} {shown && <>Оценена част: {n(shown.kcal)} kcal. Липсващият разход не е включен в сумата.</>}
              </p>
            )}
            <MacroTable
              activity={activityShown}
              outside={day.outside}
              total={shown}
              totalLabel={dailyLabel(day, data.today)}
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
              Показани са сборове за 5 минути. При пулс/скорост се използват
              оригиналните интервали; при силата средната оценка се разпределя равномерно.
              Процентът по пулс/скорост е дял от времето с приложим модел. Паузите,
              липсващите сигнали и пулсът извън 50–100% HRmax могат да останат без оценка.
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
                      {a.method === "STRENGTH_MET" ? "Силова тренировка" : a.sport} · {n(a.duration_s / 60)} min · {n(a.kcal)} kcal
                      {a.method === "STRENGTH_MET" ? ` · приблизително по ${n(a.met, 1)} MET` : ` · по пулс/скорост: ${n(a.coverage_percent, 1)}%`}
                    </summary>
                    {a.method === "STRENGTH_MET" ? <p>
                      Средна оценка за цялата сесия с почивките, включваща разхода в покой.
                      {a.above_rest_kcal != null && <> Над личния разход в покой за това време: {n(a.above_rest_kcal)} kcal.</>}
                      {" "}Разпределението е допускане: {n(100 * (a.carbohydrate_fraction_assumption ?? 0.5))}% въглехидрати от небелтъчната енергия
                      и {n(100 * (a.protein_fraction_assumption ?? 0.0328), 2)}% белтъчини от общата енергия.
                      Графиката не показва измерена интензивност на сериите.
                    </p> : <p>
                      Измерен пулс:{" "}
                      {n((a.source_seconds?.measured_hr ?? 0) / 60, 1)} min;
                      относителна интензивност:{" "}
                      {n((a.source_seconds?.relative_intensity ?? 0) / 60, 1)}{" "}
                      min. Без оценка: {n((a.unknown_seconds ?? a.duration_s - (a.covered_seconds ?? 0)) / 60, 1)} min.
                      {(a.paused_seconds ?? 0) > 0 && <> От тях паузи в записа: {n(a.paused_seconds! / 60, 1)} min.</>}
                      {(a.uncovered_reasons_seconds?.outside_hr_range ?? 0) > 0 && <> Извън обхвата на регресията: {n(a.uncovered_reasons_seconds!.outside_hr_range / 60, 1)} min.</>}
                    </p>}
                    <p>{macros.map(([k, l]) => `${l}: ${n(a.kcal == null ? null : a[`${k}_g`], 1)} g / ${n(a.kcal == null ? null : a[`${k}_kcal`])} kcal`).join(" · ")}</p>
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
