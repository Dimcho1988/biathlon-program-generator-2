"use client";

import { useState } from "react";

type Row = Record<string, unknown>;

const number = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const text = (value: unknown): string => value === null || value === undefined ? "—" : String(value);
const fixed = (value: unknown, digits = 1): string => {
  const rendered = number(value);
  return rendered === null ? "—" : rendered.toFixed(digits);
};

function MiniPlot({ rows, series, title }: {
  rows: Row[];
  series: { key: string; label: string; color: string; enabled?: boolean }[];
  title: string;
}) {
  const visible = series.filter((item) => item.enabled !== false);
  const values = visible.flatMap((item) => rows.map((row) => number(row[item.key])).filter((value): value is number => value !== null));
  if (!values.length) return <section><h3>{title}</h3><p>Няма наличен канал.</p></section>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1e-9);
  const path = (key: string) => rows.map((row, index) => {
    const value = number(row[key]);
    if (value === null) return null;
    const x = rows.length <= 1 ? 0 : index * 1000 / (rows.length - 1);
    const y = 180 - ((value - min) / span) * 170;
    return [x, y] as const;
  }).filter((point): point is readonly [number, number] => point !== null)
    .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`)
    .join(" ");
  return (
    <section>
      <h3>{title}</h3>
      <svg viewBox="0 0 1000 190" role="img" aria-label={title} style={{ width: "100%", minHeight: 180, background: "rgba(127,127,127,.08)" }}>
        {visible.map((item) => <path key={item.key} d={path(item.key)} fill="none" stroke={item.color} strokeWidth="3" />)}
      </svg>
      <p>{visible.map((item) => <span key={item.key} style={{ color: item.color, marginRight: 16 }}>● {item.label}</span>)}</p>
    </section>
  );
}

export function ShadowActivityPanel({ payload }: { payload: Record<string, unknown> }) {
  const [vflatEnabled, setVflatEnabled] = useState(true);
  const [hrmodEnabled, setHrmodEnabled] = useState(true);
  const rows = Array.isArray(payload.timeseries) ? payload.timeseries as Row[] : [];
  const waves = Array.isArray(payload.hrmod_waves) ? payload.hrmod_waves as Row[] : [];
  const zones = Array.isArray(payload.zone_summary) ? payload.zone_summary as Row[] : [];
  const segments = Array.isArray(payload.segments_15s) ? payload.segments_15s as Row[] : [];
  const diagnostics = (payload.diagnostics && typeof payload.diagnostics === "object") ? payload.diagnostics as Row : {};
  const hrDiagnostics = (diagnostics.hrmod && typeof diagnostics.hrmod === "object") ? diagnostics.hrmod as Row : {};
  const allFlags = Array.from(new Set(rows.flatMap((row) => [
    ...(Array.isArray(row.quality_flags) ? row.quality_flags : []),
    ...(Array.isArray(row.model_flags) ? row.model_flags : []),
  ].map(String))));

  return (
    <div>
      <fieldset style={{ display: "flex", gap: 24 }}>
        <legend>Независими диагностични канали</legend>
        <label><input type="checkbox" checked={vflatEnabled} onChange={(event) => setVflatEnabled(event.target.checked)} /> Vflat B65</label>
        <label><input type="checkbox" checked={hrmodEnabled} onChange={(event) => setHrmodEnabled(event.target.checked)} /> HRmod v4</label>
      </fieldset>

      <MiniPlot title="Реална скорост и Vflat B65" rows={rows} series={[
        { key: "speed_raw_kmh", label: "Raw speed", color: "#64748b" },
        { key: "vflat_b65_kmh", label: "Vflat B65", color: "#16a34a", enabled: vflatEnabled },
      ]} />
      <MiniPlot title="Raw HR, clean HR, HRmod candidate и final" rows={rows} series={[
        { key: "hr_raw_bpm", label: "Raw HR", color: "#94a3b8" },
        { key: "hr_clean_bpm", label: "Clean HR", color: "#2563eb" },
        { key: "hrmod_candidate_bpm", label: "Candidate", color: "#d97706", enabled: hrmodEnabled },
        { key: "hrmod_final_bpm", label: "Final", color: "#dc2626", enabled: hrmodEnabled },
      ]} />
      <MiniPlot title="Наклон" rows={rows} series={[
        { key: "grade_raw_pct", label: "Raw/provider grade", color: "#64748b" },
        { key: "grade_smoothed_pct", label: "Smoothed grade", color: "#7c3aed" },
      ]} />
      <MiniPlot title="Добавен и отнет пулс" rows={rows} series={[
        { key: "added_bpm", label: "Added HR", color: "#16a34a", enabled: hrmodEnabled },
        { key: "removed_bpm", label: "Removed HR", color: "#dc2626", enabled: hrmodEnabled },
      ]} />

      <h2>Диагностика</h2>
      <table><tbody>
        {[
          ["max_added_bpm", hrDiagnostics.max_added_bpm], ["max_removed_bpm", hrDiagnostics.max_removed_bpm],
          ["fraction_at_hrmax", hrDiagnostics.fraction_at_hrmax], ["receiver_downhill_overlap_s", hrDiagnostics.receiver_downhill_overlap_s],
          ["receiver_downhill_overlap_fraction", hrDiagnostics.receiver_downhill_overlap_fraction],
          ["moved_area_bpm_s", hrDiagnostics.total_moved_area_bpm_s], ["corrected waves", hrDiagnostics.corrected_wave_count],
          ["skipped waves", hrDiagnostics.skipped_wave_count], ["incomplete waves", hrDiagnostics.incomplete_wave_count],
        ].map(([label, value]) => <tr key={String(label)}><th>{text(label)}</th><td>{text(value)}</td></tr>)}
      </tbody></table>

      <h2>HR вълни, receiver и donor</h2>
      <div style={{ overflowX: "auto" }}><table><thead><tr>
        <th>ID</th><th>Status</th><th>Receiver</th><th>Donor</th><th>Added area</th><th>Removed area</th><th>Moved area</th><th>Capacity limited</th><th>Downhill overlap</th><th>Flags / exclusion</th>
      </tr></thead><tbody>{waves.map((wave) => <tr key={text(wave.wave_id)}>
        <td>{text(wave.wave_id)}</td><td>{wave.corrected ? "corrected" : wave.skip_reason ? "skipped" : "incomplete"}</td>
        <td>{fixed(wave.rise_start_elapsed_s)}–{fixed(wave.peak_elapsed_s)} s</td><td>{fixed(wave.peak_elapsed_s)}–{fixed(wave.tail_end_elapsed_s)} s</td>
        <td>{fixed(wave.added_area_bpm_s, 3)}</td><td>{fixed(wave.removed_area_bpm_s, 3)}</td><td>{fixed(wave.moved_area_bpm_s, 3)}</td>
        <td>{text(wave.capacity_limited)}</td><td>{fixed(wave.receiver_downhill_overlap_s)} s</td>
        <td>{text(wave.skip_reason ?? (Array.isArray(wave.flags) ? wave.flags.join(", ") : null))}</td>
      </tr>)}</tbody></table></div>

      <h2>Raw и HRmod времена по зони</h2>
      <table><thead><tr><th>Zone</th><th>Raw s</th><th>Clean s</th><th>HRmod s</th><th>Δ clean s</th></tr></thead>
        <tbody>{zones.map((zone) => <tr key={text(zone.zone_name)}><td>{text(zone.zone_name)}</td><td>{fixed(zone.raw_seconds)}</td><td>{fixed(zone.clean_seconds)}</td><td>{fixed(zone.hrmod_seconds)}</td><td>{fixed(zone.hrmod_minus_clean_seconds)}</td></tr>)}</tbody>
      </table>

      <h2>Сравнение по 15-секундни сегменти</h2>
      <div style={{ overflowX: "auto", maxHeight: 480 }}><table><thead><tr><th>Segment</th><th>Raw km/h</th><th>Vflat km/h</th><th>Raw HR</th><th>HRmod HR</th><th>Grade %</th></tr></thead>
        <tbody>{segments.map((segment) => <tr key={text(segment.segment_index)}><td>{fixed(segment.start_elapsed_s, 0)}–{fixed(segment.end_elapsed_s, 0)} s</td><td>{fixed(segment.speed_raw_kmh)}</td><td>{vflatEnabled ? fixed(segment.vflat_b65_kmh) : "off"}</td><td>{fixed(segment.hr_raw_bpm)}</td><td>{hrmodEnabled ? fixed(segment.hrmod_final_bpm) : "off"}</td><td>{fixed(segment.grade_smoothed_pct)}</td></tr>)}</tbody>
      </table></div>

      <h2>Versions, flags и exclusions</h2>
      <p>Vflat: {text(payload.vflat_model_version)} / {text(payload.vflat_config_version)} · HRmod: {text(payload.hrmod_model_version)} / {text(payload.hrmod_config_version)} · Terrain: {text(payload.terrain_model_version)}</p>
      <p>Flags: {allFlags.length ? allFlags.join(", ") : "няма"}</p>
      <details><summary>Hashes и пълна диагностика</summary><pre style={{ overflowX: "auto" }}>{JSON.stringify({ hashes: payload.hashes, diagnostics }, null, 2)}</pre></details>
    </div>
  );
}
