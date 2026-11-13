import { createHash } from "node:crypto";

// This is a cache namespace, never an authorization token. Every route request
// still authorizes the current account and its selected athlete independently.
export function speedLoadCacheScope(access: { actorUserId: string; userId: string; athleteAlias: string }) {
  return createHash("sha256").update(JSON.stringify([
    "speed-load-cache-v1", access.actorUserId, access.userId, access.athleteAlias,
  ])).digest("hex");
}
