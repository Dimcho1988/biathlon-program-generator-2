import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AppShell } from "../components/app-shell";
import { dashboardHref, rememberedNavigationHref } from "../lib/dashboard-navigation";

let pathname = "/trainability";
vi.mock("next/navigation", () => ({ usePathname: () => pathname, useRouter: () => ({ push: vi.fn() }) }));

describe("navigation between analysis pages", () => {
  it("keeps the global menu and athlete visible on the index, detail, loading and error screens", () => {
    for (const route of ["/trainability", "/activities/activity-1", "/response", "/speed", "/"]) {
      pathname = route;
      const html = renderToStaticMarkup(<AppShell athlete={<p>Избран спортист</p>}><main>Зареждаме данните…</main></AppShell>);
      for (const href of ["/", "/activities", "/trainability", "/response", "/speed", "/account"]) expect(html).toContain(`href="${href}"`);
      expect(html).toContain("Избран спортист");
      expect(html).toContain('aria-controls="workspace-navigation"');
      expect(html).toContain('aria-label="Превключи светла или тъмна тема"');
      expect(html).toContain('aria-current="page"');
    }
  });
  it("retains report dates when switching views and strips transient or private query values", () => {
    expect(dashboardHref("recovery", "2026-09-01", "2026-09-14")).toBe("/?view=recovery&report_start=2026-09-01&report_end=2026-09-14");
    expect(rememberedNavigationHref("/", "/?view=report&report_start=2026-09-01&report_end=2026-09-14&wake=ready&sync=queued&athlete_alias=private")).toBe("/?view=report&report_start=2026-09-01&report_end=2026-09-14");
    expect(rememberedNavigationHref("/trainability", "/trainability?start=2026-09-01&end=2026-09-14")).toBe("/trainability?start=2026-09-01&end=2026-09-14");
  });
  it("rejects external and different-page destinations from browser storage", () => {
    for (const saved of ["//example.com/trainability?start=2026-09-01", "https://example.com/trainability", "/api/session/logout", "javascript:alert(1)", "/trainability?start=invalid&end=invalid"]) {
      expect(rememberedNavigationHref("/trainability", saved)).toBe("/trainability");
    }
  });
});
