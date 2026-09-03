import { completedWorkFixture, loadHistoryFixture, recoveryHistoryFixture, trainingStatusFixture, volumeHistoryFixture } from "./fixture";
import { parseCompletedWork, type CompletedWork } from "./completed-work";
import { parseLoadHistory, type LoadHistory } from "./load-history";
import { parseTrainingStatus, type TrainingStatus } from "./training-status";
import { parseRecoveryHistory, type RecoveryHistory } from "./recovery-history";
import { parseVolumeHistory, type VolumeHistory } from "./volume-history";
import {
  parseMesocycleAccentPreferencesResponse,
  parsePlanningMethodology,
  parsePlanningProfileResponse,
  type MesocycleAccentPreferencesResponse,
  type PlanningMethodology,
  type PlanningProfileResponse,
} from "./planning-profile";
import {
  parsePlanningCalendarResponse,
  type PlanningCalendarResponse,
} from "./planning-calendar";
import { waitForApi } from "./api-readiness";
import {
  activityCalendarFixture,
  activityDetailFixture,
  activitySeriesFixture,
  parseActivityCalendar,
  parseActivityDetail,
  parseActivitySeries,
  parseActivityView,
  type ActivityCalendar,
  type ActivityDetail,
  type ActivitySeries,
  type ActivityView,
} from "./activities";
import { parseDashboardView, type DashboardView } from "./dashboard-view";
import { parseSyncState, type SyncState } from "./sync";

// Render Free can take more than 50 seconds to wake the API after inactivity.
// Keep the preview reliable without introducing a paid always-on service.
const TIMEOUT_MS = 75_000;
const DIRECT_WAKE_TIMEOUT_MS = 25_000;
const DIRECT_WAKE_RETRY_DELAY_MS = 2_000;
const DIRECT_WAKE_ATTEMPTS = 3;
export type DataMode = "api" | "fixture";
export interface TrainingStatusResult { data: TrainingStatus; mode: DataMode }
export interface AthleteSettings {
  configured: boolean;
  hr_zone_bounds_bpm: [number, number, number, number, number, number] | null;
  timezone: string | null;
  hrmax_bpm: number | null;
}
export interface ActivityShadowIndexRow {
  activity_ref: string;
  run_key: string;
  created_at?: string;
  vflat_model_version?: string | null;
  hrmod_model_version?: string | null;
  terrain_model_version?: string | null;
}
export interface ActivityShadowIndex {
  schema_version: "activity-shadow-index-v1";
  activities: ActivityShadowIndexRow[];
}

interface ResourceReliabilityOptions {
  continueAfterReadinessFailure?: boolean;
  skipReadiness?: boolean;
  timeoutMs?: number;
  attempts?: number;
}

const pause = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function fetchApiResource(
  path: string,
  token?: string,
  athleteAlias?: string,
  reliability: ResourceReliabilityOptions = {},
): Promise<unknown> {
  const baseUrl = process.env.ONFLOWS_API_BASE_URL;
  if (!baseUrl) throw new Error("ONFLOWS_API_BASE_URL не е зададен. Изберете API адрес или explicit fixture режим.");
  let readinessFailed = false;
  if (!reliability.skipReadiness) {
    try {
      await waitForApi(baseUrl);
    } catch (error) {
      if (!reliability.continueAfterReadinessFailure)
        throw new Error("API услугата не се събуди навреме.", { cause: error });
      readinessFailed = true;
    }
  }

  // A healthy Render process can still return one transient gateway/store 5xx
  // while several dashboard resources read the same snapshot in parallel.
  // Retry only infrastructure responses; valid 4xx and contract failures still
  // fail immediately.
  const attempts = reliability.attempts ?? DIRECT_WAKE_ATTEMPTS;
  let response: Response | null = null;
  let requestError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      response = await fetch(new URL(path, baseUrl), {
        signal: AbortSignal.timeout(reliability.timeoutMs ?? (readinessFailed || attempt > 1 ? DIRECT_WAKE_TIMEOUT_MS : TIMEOUT_MS)), cache: "no-store",
        headers: {
          Accept: "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(athleteAlias ? { "X-OnFlows-Athlete-Alias": athleteAlias } : {}),
        },
      });
      requestError = undefined;
    } catch (error) {
      response = null;
      requestError = error;
    }
    const retryableResponse = response !== null && (
      response.status === 502 || response.status === 503 || response.status === 504 ||
      (response.ok && !(response.headers.get("content-type") ?? "").toLowerCase().includes("application/json"))
    );
    if (response && !retryableResponse) break;
    if (attempt < attempts) await pause(DIRECT_WAKE_RETRY_DELAY_MS);
  }
  if (!response)
    throw new Error("API услугата не отговори навреме или не е достъпна.", { cause: requestError });
  if (!response.ok) throw new Error(`API услугата върна грешка (${response.status}).`);
  try { return await response.json(); } catch (error) { throw new Error("API услугата върна невалиден JSON.", { cause: error }); }
}

