import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { durationHms, durationSeconds, durationDelta } from "../lib/duration-format";
import { CompletedWorkSection } from "../components/completed-work-section";
import { completedWorkFixture } from "../lib/fixture";

describe("report durations", () => {
  it("rounds once to seconds, carries minutes and preserves hours beyond one day", () => {
    expect(durationHms(0)).toBe("0:00:00");
    expect(durationHms(59.999)).toBe("1:00:00");
    expect(durationHms(10696.2)).toBe("178:16:12");
    expect(durationHms(1 / 60)).toBe("0:00:01");
    expect(durationHms(9.3)).toBe("0:09:18");
    expect(durationSeconds(3599.6)).toBe("1:00:00");
    expect(durationSeconds(90061)).toBe("25:01:01");
  });
  it("keeps unknown and invalid durations distinct from zero and preserves signed differences", () => {
    for (const value of [null, undefined, NaN, Infinity, -1]) {
      expect(durationSeconds(value)).toBe("—");
      expect(durationHms(value)).toBe("—");
    }
    expect(durationDelta(-61)).toBe("−0:01:01");
    expect(durationDelta(61)).toBe("+0:01:01");
    expect(durationDelta(-0.1)).toBe("0:00:00");
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
