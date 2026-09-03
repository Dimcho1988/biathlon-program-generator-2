import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ShadowActivityPanel } from "../components/shadow-activity-panel";

const payload = {
  vflat_model_version: "vflat_b65_inertia_extrapolation_v4",
  vflat_config_version: "vflat_b65_config_v4",
  hrmod_model_version: "hrmod_mirror_area_shift_v6",
  hrmod_config_version: "hrmod_config_v6",
  terrain_model_version: "terrain_downhill_donor_exclusion_v4",
  timeseries: [
    { timestamp: "2026-08-22T08:00:00Z", elapsed_s: 0, speed_raw_kmh: 12, vflat_b65_kmh: 13, hr_raw_bpm: 140, hr_clean_bpm: 140, hrmod_candidate_bpm: 142, hrmod_final_bpm: 142, grade_raw_pct: 2, grade_smoothed_pct: 2, added_bpm: 2, removed_bpm: 0, receiver_flag: true, donor_flag: false, quality_flags: [], model_flags: [] },
    { timestamp: "2026-08-22T08:00:01Z", elapsed_s: 1, speed_raw_kmh: 11, vflat_b65_kmh: 13, hr_raw_bpm: 141, hr_clean_bpm: 141, hrmod_candidate_bpm: 143, hrmod_final_bpm: 143, grade_raw_pct: 3, grade_smoothed_pct: 2.5, added_bpm: 2, removed_bpm: 0, receiver_flag: true, donor_flag: false, quality_flags: [], model_flags: ["RECEIVER_DOWNHILL_OVERLAP"] },
    { timestamp: "2026-08-22T08:00:02Z", elapsed_s: 2, speed_raw_kmh: 14, vflat_b65_kmh: 14, hr_raw_bpm: 143, hr_clean_bpm: 143, hrmod_candidate_bpm: 139, hrmod_final_bpm: 139, grade_raw_pct: -2, grade_smoothed_pct: -1.5, added_bpm: 0, removed_bpm: 4, receiver_flag: false, donor_flag: true, quality_flags: [], model_flags: [] },
  ],
  segments_15s: [
    { segment_index: 0, start_elapsed_s: 0, end_elapsed_s: 15, speed_raw_kmh: 12.3, vflat_b65_kmh: 13.3, hr_raw_bpm: 141.3, hrmod_final_bpm: 141.3, grade_smoothed_pct: 1 },
  ],
  zone_summary: [
    { zone_name: "Z1", raw_seconds: 30, clean_seconds: 30, hrmod_candidate_seconds: 25, hrmod_final_seconds: 20, final_minus_clean_seconds: -10 },
    { zone_name: "Z2", raw_seconds: 30, clean_seconds: 30, hrmod_candidate_seconds: 35, hrmod_final_seconds: 40, final_minus_clean_seconds: 10 },
  ],
  hrmod_waves: [
    { wave_id: 1, corrected: true, rise_start_elapsed_s: 0, peak_elapsed_s: 1, tail_end_elapsed_s: 2, added_area_bpm_s: 4, removed_area_bpm_s: 4, moved_area_bpm_s: 4, capacity_limited: false, receiver_downhill_overlap_s: 0, flags: ["AREA_CONSERVATION_PASSED"] },
  ],
  diagnostics: {
    hrmod: { max_added_bpm: 2, max_removed_bpm: 4, corrected_wave_count: 1, skipped_wave_count: 0, incomplete_wave_count: 0, total_moved_area_bpm_s: 4 },
  },
  hashes: { hr_input_hash: "abc" },
};

describe("Raw ↔ Shadow comparison", () => {
  it("renders a coach-facing parallel comparison without implying canonical effects", () => {
    const html = renderToStaticMarkup(
      <ShadowActivityPanel payload={payload} activityRef="shadow-0123456789abcdef0123456789abcdef" />,
    );
    for (const label of [
      "Средна скорост", "Среден пулс", "Преразпределено по зони",
      "Реална скорост ↔ Vflat B65", "Raw / clean HR ↔ HRmod candidate / final",
      "Receiver и donor интервали", "Raw ↔ HRmod времена по зони",
      "15-секундни сегменти (1)", "HR вълни, receiver и donor (1)",
    ]) expect(html).toContain(label);
    expect(html).toContain("vflat_b65_inertia_extrapolation_v4");
    expect(html).toContain("hrmod_mirror_area_shift_v6");
    expect(html).toContain("RECEIVER_DOWNHILL_OVERLAP");
    expect(html).toContain("89ABCDEF");
    expect(html).toContain("HRmod candidate");
    expect(html).toContain("HRmod final");
    expect(html).toContain("Δ final спрямо clean");
  });

  it("explains a generic unavailable HRmod zone distribution", () => {
    const html = renderToStaticMarkup(
      <ShadowActivityPanel payload={{ ...payload, zone_summary: [] }} activityRef="shadow-0123456789abcdef0123456789abcdef" />,
    );
    expect(html).toContain("HRmod разпределението не е налично");
    expect(html).toContain("/?settings=edit");
    expect(html).toContain("Обнови данните");
  });

  it("shows the exact profile-range exclusion without blaming a valid zero-wave result", () => {
    const excluded = {
      ...payload,
      zone_summary: [],
      hrmod_waves: [],
      timeseries: [
        { timestamp: "2026-09-01T08:00:00Z", elapsed_s: 0, hr_raw_bpm: 78, speed_raw_kmh: 12, vflat_b65_kmh: 13 },
        { timestamp: "2026-09-01T08:00:01Z", elapsed_s: 1, hr_raw_bpm: 152, speed_raw_kmh: 12, vflat_b65_kmh: 13 },
      ],
      diagnostics: { hrmod: { flags: ["HRMOD_HR_OUTSIDE_PROFILE"] } },
    };
    const html = renderToStaticMarkup(<ShadowActivityPanel payload={excluded} activityRef="shadow-0123456789abcdef0123456789abcdef" profileHrRange={[80, 180]} />);
    expect(html).toContain("изключен само за тази активност");
    expect(html).toContain("80–180 bpm");
    expect(html).toContain("78.0–152.0 bpm");
    expect(html).toContain("не променяйте верни граници");
  });
});