export async function getSyncState(
  athleteAlias?: string,
  options: { direct?: boolean } = {},
): Promise<SyncState> {
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  const payload = await fetchApiResource(
    "/api/v2/real/sync-status",
    token,
    athleteAlias,
    options.direct ? { skipReadiness: true, timeoutMs: 10_000, attempts: 1 } : {},
  );
  return parseSyncState(payload);
}

export async function getDashboardView(
  athleteAlias?: string,
  periodStart?: string,
  periodEnd?: string,
): Promise<DashboardView> {
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  const query = new URLSearchParams();
  if (periodStart) query.set("period_start", periodStart);
  if (periodEnd) query.set("period_end", periodEnd);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return parseDashboardView(await fetchApiResource(
    `/api/v2/real/dashboard-view${suffix}`,
    token,
    athleteAlias,
  ));
}

export async function getTrainingStatus(athleteAlias?: string): Promise<TrainingStatusResult> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture")
    return { data: parseTrainingStatus(trainingStatusFixture), mode: "fixture" };
  const real = process.env.ONFLOWS_API_RESOURCE === "real";
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (real && !token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  const payload = await fetchApiResource(real ? "/api/v2/real/training-status" : "/api/v1/demo/training-status", token, athleteAlias);
  return { data: parseTrainingStatus(payload), mode: "api" };
}

export async function getLoadHistory(athleteAlias?: string): Promise<LoadHistory | null> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") return parseLoadHistory(loadHistoryFixture);
  if (process.env.ONFLOWS_API_RESOURCE !== "real") return null;
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parseLoadHistory(await fetchApiResource("/api/v2/real/load-history", token, athleteAlias));
}

export async function getCompletedWork(athleteAlias?: string, periodStart?: string, periodEnd?: string): Promise<CompletedWork | null> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") return parseCompletedWork(completedWorkFixture);
  if (process.env.ONFLOWS_API_RESOURCE !== "real") return null;
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  const query = new URLSearchParams();
  if (periodStart) query.set("period_start", periodStart);
  if (periodEnd) query.set("period_end", periodEnd);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return parseCompletedWork(await fetchApiResource(`/api/v2/real/completed-work${suffix}`, token, athleteAlias));
}

export async function getVolumeHistory(athleteAlias?: string): Promise<VolumeHistory | null> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") return parseVolumeHistory(volumeHistoryFixture);
  if (process.env.ONFLOWS_API_RESOURCE !== "real") return null;
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parseVolumeHistory(await fetchApiResource("/api/v2/real/volume-history", token, athleteAlias));
}

export async function getRecoveryHistory(athleteAlias?: string): Promise<RecoveryHistory | null> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") return parseRecoveryHistory(recoveryHistoryFixture);
  if (process.env.ONFLOWS_API_RESOURCE !== "real") return null;
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parseRecoveryHistory(await fetchApiResource("/api/v2/real/recovery-history", token, athleteAlias));
}

