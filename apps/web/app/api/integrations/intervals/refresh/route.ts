import { NextResponse } from "next/server";

export async function POST() {
  try {
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");
    const response = await fetch(new URL("/api/v2/real/refresh", baseUrl), {
      method: "POST",
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      signal: AbortSignal.timeout(180_000),
    });
    if (!response.ok) throw new Error("Refresh failed");
    return new NextResponse(null, { status: 303, headers: { Location: "/" } });
  } catch {
    return new NextResponse(null, { status: 303, headers: { Location: "/?intervals=refresh-error" } });
  }
}
