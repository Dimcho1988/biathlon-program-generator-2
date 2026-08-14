import { trainingStatusFixture } from "./fixture";
import { parseTrainingStatus, type TrainingStatus } from "./training-status";

const ENDPOINT = "/api/v1/demo/training-status";
const TIMEOUT_MS = 8_000;

export type DataMode = "api" | "fixture";
export interface TrainingStatusResult { data: TrainingStatus; mode: DataMode }

export async function getTrainingStatus(): Promise<TrainingStatusResult> {
  if (process.env.ONFLOWS_DATA_MODE === "fixture") {
    return { data: parseTrainingStatus(trainingStatusFixture), mode: "fixture" };
  }
  const baseUrl = process.env.ONFLOWS_API_BASE_URL;
  if (!baseUrl) throw new Error("ONFLOWS_API_BASE_URL не е зададен. Изберете API адрес или explicit fixture режим.");
  let response: Response;
  try {
    response = await fetch(new URL(ENDPOINT, baseUrl), {
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    throw new Error("API услугата не отговори навреме или не е достъпна.", { cause: error });
  }
  if (!response.ok) throw new Error(`API услугата върна грешка (${response.status}).`);
  let payload: unknown;
  try { payload = await response.json(); } catch (error) {
    throw new Error("API услугата върна невалиден JSON.", { cause: error });
  }
  return { data: parseTrainingStatus(payload), mode: "api" };
}
