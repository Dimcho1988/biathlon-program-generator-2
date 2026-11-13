import { expect, it } from "vitest";
import { executionGuidance } from "../lib/execution-guidance";
import type { SessionBlock } from "../lib/training-management";

it("shares only identical guidance and preserves distinct targets and lactate ranges", () => {
  const base: SessionBlock = { kind: "WORK", label: "Повторение", zone: "Z3", duration_min: 5,
    target_hr_bpm: 160, target_speed_kmh: 18, repetition: 1, instructions: "Запази ритъма.",
    lactate_reference: { low_mmol: 2, high_mmol: 4, source: "MANUAL", label: "Личен", assessed_on: null } };
  const blocks = [base, { ...base, repetition: 2, target_speed_kmh: 19 },
    { ...base, repetition: 3, instructions: "Намали темпото.",
      lactate_reference: { ...base.lactate_reference!, high_mmol: 3 } }];
  const before = JSON.stringify(blocks);
  const result = executionGuidance(blocks);
  expect(result.common).toEqual([{ zone: "Z3", text: "Запази ритъма.", count: 2 }]);
  expect(result.blocks).toEqual([{ showInstructions: false, showLactate: true },
    { showInstructions: false, showLactate: false }, { showInstructions: true, showLactate: true }]);
  expect(JSON.stringify(blocks)).toBe(before);
});
