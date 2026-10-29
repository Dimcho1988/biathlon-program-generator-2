import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { RecoveryV2Section } from "../components/recovery-v2";
import { RecoveryTimeline } from "../components/recovery-timeline";
import { StatusOverview } from "../components/status-overview";
import { MODEL_ZONES, type RecoveryV2 } from "../lib/models";
import { ZONES, type TrainingStatus } from "../lib/training-status";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

function recovery(complete: boolean): RecoveryV2 {
  const baseline = { baseline_daily_min: 20, baseline_raw_daily_min: 20, history_days: 40, baseline_source: "PERSONAL" as const };
  return {
    schema_version: "recovery-history-v2", athlete_id: "test", period_start: "2026-09-01", period_end: "2026-10-10", as_of: "2026-10-10",
    basis: "load-only", time_resolution: "calendar-day", ready_threshold_percent: 90, config_revision: 0,
    model: { algorithm_version: "recovery-daily-e-biexponential-v2", parameter_version: "v1", parameter_fingerprint: "test", practical_full_recovery_percent: 90 },
    settings: Object.fromEntries(MODEL_ZONES.map(zone => [zone, { duration_coefficient: 1, shape: 1, sensitivity: 1, initial_daily_min: 20 }])) as RecoveryV2["settings"],
    current: MODEL_ZONES.map(zone => ({ ...baseline, zone, readiness_percent: 100, residual_fatigue: 0, days_to_practical_recovery: 0 })),
    daily: [{ ...baseline, date: "2026-10-09", zone: "Z2", readiness_before_percent: 100, readiness_after_percent: 100, residual_fatigue_after: 0, impulse: 0, effective_load: 37.5, isolated_days_to_90: 0, residual_fatigue_now: 1 }],
    forecast: MODEL_ZONES.flatMap(zone => [0, 1, 2].map(days => ({ zone, days, readiness_percent: 100 }))),
    source_as_of: "2026-10-10", source_stale: false, warnings: [],
    data_quality: { complete, missing_days: complete ? 0 : 2, partial_activities: complete ? 0 : 3, expected_days: 40, covered_days: complete ? 40 : 38 },
  };
}

const status: TrainingStatus = {
  schema_version: "training-status-v1", as_of: "2026-10-10", athlete_id: "test",
  model: { algorithm_version: "test", effective_hr_version: "test", effective_hr_source: "test", parameter_version: 1 },
  data_quality: { history_reliability: 1, latest_activity_quality_score: 1, warnings: [] },
  zones: ZONES.map(zone => ({ zone, raw_time_min: 0, equivalent_time_min: 0, tref_min: 30, status_7_40: 1, recovery_readiness_percent: 100, recovery_days_to_full: 0 })),
};

describe("incomplete recovery is not displayed as complete readiness", () => {
  it("keeps observed load while withholding readiness, recovery dates and the forecast", () => {
    const history = recovery(false);
    const html = renderToStaticMarkup(<RecoveryV2Section history={history} canEdit={false} />);
    expect(html).toContain("Недостатъчни данни");
    expect(html).toContain("3 непълно оценени активности");
    expect(html).toContain("2 дни без пълни данни");
    expect(html).toContain("Неопределено");
    expect(html).toContain("37,5");
    expect(html).not.toContain("100%");
    expect(html).not.toContain("достигнат ориентир");
    expect(html).not.toContain("data-recovery-zone");
    expect(html).not.toContain("Дни с най-голяма остатъчна умора");
    expect(history.current[0].readiness_percent).toBe(100);
  });

  it("also suppresses the standalone timeline and overview bars", () => {
    const timeline = renderToStaticMarkup(<RecoveryTimeline history={recovery(false)} />);
    expect(timeline).toContain("Недостатъчни данни за графика");
    expect(timeline).not.toContain("<svg");
    const overview = renderToStaticMarkup(<StatusOverview data={status} recovery={recovery(false)} load={null} />);
    expect(overview).toContain("Недостатъчни данни");
    expect(overview).toContain("7/40 е изчислен само от отчетения товар");
    expect(overview).not.toContain("readiness-bar");
    expect(overview).not.toContain("100<small>%");
    expect(overview).not.toContain("достигнат ориентир");
  });

  it("keeps normal percentages, readiness labels and forecast for complete data", () => {
    const history = recovery(true);
    const html = renderToStaticMarkup(<RecoveryV2Section history={history} canEdit={false} />);
    expect(html).toContain("100%");
    expect(html).toContain("достигнат ориентир");
    expect(html).toContain('data-recovery-zone="Z2"');
    expect(html).not.toContain("Недостатъчни данни");
    const overview = renderToStaticMarkup(<StatusOverview data={status} recovery={history} load={null} />);
    expect(overview).toContain("readiness-bar");
    expect(overview).toContain("100<small>%");
    expect(overview).toContain("достигнат ориентир");
  });
});