export async function getAthleteSettings(athleteAlias: string): Promise<AthleteSettings> {
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  const payload = await fetchApiResource("/api/v2/athlete/settings", token, athleteAlias);
  if (typeof payload !== "object" || payload === null || !("configured" in payload) || typeof payload.configured !== "boolean")
    throw new Error("API услугата върна невалидни настройки на спортиста.");
  const bounds = "hr_zone_bounds_bpm" in payload ? payload.hr_zone_bounds_bpm : null;
  const timezone = "timezone" in payload ? payload.timezone : null;
  const hrmax = "hrmax_bpm" in payload ? payload.hrmax_bpm : null;
  if (bounds !== null && (!Array.isArray(bounds) || bounds.length !== 6 || !bounds.every((value) => Number.isInteger(value))))
    throw new Error("API услугата върна невалидни HR граници.");
  if (timezone !== null && typeof timezone !== "string")
    throw new Error("API услугата върна невалидна часова зона.");
  if (hrmax !== null && (!Number.isInteger(hrmax) || Number(hrmax) < 30 || Number(hrmax) > 240))
    throw new Error("API услугата върна невалиден HRmax.");
  return { configured: payload.configured, hr_zone_bounds_bpm: bounds as AthleteSettings["hr_zone_bounds_bpm"], timezone, hrmax_bpm: hrmax as number | null };
}

export async function getActivityShadowIndex(
  athleteAlias: string,
): Promise<ActivityShadowIndex> {
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  const payload = await fetchApiResource(
    "/api/v2/real/activity-shadows",
    token,
    athleteAlias,
  );
  if (
    typeof payload !== "object" || payload === null ||
    !("activities" in payload) || !Array.isArray(payload.activities)
  ) throw new Error("API услугата върна невалиден shadow index.");
  return payload as ActivityShadowIndex;
}

export async function getActivityShadow(
  athleteAlias: string,
  activityRef: string,
): Promise<Record<string, unknown>> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") {
    const timeseries = activitySeriesFixture.series.map((row) => ({
      ...row,
      speed_raw_kmh: row.speed_kmh,
      vflat_b65_kmh: row.speed_kmh === null ? null : row.speed_kmh * 1.04,
      hr_raw_bpm: row.hr_bpm,
      hr_clean_bpm: row.hr_bpm,
      hrmod_candidate_bpm: row.hr_bpm === null ? null : row.hr_bpm + 2,
      hrmod_final_bpm: row.hr_bpm === null ? null : row.hr_bpm + 1.2,
      grade_raw_pct: row.grade_pct,
      grade_smoothed_pct: row.grade_pct,
      added_bpm: 1.2,
      removed_bpm: 0,
      receiver_flag: false,
      donor_flag: false,
    }));
    return {
      schema_version: "activity-shadow-derived-v2",
      experimental: true,
      affects_canonical_load: false,
      vflat_model_version: "vflat_b65_inertia_extrapolation_v4",
      vflat_config_version: "vflat_b65_config_v4",
      hrmod_model_version: "hrmod_mirror_area_shift_v6",
      hrmod_config_version: "hrmod_config_v6",
      terrain_model_version: "terrain_downhill_donor_exclusion_v4",
      timeseries,
      segments_15s: [],
      hrmod_waves: [],
      zone_summary: [],
      diagnostics: { hrmod: { corrected_wave_count: 0 } },
    };
  }
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  if (!/^(?:shadow-|act_)[a-f0-9]{32}$/.test(activityRef))
    throw new Error("Невалиден shadow activity reference.");
  const path = activityRef.startsWith("act_")
    ? `/api/v2/real/activities/${encodeURIComponent(activityRef)}/shadow`
    : `/api/v2/real/activity-shadow?activity_ref=${encodeURIComponent(activityRef)}`;
  const payload = await fetchApiResource(
    path,
    token,
    athleteAlias,
  );
  if (typeof payload !== "object" || payload === null)
    throw new Error("API услугата върна невалиден shadow резултат.");
  return payload as Record<string, unknown>;
}

