"use client";

import { useState } from "react";
import Link from "next/link";

type Row = Record<string, unknown>;

const number = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const text = (value: unknown): string => value === null || value === undefined ? "—" : String(value);
const fixed = (value: unknown, digits = 1): string => {
  const rendered = number(value);
  return rendered === null ? "—" : rendered.toFixed(digits);
};
const mean = (rows: Row[], key: string): number | null => {
  const values = rows.map((row) => number(row[key])).filter((value): value is number => value !== null);
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
};
const signed = (value: unknown, digits = 1): string => {
  const rendered = number(value);
  if (rendered === null) return "—";
  return `${rendered > 0 ? "+" : ""}${rendered.toFixed(digits)}`;
};
const difference = (left: unknown, right: unknown): number | null => {
  const leftNumber = number(left);
  const rightNumber = number(right);
  return leftNumber === null || rightNumber === null ? null : leftNumber - rightNumber;
};
const duration = (seconds: unknown): string => {
  const value = number(seconds);
  if (value === null) return "—";
  const rounded = Math.max(0, Math.round(value));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remaining = rounded % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`
    : `${minutes}:${String(remaining).padStart(2, "0")}`;
};
const dateTime = (value: unknown): string => {
  if (typeof value !== "string") return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("bg-BG", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "Europe/Sofia",
  }).format(parsed);
};

function MiniPlot({ rows, series, title }: {
  rows: Row[];
  series: { key: string; label: string; color: string; enabled?: boolean }[];
  title: string;
}) {
  const visible = series.filter((item) => item.enabled !== false);
  const values = visible.flatMap((item) => rows.map((row) => number(row[item.key])).filter((value): value is number => value !== null));
  if (!values.length) return <section className="shadow-chart"><h3>{title}</h3><p>Няма наличен канал.</p></section>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1e-9);
  const elapsed = rows.map((row, index) => number(row.elapsed_s) ?? index);
  const minElapsed = Math.min(...elapsed);
  const maxElapsed = Math.max(...elapsed);
  const elapsedSpan = Math.max(maxElapsed - minElapsed, 1);
  const path = (key: string) => {
    const commands: string[] = [];
    let drawing = false;
    let previousElapsed: number | null = null;
    rows.forEach((row, index) => {
      const value = number(row[key]);
      const currentElapsed = elapsed[index];
      if (value === null) {
        drawing = false;
        previousElapsed = null;
        return;
      }
      const x = 42 + ((currentElapsed - minElapsed) / elapsedSpan) * 928;
      const y = 178 - ((value - min) / span) * 154;
      const gap = previousElapsed !== null && currentElapsed - previousElapsed > 10;
      commands.push(`${!drawing || gap ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`);
      drawing = true;
      previousElapsed = currentElapsed;
    });
    return commands.join(" ");
  };
  const zeroY = min < 0 && max > 0 ? 178 - ((0 - min) / span) * 154 : null;
  return (
    <section className="shadow-chart">
      <h3>{title}</h3>
      <svg viewBox="0 0 1000 210" role="img" aria-label={title}>
        {zeroY !== null && <line x1="42" x2="970" y1={zeroY} y2={zeroY} className="shadow-zero-line" />}
        {visible.map((item) => <path key={item.key} d={path(item.key)} fill="none" stroke={item.color} strokeWidth="2.5" />)}
        <text x="4" y="30">{max.toFixed(1)}</text>
        <text x="4" y="181">{min.toFixed(1)}</text>
        <text x="42" y="204">{duration(minElapsed)}</text>
        <text x="970" y="204" textAnchor="end">{duration(maxElapsed)}</text>
      </svg>
      <p className="shadow-legend">{visible.map((item) => <span key={item.key} style={{ color: item.color }}>● {item.label}</span>)}</p>
    </section>
  );
}

function IntervalBands({ rows }: { rows: Row[] }) {
  const elapsed = rows.map((row, index) => number(row.elapsed_s) ?? index);
  const maxElapsed = Math.max(...elapsed, 1);
  const runs = (key: string) => {
    const result: Array<[number, number]> = [];
    let start: number | null = null;
    rows.forEach((row, index) => {
      if (row[key] === true && start === null) start = elapsed[index];
      if (row[key] !== true && start !== null) {
        result.push([start, elapsed[Math.max(0, index - 1)] + 1]);
        start = null;
      }
    });
    if (start !== null) result.push([start, maxElapsed]);
    return result;
  };
  const receiver = runs("receiver_flag");
  const donor = runs("donor_flag");
  return (
    <section className="shadow-chart shadow-intervals">
      <h3>Receiver и donor интервали</h3>
      <svg viewBox="0 0 1000 96" role="img" aria-label="Receiver и donor интервали">
        <text x="4" y="31">Receiver</text><text x="4" y="68">Donor</text>
        <line x1="105" x2="970" y1="26" y2="26" /><line x1="105" x2="970" y1="63" y2="63" />
        {receiver.map(([start, end], index) => <rect key={`r-${index}`} x={105 + start / maxElapsed * 865} y="15" width={Math.max((end - start) / maxElapsed * 865, 2)} height="22" rx="3" fill="#16a34a" />)}
        {donor.map(([start, end], index) => <rect key={`d-${index}`} x={105 + start / maxElapsed * 865} y="52" width={Math.max((end - start) / maxElapsed * 865, 2)} height="22" rx="3" fill="#dc2626" />)}
      </svg>
    </section>
  );
}

export function ShadowActivityPanel({ payload, activityRef }: { payload: Record<string, unknown>; activityRef: string }) {
  const [vflatEnabled, setVflatEnabled] = useState(true);
  const [hrmodEnabled, setHrmodEnabled] = useState(true);
  const rows = Array.isArray(payload.timeseries) ? payload.timeseries as Row[] : [];
  const waves = Array.isArray(payload.hrmod_waves) ? payload.hrmod_waves as Row[] : [];
  const zones = Array.isArray(payload.zone_summary) ? payload.zone_summary as Row[] : [];
  const segments = Array.isArray(payload.segments_15s) ? payload.segments_15s as Row[] : [];
  const diagnostics = (payload.diagnostics && typeof payload.diagnostics === "object") ? payload.diagnostics as Row : {};
  const hrDiagnostics = (diagnostics.hrmod && typeof diagnostics.hrmod === "object") ? diagnostics.hrmod as Row : {};
  const allFlags = Array.from(new Set([
    ...rows.flatMap((row) => [
      ...(Array.isArray(row.quality_flags) ? row.quality_flags : []),
      ...(Array.isArray(row.model_flags) ? row.model_flags : []),
    ].map(String)),
    ...waves.flatMap((wave) => Array.isArray(wave.flags) ? wave.flags.map(String) : []),
  ]));
  const allExclusions = Array.from(new Set(
    rows.map((row) => row.exclusion_reason)
      .filter((value): value is string => typeof value === "string" && value.length > 0),
  ));
  const firstRow = rows[0] ?? {};
  const lastRow = rows.at(-1) ?? {};
  const activityDuration = (number(lastRow.elapsed_s) ?? rows.length - 1) - (number(firstRow.elapsed_s) ?? 0);
  const rawSpeed = mean(rows, "speed_raw_kmh");
  const vflatSpeed = mean(rows, "vflat_b65_kmh");
  const cleanHr = mean(rows, "hr_clean_bpm");
  const finalHr = mean(rows, "hrmod_final_bpm");
  const redistributedZoneSeconds = zones.reduce((sum, zone) => {
    const raw = number(zone.raw_seconds);
    const modulated = number(zone.hrmod_seconds);
    return raw === null || modulated === null ? sum : sum + Math.abs(modulated - raw);
  }, 0) / 2;

  return (
    <div className="shadow-lab">
      <section className="shadow-activity-heading">
        <div>
          <p className="section-kicker">Избрана активност</p>
          <h2>{dateTime(firstRow.timestamp)}</h2>
          <p>{duration(activityDuration)} · {rows.length.toLocaleString("bg-BG")} проби · ref {activityRef.slice(-8).toUpperCase()}</p>
        </div>
        <fieldset className="shadow-toggles">
          <legend>Диагностични канали</legend>
          <label><input type="checkbox" checked={vflatEnabled} onChange={(event) => setVflatEnabled(event.target.checked)} /> Vflat B65</label>
          <label><input type="checkbox" checked={hrmodEnabled} onChange={(event) => setHrmodEnabled(event.target.checked)} /> HRmod v4</label>
        </fieldset>
      </section>

      <section className="shadow-summary" aria-label="Обобщение на сравнението">
        <article><span>Средна скорост</span><strong>{fixed(rawSpeed)} → {vflatEnabled ? fixed(vflatSpeed) : "off"} km/h</strong><small>Δ {vflatEnabled && rawSpeed !== null && vflatSpeed !== null ? signed(vflatSpeed - rawSpeed) : "—"}</small></article>
        <article><span>Среден пулс</span><strong>{fixed(cleanHr)} → {hrmodEnabled ? fixed(finalHr) : "off"} bpm</strong><small>Δ {hrmodEnabled && cleanHr !== null && finalHr !== null ? signed(finalHr - cleanHr) : "—"}</small></article>
        <article><span>Преразпределено по зони</span><strong>{duration(redistributedZoneSeconds)}</strong><small>½ Σ |HRmod − Raw|</small></article>
        <article><span>HR вълни</span><strong>{text(hrDiagnostics.corrected_wave_count ?? 0)} коригирани</strong><small>{waves.length} открити общо</small></article>
        <article><span>Диагностични сигнали</span><strong>{allFlags.length} flags</strong><small>{allExclusions.length} exclusions</small></article>
      </section>

      <div className="shadow-plots">
        <MiniPlot title="Реална скорост ↔ Vflat B65" rows={rows} series={[
          { key: "speed_raw_kmh", label: "Raw speed", color: "#64748b" },
          { key: "vflat_b65_kmh", label: "Vflat B65", color: "#16a34a", enabled: vflatEnabled },
        ]} />
        <MiniPlot title="Raw / clean HR ↔ HRmod candidate / final" rows={rows} series={[
          { key: "hr_raw_bpm", label: "Raw HR", color: "#94a3b8" },
          { key: "hr_clean_bpm", label: "Clean HR", color: "#2563eb" },
          { key: "hrmod_candidate_bpm", label: "Candidate", color: "#d97706", enabled: hrmodEnabled },
          { key: "hrmod_final_bpm", label: "Final", color: "#dc2626", enabled: hrmodEnabled },
        ]} />
        <MiniPlot title="Наклон под времевите графики" rows={rows} series={[
          { key: "grade_raw_pct", label: "Raw/provider grade", color: "#64748b" },
          { key: "grade_smoothed_pct", label: "Smoothed grade", color: "#7c3aed" },
        ]} />
        <IntervalBands rows={rows} />
        <MiniPlot title="Добавен ↔ отнет пулс" rows={rows} series={[
          { key: "added_bpm", label: "Added HR", color: "#16a34a", enabled: hrmodEnabled },
          { key: "removed_bpm", label: "Removed HR", color: "#dc2626", enabled: hrmodEnabled },
        ]} />
      </div>

      <section className="shadow-zone-comparison">
        <div className="section-heading"><div><p className="section-kicker">Z1—Z5</p><h2>Raw ↔ HRmod времена по зони</h2></div><p>Паралелно сравнение; реалните зони остават непроменени</p></div>
        {zones.length === 0 ? <p className="detail-empty">HRmod разпределението още не е изчислено с текущите индивидуални настройки. Проверете <Link href="/?settings=edit">зоните и HRmax</Link>, запазете ги и изберете „Обнови данните“.</p> : <div className="shadow-table-wrap"><table><thead><tr><th>Зона</th><th>Raw HR</th><th>Clean HR</th><th>HRmod final</th><th>Δ спрямо clean</th></tr></thead>
          <tbody>{zones.map((zone) => <tr key={text(zone.zone_name)}><th>{text(zone.zone_name)}</th><td>{duration(zone.raw_seconds)}</td><td>{duration(zone.clean_seconds)}</td><td>{hrmodEnabled ? duration(zone.hrmod_seconds) : "off"}</td><td>{hrmodEnabled && number(zone.hrmod_minus_clean_seconds) !== null ? `${signed(number(zone.hrmod_minus_clean_seconds)! / 60)} мин` : hrmodEnabled ? "—" : "off"}</td></tr>)}</tbody>
        </table></div>}
      </section>

      <details className="shadow-details">
        <summary><span><small>Възпроизводимо сравнение</small>15-секундни сегменти ({segments.length})</span><span className="chevron" aria-hidden="true">⌄</span></summary>
        <div className="shadow-table-wrap shadow-scroll-table"><table><thead><tr><th>Сегмент</th><th>Raw km/h</th><th>Vflat km/h</th><th>Δ speed</th><th>Raw HR</th><th>HRmod HR</th><th>Δ HR</th><th>Наклон</th></tr></thead>
          <tbody>{segments.map((segment) => <tr key={text(segment.segment_index)}><td>{duration(segment.start_elapsed_s)}–{duration(segment.end_elapsed_s)}</td><td>{fixed(segment.speed_raw_kmh)}</td><td>{vflatEnabled ? fixed(segment.vflat_b65_kmh) : "off"}</td><td>{vflatEnabled ? signed(difference(segment.vflat_b65_kmh, segment.speed_raw_kmh)) : "off"}</td><td>{fixed(segment.hr_raw_bpm)}</td><td>{hrmodEnabled ? fixed(segment.hrmod_final_bpm) : "off"}</td><td>{hrmodEnabled ? signed(difference(segment.hrmod_final_bpm, segment.hr_raw_bpm)) : "off"}</td><td>{number(segment.grade_smoothed_pct) === null ? "—" : `${fixed(segment.grade_smoothed_pct)}%`}</td></tr>)}</tbody>
        </table></div>
      </details>

      <details className="shadow-details">
        <summary><span><small>HRmod v4</small>HR вълни, receiver и donor ({waves.length})</span><span className="chevron" aria-hidden="true">⌄</span></summary>
        <div className="shadow-table-wrap shadow-scroll-table"><table><thead><tr>
          <th>ID</th><th>Status</th><th>Receiver</th><th>Donor</th><th>Added area</th><th>Removed area</th><th>Moved area</th><th>Capacity</th><th>Downhill</th><th>Flags / exclusion</th>
        </tr></thead><tbody>{waves.map((wave) => <tr key={text(wave.wave_id)}>
          <td>{text(wave.wave_id)}</td><td>{wave.corrected ? "corrected" : wave.skip_reason ? "skipped" : "incomplete"}</td>
          <td>{duration(wave.rise_start_elapsed_s)}–{duration(wave.peak_elapsed_s)}</td><td>{duration(wave.peak_elapsed_s)}–{duration(wave.tail_end_elapsed_s)}</td>
          <td>{fixed(wave.added_area_bpm_s, 3)}</td><td>{fixed(wave.removed_area_bpm_s, 3)}</td><td>{fixed(wave.moved_area_bpm_s, 3)}</td>
          <td>{wave.capacity_limited ? "limited" : "ok"}</td><td>{duration(wave.receiver_downhill_overlap_s)}</td>
          <td>{text(wave.skip_reason ?? (Array.isArray(wave.flags) ? wave.flags.join(", ") : null))}</td>
        </tr>)}</tbody></table></div>
      </details>

      <details className="shadow-details">
        <summary><span><small>Само за проверка</small>Flags, exclusions и версии</span><span className="chevron" aria-hidden="true">⌄</span></summary>
        <div className="shadow-diagnostics">
          <dl>{[
            ["max_added_bpm", hrDiagnostics.max_added_bpm], ["max_removed_bpm", hrDiagnostics.max_removed_bpm],
            ["fraction_at_hrmax", hrDiagnostics.fraction_at_hrmax], ["receiver_downhill_overlap_s", hrDiagnostics.receiver_downhill_overlap_s],
            ["receiver_downhill_overlap_fraction", hrDiagnostics.receiver_downhill_overlap_fraction],
            ["moved_area_bpm_s", hrDiagnostics.total_moved_area_bpm_s], ["corrected waves", hrDiagnostics.corrected_wave_count],
            ["skipped waves", hrDiagnostics.skipped_wave_count], ["incomplete waves", hrDiagnostics.incomplete_wave_count],
          ].map(([label, value]) => <div key={String(label)}><dt>{text(label)}</dt><dd>{text(value)}</dd></div>)}</dl>
          <p><strong>Vflat:</strong> {text(payload.vflat_model_version)} / {text(payload.vflat_config_version)}</p>
          <p><strong>HRmod:</strong> {text(payload.hrmod_model_version)} / {text(payload.hrmod_config_version)}</p>
          <p><strong>Terrain:</strong> {text(payload.terrain_model_version)}</p>
          <div className="shadow-chip-list">{allFlags.length ? allFlags.map((flag) => <span key={flag}>{flag}</span>) : <span>Няма flags</span>}</div>
          <p><strong>Exclusions:</strong> {allExclusions.length ? allExclusions.join(", ") : "няма"}</p>
          <details><summary>Hashes и пълна JSON диагностика</summary><pre>{JSON.stringify({ hashes: payload.hashes, diagnostics }, null, 2)}</pre></details>
        </div>
      </details>
    </div>
  );
}
