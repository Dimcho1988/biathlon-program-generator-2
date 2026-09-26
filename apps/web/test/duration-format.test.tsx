import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { durationHms } from "../lib/duration-format";
import { CompletedWorkSection } from "../components/completed-work-section";
import { completedWorkFixture } from "../lib/fixture";

describe("report durations", () => {
  it("rounds once to seconds, carries minutes and preserves hours beyond one day", () => {
    expect(durationHms(0)).toBe("0:00:00");
    expect(durationHms(59.999)).toBe("1:00:00");
    expect(durationHms(10696.2)).toBe("178:16:12");
    expect(durationHms(1 / 60)).toBe("0:00:01");
  });
  it("formats all report time columns but retains the numeric effective load", () => {
    const r = structuredClone(completedWorkFixture);
    r.totals.activity_duration_min = 10696.2;
    r.zones[0].raw_time_min = 61.5; r.zones[0].equivalent_time_min = 30.5; r.zones[0].effective_load = 637.3;
    const html = renderToStaticMarkup(<CompletedWorkSection report={r} />);
    for (const v of ["178:16:12", "1:01:30", "0:30:30", "637,3", "ч:мм:сс"]) expect(html).toContain(v);
    expect(html).not.toMatch(/\d мин<\/t[dh]>/);
  });
});