export async function getActivityCalendar(
  athleteAlias?: string,
  periodStart?: string,
  periodEnd?: string,
): Promise<ActivityCalendar> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") return activityCalendarFixture;
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  const query = new URLSearchParams();
  if (periodStart) query.set("period_start", periodStart);
  if (periodEnd) query.set("period_end", periodEnd);
  const suffix = query.size ? `?${query.toString()}` : "";
  return parseActivityCalendar(await fetchApiResource(`/api/v2/real/activities${suffix}`, token, athleteAlias));
}

export async function getActivityDetail(
  activityRef: string,
  athleteAlias?: string,
): Promise<ActivityDetail> {
  if (!/^act_[a-f0-9]{32}$/.test(activityRef)) throw new Error("Невалиден activity reference.");
  if (process.env.ONFLOWS_DATA_MODE === "fixture") {
    const selected = activityCalendarFixture.activities.find((activity) => activity.activity_ref === activityRef);
    return selected ? { ...activityDetailFixture, ...selected, activity_ref: selected.activity_ref } : activityDetailFixture;
  }
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parseActivityDetail(await fetchApiResource(
    `/api/v2/real/activities/${encodeURIComponent(activityRef)}`,
    token,
    athleteAlias,
    { continueAfterReadinessFailure: true },
  ));
}

export async function getActivitySeries(
  activityRef: string,
  athleteAlias?: string,
): Promise<ActivitySeries | null> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") return { ...activitySeriesFixture, activity_ref: activityRef };
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  try {
    return parseActivitySeries(await fetchApiResource(
      `/api/v2/real/activities/${encodeURIComponent(activityRef)}/series`,
      token,
      athleteAlias,
      { continueAfterReadinessFailure: true },
    ));
  } catch (error) {
    if (error instanceof Error && error.message.includes("(404)")) return null;
    throw error;
  }
}

export async function getActivityView(
  activityRef: string,
  athleteAlias?: string,
): Promise<ActivityView> {
  if (!/^act_[a-f0-9]{32}$/.test(activityRef)) throw new Error("Невалиден activity reference.");
  if (process.env.ONFLOWS_DATA_MODE === "fixture") {
    const selected = activityCalendarFixture.activities.find((activity) => activity.activity_ref === activityRef);
    const activity = selected
      ? { ...activityDetailFixture, ...selected, activity_ref: selected.activity_ref }
      : { ...activityDetailFixture, activity_ref: activityRef };
    const shadow = activity.shadow_available ? await getActivityShadow("", activityRef) : null;
    return parseActivityView({
      schema_version: "activity-view-v1",
      generation_id: null,
      revision: 0,
      analysis_as_of: null,
      activated_at: null,
      activity,
      series: { ...activitySeriesFixture, activity_ref: activity.activity_ref },
      shadow,
    });
  }
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parseActivityView(await fetchApiResource(
    `/api/v2/real/activities/${encodeURIComponent(activityRef)}/view`,
    token,
    athleteAlias,
    { continueAfterReadinessFailure: true },
  ));
}

export async function getAthletePlanningProfile(athleteAlias: string): Promise<PlanningProfileResponse> {
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parsePlanningProfileResponse(
    await fetchApiResource("/api/v2/athlete/planning-profile", token, athleteAlias),
  );
}

export async function getPlanningMethodology(athleteAlias: string): Promise<PlanningMethodology> {
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parsePlanningMethodology(
    await fetchApiResource("/api/v2/planning/methodology", token, athleteAlias),
  );
}

export async function getMesocycleAccentPreferences(
  athleteAlias: string,
): Promise<MesocycleAccentPreferencesResponse> {
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parseMesocycleAccentPreferencesResponse(
    await fetchApiResource(
      "/api/v2/athlete/mesocycle-accent-preferences",
      token,
      athleteAlias,
    ),
  );
}

export async function getPlanningCalendar(
  athleteAlias: string,
): Promise<PlanningCalendarResponse> {
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parsePlanningCalendarResponse(
    await fetchApiResource(
      "/api/v2/athlete/planning-calendar",
      token,
      athleteAlias,
    ),
  );
}
