export const DASHBOARD_VIEWS = [
  ["overview", "Обобщение"],
  ["load", "Натоварване"],
  ["recovery", "Възстановяване"],
  ["report", "Отчет"],
  ["details", "Подробности"],
] as const;

export type DashboardViewKey = typeof DASHBOARD_VIEWS[number][0];

export function dashboardView(value?: string): DashboardViewKey {
  return DASHBOARD_VIEWS.find(([key]) => key === value)?.[0] ?? "overview";
}

export function dashboardHref(view: DashboardViewKey, start?: string, end?: string) {
  const query = new URLSearchParams();
  if (view !== "overview") query.set("view", view);
  if (start) query.set("report_start", start);
  if (end) query.set("report_end", end);
  return query.size ? `/?${query}` : "/";
}

// Remember only presentation filters, never account identifiers or notices.
export function rememberedNavigationHref(path: string, saved: string | null): string {
  if (!saved) return path;
  try {
    const url = new URL(saved, "https://onflows.invalid");
    if (url.origin !== "https://onflows.invalid" || url.pathname !== path) return path;
    const keys = path === "/" ? ["view", "report_start", "report_end"] : ["start", "end"];
    if (!["/", "/activities", "/trainability", "/response"].includes(path)) return path;
    const query = new URLSearchParams();
    for (const key of keys) {
      const value = url.searchParams.get(key);
      if (value && (key === "view" ? DASHBOARD_VIEWS.some(([name]) => name === value) : /^\d{4}-\d{2}-\d{2}$/.test(value))) query.set(key, value);
    }
    return query.size ? `${path}?${query}` : path;
  } catch { return path; }
}
