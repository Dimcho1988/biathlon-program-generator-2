import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { RecoveryV2Section } from "../components/recovery-v2";
import { RecoveryTimeline } from "../components/recovery-timeline";
import { StatusOverview } from "../components/status-overview";
import { MODEL_ZONES, parseRecoveryV2, type RecoveryV2 } from "../lib/models";
import { ZONES, type TrainingStatus } from "../lib/training-status";
import { Dashboard } from "../components/dashboard";
import { recoveryDisplayState } from "../lib/recovery-display";

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

function estimatedRecovery(): RecoveryV2 {
  const history = recovery(false);
  history.data_quality = {
    complete: false, missing_days: 0, partial_activities: 3, expected_days: 40, covered_days: 40,
    readiness_available: true, readiness_basis: "ESTIMATED_LOAD", estimated: true,
    recorded_seconds: 72000, unknown_seconds: 3120, coverage_percent: 95.666667,
    estimation: { policy: "UNIFIED_RECORDED_INDICATORS_WITH_PLANNING_ESTIMATES", version: "test-v1", supported: true,
      estimated_minutes: 52, estimated_activity_count: 3, unresolved_activity_count: 0, missing_calendar_days: [] },
  };
  return history;
}

describe("incomplete recovery is not displayed as complete readiness", () => {
  it("identifies the dated activity and reason while keeping readiness unavailable", () => {
    const history=recovery(false);
    history.data_quality!.issues=[{activity_ref:"recorded",date:"2026-10-05",sport:"Hike",reason:"Q_REANALYSIS_REQUIRED"}];
    const html=renderToStaticMarkup(<RecoveryV2Section history={parseRecoveryV2(history)} canEdit={false}/>);
    expect(html).toContain("2026-10-05");
    expect(html).toContain("Hike");
    expect(html).toContain("Товарът изисква преизчисляване от първичния запис");
    expect(recoveryDisplayState(history).unavailable).toBe(true);
    expect(html).not.toContain("Q_REANALYSIS_REQUIRED");
  });
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

  it("keeps incomplete recovery hidden in dashboard details", () => {
    const html = renderToStaticMarkup(<Dashboard view="details" mode="api" data={status} recoveryHistory={recovery(false)} />);
    expect(html).toContain("Недостатъчни данни за възстановяването");
    expect(html).toContain("<dt>Готовност за натоварване</dt><dd>Неопределено</dd>");
    expect(html).toContain("<dt>Дни до 90% готовност</dt><dd>Неопределено</dd>");
    expect(html).not.toContain("<dt>Готовност за натоварване</dt><dd>100%</dd>");
  });

  it("does not substitute legacy readiness if API recovery is missing", () => {
    const overview = renderToStaticMarkup(<Dashboard mode="api" data={status} recoveryHistory={null} />);
    expect(overview).toContain("Възстановяването не е достъпно");
    expect(overview).not.toContain("readiness-bar");
    expect(overview).not.toContain("100<small>%");
    const details = renderToStaticMarkup(<Dashboard view="details" mode="api" data={status} recoveryHistory={null} />);
    expect(details).toContain("<dt>Готовност за натоварване</dt><dd>Неопределено</dd>");
    expect(details).toContain("<dt>Дни до пълно възстановяване</dt><dd>Неопределено</dd>");
    const fixture = renderToStaticMarkup(<Dashboard mode="fixture" data={status} />);
    expect(fixture).toContain("100<small>%");
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

  it("displays fully covered speed-only load even when the last HR coverage is zero", () => {
    const history = recovery(true);
    history.data_quality = { ...history.data_quality!, readiness_available: true, readiness_basis: "RECORDED_LOAD", estimated: false,
      recorded_seconds: 72000, unknown_seconds: 0, coverage_percent: 100 };
    const noHrStatus = { ...status, data_quality: { ...status.data_quality, latest_activity_quality_score: 0 } };
    expect(parseRecoveryV2(history)).toBe(history);
    const overview = renderToStaticMarkup(<StatusOverview data={noHrStatus} recovery={history} load={null} />);
    expect(overview).toContain("100<small>%");
    expect(overview).not.toContain("Недостатъчни данни");
    expect(overview).not.toContain("Моделна оценка на възстановяването");
    const dashboard = renderToStaticMarkup(<Dashboard mode="api" data={noHrStatus} recoveryHistory={history} />);
    expect(dashboard).toContain("Покритие на товара 100%");
    expect(dashboard).toContain("Пулсово покритие · последна активност 0%");
  });

  it("shows supported estimated recovery consistently and preserves its incomplete measured coverage", () => {
    const history = estimatedRecovery();
    expect(parseRecoveryV2(history)).toBe(history);
    expect(recoveryDisplayState(history)).toEqual({ estimated: true, unavailable: false });
    for (const html of [
      renderToStaticMarkup(<RecoveryV2Section history={history} canEdit={false} />),
      renderToStaticMarkup(<StatusOverview data={status} recovery={history} load={null} />),
    ]) {
      expect(html).toContain("Моделна оценка на възстановяването");
      expect(html).toContain("95,7%");
      expect(html).toContain("0:52:00");
      expect(html).toContain("моделна оценка · достигнат ориентир");
      expect(html).not.toContain("Недостатъчни данни");
    }
    const timeline = renderToStaticMarkup(<RecoveryTimeline history={history} />);
    expect(timeline).toContain('data-recovery-zone="Z2"');
    expect(timeline).toContain("История с оценен товар");
    expect(history.data_quality!.complete).toBe(false);
    expect(history.data_quality!.unknown_seconds).toBe(3120);
  });

  it("uses and labels the projected estimate in dashboard details", () => {
    const history = estimatedRecovery();
    history.current[0] = { ...history.current[0], readiness_percent: 37.5, residual_fatigue: 62.5, days_to_practical_recovery: 2 };
    const html = renderToStaticMarkup(<Dashboard view="details" mode="api" data={status} recoveryHistory={history} />);
    expect(html).toContain("Моделна оценка на възстановяването");
    expect(html).toContain("<dt>Готовност за натоварване · моделна оценка</dt><dd>37,5%</dd>");
    expect(html).toContain("<dt>Дни до 90% готовност · моделна оценка</dt><dd>2 дни</dd>");
  });

  it.each(["missing_day", "unresolved_activity", "unsupported_estimate", "explicitly_unavailable"])("keeps genuinely unresolved data blocked: %s", reason => {
    const history = estimatedRecovery();
    const quality = history.data_quality!;
    if (reason === "missing_day") quality.missing_days = 1;
    if (reason === "unresolved_activity") quality.estimation!.unresolved_activity_count = 1;
    if (reason === "unsupported_estimate") quality.estimation!.supported = false;
    if (reason === "explicitly_unavailable") quality.readiness_available = false;
    expect(() => parseRecoveryV2(history)).toThrow();
    expect(recoveryDisplayState(history).unavailable).toBe(true);
    const html = renderToStaticMarkup(<RecoveryV2Section history={history} canEdit={false} />);
    expect(html).toContain("Недостатъчни данни");
    expect(html).not.toContain('data-recovery-zone="Z2"');
    expect(html).not.toContain("Моделна оценка на възстановяването");
  });

  it("rejects a measured-source label over incomplete recorded load and impossible coverage", () => {
    const history = estimatedRecovery();
    history.data_quality!.readiness_basis = "RECORDED_LOAD";
    history.data_quality!.estimated = false;
    expect(() => parseRecoveryV2(history)).toThrow(/отчетен товар/);
    history.data_quality!.unknown_seconds = 72001;
    expect(() => parseRecoveryV2(history)).toThrow(/покритие/);
  });
});
