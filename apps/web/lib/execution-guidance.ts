import type { SessionBlock } from "./training-management";

const instructionKey = (block: SessionBlock) => JSON.stringify([block.kind, block.zone, block.instructions]);

/** Share identical prose, while keeping every block and its exact targets. */
export function executionGuidance(blocks: SessionBlock[]) {
  const groups = new Map<string, { zone: string; text: string; count: number }>();
  for (const block of blocks) {
    if (!block.instructions) continue;
    const key = instructionKey(block);
    const group = groups.get(key);
    if (group) group.count++;
    else groups.set(key, { zone: block.zone, text: block.instructions, count: 1 });
  }
  const common = [...groups.entries()].filter(([, group]) => group.count > 1);
  const shared = new Set(common.map(([key]) => key));
  const lactates = new Set<string>();
  return {
    common: common.map(([, group]) => group),
    blocks: blocks.map(block => {
      const key = JSON.stringify([block.zone, block.lactate_reference]);
      const showLactate = Boolean(block.lactate_reference) && !lactates.has(key);
      lactates.add(key);
      return { showInstructions: !shared.has(instructionKey(block)), showLactate };
    }),
  };
}
