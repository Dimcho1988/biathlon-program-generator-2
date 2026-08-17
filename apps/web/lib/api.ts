import { completedWorkFixture, loadHistoryFixture, recoveryHistoryFixture, trainingStatusFixture } from "./fixture";
import { parseCompletedWork, type CompletedWork } from "./completed-work";
import { parseLoadHistory, type LoadHistory } from "./load-history";
import { parseTrainingStatus, type TrainingStatus } from "./training-status";
import { parseRecoveryHistory, type RecoveryHistory } from "./recovery-history";
import { waitForApi } from "./api-readiness";

// Render Free can take more than 50 seconds to wake the API after inactivity.
// Keep the preview reliable without introducing a paid always-on service.
const TIMEOUT_MS = 75_000;
export type DataMode = "api" | "fixture";
export interface TrainingStatusResult { data: TrainingStatus; mode: DataMode }
export interface AthleteSettings {
  configured: boolean;
  hr_zone_bounds_bpm: [number, number, number, number, number, number] | null;
  timezone: string | null;
}

async function fetchApiResource(path: string, token?: string, athleteAlias?: string): Promise<unknown> {
  const baseUrl = process.env.ONFLOWS_API_BASE_URL;
  if (!baseUrl) throw new Error("ONFLOWS_API_BASE_URL не е зададен. Изберете API адрес или explicit fixture режим.");
  try {
    await waitForApi(baseUrl);
  } catch (error) {
    throw new Error("API услугата не се събуди навреме.", { cause: error });
  }
  let response: Response;
  try {
    response = await fetch(new URL(path, baseUrl), {
      signal: AbortSignal.timeout(TIMEOUT_MS), cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(athleteAlias ? { "X-OnFlows-Athlete-Alias": athleteAlias } : {}),
      },
    });
  } catch (error) {
    throw new Error("API услугата не отговори навреме или не е достъпна.", { cause: error });
  }
  if (!response.ok) throw new Error(`API услугата върна грешка (${response.status}).`);
  try { return await response.json(); } catch (error) { throw new Error("API услугата върна невалиден JSON.", { cause: error }); }
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
  if (bounds !== null && (!Array.isArray(bounds) || bounds.length !== 6 || !bounds.every((value) => Number.isInteger(value))))
    throw new Error("API услугата върна невалидни HR граници.");
  if (timezone !== null && typeof timezone !== "string")
    throw new Error("API услугата върна невалидна часова зона.");
  return { configured: payload.configured, hr_zone_bounds_bpm: bounds as AthleteSettings["hr_zone_bounds_bpm"], timezone };
}
