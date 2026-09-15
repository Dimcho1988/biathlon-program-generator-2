import Link from "next/link";
import type { CSSProperties } from "react";
import type { LoadHistory } from "../lib/load-history";
import type { RecoveryHistory } from "../lib/recovery-history";
import type { TrainingStatus } from "../lib/training-status";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(`${value}T12:00:00Z`));

export function StatusOverview({ data, recovery, load }: { data: TrainingStatus; recovery: RecoveryHistory | null; load: LoadHistory | null }) {
  const v2 = recovery?.schema_version === "recovery-history-v2" ? recovery : null;
  const threshold = v2?.ready_threshold_percent ?? (recovery?.schema_version === "recovery-history-v1" ? recovery.model.practical_full_recovery_percent : null);
  const zones = data.zones.map((zone) => ({
    zone: zone.zone as string,
    readiness: recovery?.current.find((row) => row.zone === zone.zone)?.readiness_percent ?? zone.recovery_readiness_percent,
    ratio: load?.zones.find((row) => row.zone === zone.zone)?.status_7_40 ?? zone.status_7_40,
  }));
  const strengthReadiness = v2?.current.find((row) => row.zone === "STR")?.readiness_percent
    ?? (recovery?.schema_version === "recovery-history-v1" ? recovery.strength?.current.readiness_percent : undefined);
  if (strengthReadiness !== undefined && load?.strength) zones.push({ zone: "STR", readiness: strengthReadiness, ratio: load.strength.summary.status_7_40 });
  const periodEnd = load?.period_end ?? data.as_of;
  const from = new Date(`${periodEnd}T12:00:00Z`); from.setUTCDate(from.getUTCDate() - 6);
  const periodStart = from.toISOString().slice(0, 10);
  const recent = load?.activities.filter((row) => row.date >= periodStart && row.date <= periodEnd);
  const duration = recent?.reduce((total, row) => total + (row.duration_min ?? 0), 0);
  const missingDuration = recent?.some((row) => row.duration_min === null);

  return <div className="status-overview">
    <section className="overview-readiness" aria-labelledby="overview-readiness-title">
      <div className="section-heading"><div><p className="section-kicker">Товарна готовност</p><h2 id="overview-readiness-title">Възстановяване по зони</h2></div><Link href="/?view=recovery" prefetch={false}>Разгледай →</Link></div>
      <p className="overview-explanation">{threshold === null ? "Готовност според текущия модел." : `Праг за готовност ${number.format(threshold)}%.`} Всяка зона се оценява отделно.</p>
      <div className="readiness-grid">{zones.map(({ zone, readiness, ratio }) => <article key={zone} style={{ "--zone": zone === "STR" ? "var(--strength)" : `var(--zone-${zone.slice(1)})` } as CSSProperties}>
        <span className="readiness-zone">{zone}</span><strong>{number.format(readiness)}<small>%</small></strong>
        <div className="readiness-bar" aria-hidden="true"><span style={{ width: `${Math.max(0, Math.min(100, readiness))}%` }} /></div>
        <span className="readiness-label">{threshold === null ? "готовност" : readiness >= threshold ? "достигнат праг" : "възстановява се"}</span>
        <div className="readiness-ratio"><span>7/40</span><b>{number.format(ratio)}</b></div>
      </article>)}</div>
      <p className="overview-date">{v2 ? `Готовност към ${date(v2.as_of)}` : `Анализ към ${date(data.as_of)}`} · Тренировъчни данни до {date(periodEnd)}.{v2?.source_stale && " Прогнозата допуска, че след това няма ново натоварване."}</p>
    </section>
    <div className="overview-shortcuts">
      <Link href="/?view=load" className="overview-card" prefetch={false}><span>Последни 7 дни с данни</span><strong>{duration === undefined ? "Няма данни" : `${number.format(duration / 60)} ч`}</strong><p>{recent ? `${recent.length} активности · ${date(periodStart)} – ${date(periodEnd)}` : "Историята още не е налична."}{missingDuration ? " · Непълен обем" : ""}</p><small>Натоварване и обем <span aria-hidden="true">↗</span></small></Link>
      <Link href="/trainability" className="overview-card" prefetch={false}><span>Индекс на тренираност</span><strong>Пулс и скорост</strong><p>Проследи тенденцията по спорт и зони.</p><small>Отвори индекса <span aria-hidden="true">↗</span></small></Link>
      <Link href="/response" className="overview-card" prefetch={false}><span>Самочувствие</span><strong>Самочувствие и стрес</strong><p>Лични оценки и реакция към натоварването.</p><small>Стрес и възстановяване <span aria-hidden="true">↗</span></small></Link>
    </div>
  </div>;
}
