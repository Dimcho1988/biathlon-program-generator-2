import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../lib/account-access";
import { multiProfileMode } from "../../../../lib/athlete-session";
import { waitForApi } from "../../../../lib/api-readiness";
import {
  MESOCYCLE_ACCENT_COMPONENTS,
  type MesocycleAccentComponent,
  type MesocycleAccentMode,
} from "../../../../lib/planning-profile";

const redirect = (state: "accents-saved" | "accents-invalid" | "accents-error") =>
  new NextResponse(null, {
    status: 303,
    headers: { Location: `/planning?planning=${state}` },
  });

export async function POST(request: Request) {
  let stage = "session";
  try {
    const athlete = await currentAuthorizedAthlete();
    const athleteAlias = athlete?.athleteAlias;
    if (!multiProfileMode() || !athleteAlias)
      return new NextResponse(null, {
        status: 303,
        headers: { Location: "/?intervals=session-required" },
      });
    if (!athlete.canEditPlan)
      return new NextResponse(null, { status: 303, headers: { Location: "/planning?planning=forbidden" } });
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");

    stage = "form";
    const form = await request.formData();
    const schemaVersion = form.get("schema_version");
    const modeRaw = form.get("accent_mode");
    const mode = typeof modeRaw === "string"
      && ["AUTO", "MANUAL", "HYBRID"].includes(modeRaw)
      ? modeRaw as MesocycleAccentMode
      : null;
    const limitRaw = form.get("accent_limit");
    const limit = typeof limitRaw === "string" && /^[1-6]$/.test(limitRaw)
      ? Number(limitRaw)
      : null;
    const submittedComponents = form.getAll("manual_components");
    const uniqueComponents = new Set(submittedComponents);
    const componentsValid = uniqueComponents.size === submittedComponents.length
      && submittedComponents.every((component) =>
        typeof component === "string"
        && MESOCYCLE_ACCENT_COMPONENTS.includes(component as MesocycleAccentComponent));
    const manualComponents = MESOCYCLE_ACCENT_COMPONENTS.filter((component) =>
      uniqueComponents.has(component));

    if (
      schemaVersion !== "mesocycle-accent-preferences-v1"
      || mode === null
      || limit === null
      || !componentsValid
      || manualComponents.length > limit
      || (mode === "AUTO" && manualComponents.length !== 0)
      || (mode !== "AUTO" && manualComponents.length === 0)
    ) return redirect("accents-invalid");

    stage = "api";
    await waitForApi(baseUrl);
    const response = await fetch(
      new URL("/api/v2/athlete/mesocycle-accent-preferences", baseUrl),
      {
        method: "PUT",
        cache: "no-store",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-OnFlows-Athlete-Alias": athleteAlias,
        },
        body: JSON.stringify({
          schema_version: schemaVersion,
          accent_mode: mode,
          accent_limit: limit,
          manual_components: manualComponents,
        }),
        signal: AbortSignal.timeout(75_000),
      },
    );
    stage = `api-response-${response.status}`;
    if (response.status === 409 || response.status === 422)
      return redirect("accents-invalid");
    if (!response.ok) throw new Error("Mesocycle accent preference update failed");
    return redirect("accents-saved");
  } catch {
    console.error(`Mesocycle accent preference update failed [${stage}]`);
    return redirect("accents-error");
  }
}
