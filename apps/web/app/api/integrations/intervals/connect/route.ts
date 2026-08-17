import { NextResponse } from "next/server";

const apiConfiguration = () => {
  const baseUrl = process.env.ONFLOWS_API_BASE_URL;
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");
  return { baseUrl, token };
};

const retryableInfrastructureStatus = (status: number) => status === 502 || status === 503 || status === 504;
const pause = () => new Promise((resolve) => setTimeout(resolve, 1_000));

const startAuthorization = (baseUrl: string, token: string) =>
  fetch(new URL("/api/v2/integrations/intervals/authorize", baseUrl), {
    method: "POST",
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    },
    // The preview API runs on Render Free and may need 50+ seconds to wake.
    signal: AbortSignal.timeout(75_000),
  });

const waitForApi = async (baseUrl: string) => {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const response = await fetch(new URL("/health", baseUrl), {
      cache: "no-store",
      signal: AbortSignal.timeout(75_000),
    });
    if (response.ok) return;
    if (!retryableInfrastructureStatus(response.status)) throw new Error("API health check failed");
    if (attempt < 2) await pause();
  }
  throw new Error("API did not wake in time");
};

export async function GET() {
  let stage = "configuration";
  try {
    const { baseUrl, token } = apiConfiguration();
    stage = "api-fetch";
    let response = await startAuthorization(baseUrl, token);
    if (retryableInfrastructureStatus(response.status)) {
      stage = `api-wake-${response.status}`;
      await waitForApi(baseUrl);
      stage = "api-retry";
      response = await startAuthorization(baseUrl, token);
    }
    stage = `api-response-${response.status}`;
    if (!response.ok) throw new Error("OAuth start failed");
    stage = "api-json";
    const payload: unknown = await response.json();
    if (typeof payload !== "object" || payload === null || !("authorization_url" in payload) || typeof payload.authorization_url !== "string")
      throw new Error("OAuth response is invalid");
    stage = "provider-destination";
    const destination = new URL(payload.authorization_url);
    if (destination.protocol !== "https:" || destination.hostname !== "intervals.icu" || destination.pathname !== "/oauth/authorize")
      throw new Error("OAuth destination is invalid");
    return NextResponse.redirect(destination, 303);
  } catch {
    console.error(`Intervals OAuth start failed [${stage}]`);
    return new NextResponse(null, { status: 303, headers: { Location: "/?intervals=connect-start-error" } });
  }
}
