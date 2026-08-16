import { loadHistoryFixture, recoveryHistoryFixture, trainingStatusFixture } from "./fixture";
import { parseLoadHistory, type LoadHistory } from "./load-history";
import { parseTrainingStatus, type TrainingStatus } from "./training-status";
import { parseRecoveryHistory, type RecoveryHistory } from "./recovery-history";

const TIMEOUT_MS = 8_000;
export type DataMode = "api" | "fixture";
export interface TrainingStatusResult { data: TrainingStatus; mode: DataMode }

async function fetchApiResource(path: string, token?: string): Promise<unknown> {
  const baseUrl = process.env.ONFLOWS_API_BASE_URL;
  if (!baseUrl) throw new Error("ONFLOWS_API_BASE_URL не е зададен. Изберете API адрес или explicit fixture режим.");
  let response: Response;
  try {
    response = await fetch(new URL(path, baseUrl), {
      signal: AbortSignal.timeout(TIMEOUT_MS), cache: "no-store",
      headers: token ? { Accept: "application/json", Authorization: `Bearer ${token}` } : { Accept: "application/json" },
    });
  } catch (error) {
    throw new Error("API услугата не отговори навреме или не е достъпна.", { cause: error });
  }
  if (!response.ok) throw new Error(`API услугата върна грешка (${response.status}).`);
  try { return await response.json(); } catch (error) { throw new Error("API услугата върна невалиден JSON.", { cause: error }); }
}

export async function getTrainingStatus(): Promise<TrainingStatusResult> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture")
    return { data: parseTrainingStatus(trainingStatusFixture), mode: "fixture" };
  const real = process.env.ONFLOWS_API_RESOURCE === "real";
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (real && !token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  const payload = await fetchApiResource(real ? "/api/v2/real/training-status" : "/api/v1/demo/training-status", token);
  return { data: parseTrainingStatus(payload), mode: "api" };
}

export async function getLoadHistory(): Promise<LoadHistory | null> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") return parseLoadHistory(loadHistoryFixture);
  if (process.env.ONFLOWS_API_RESOURCE !== "real") return null;
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parseLoadHistory(await fetchApiResource("/api/v2/real/load-history", token));
}

export async function getRecoveryHistory(): Promise<RecoveryHistory | null> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") return parseRecoveryHistory(recoveryHistoryFixture);
  if (process.env.ONFLOWS_API_RESOURCE !== "real") return null;
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("ONFLOWS_SERVICE_TOKEN не е зададен на Next.js server.");
  return parseRecoveryHistory(await fetchApiResource("/api/v2/real/recovery-history", token));
}
