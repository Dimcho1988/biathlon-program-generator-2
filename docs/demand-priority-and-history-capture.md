# Requested views, planning and compact lifetime history

The worker previously drained up to 32 prepared projections (120-second soft
budget), including unopened pages, before checking programme work. It now drains
dashboard/learning dependencies and views requested within the existing five-minute
demand window, reserving one of those 32 turns for an unopened page when time
remains. It then checks explicit and due automatic programme jobs. When neither
claims work, the spare lane drains the ordinary bounded batch.

The new requested/background claim operations use the same SQL selection,
cooldown, lease, source revision, generation and publication guards as CLAIM.
Old workers remain compatible. All calculations still share the existing model
memory lock. A running calculation is never interrupted by a higher priority
arrival. Continuous demand is bounded by the job/time budget; background work
can be delayed further when an individual requested calculation exhausts the
soft time budget.

Separately, full synchronizations could fail during activation because the
permanent history capture copied `refresh_checkpoint` into every catalogue row.
That compressed import cache is already retained on the immutable generation
catalogue; history projections do not read it. Days with many recordings could
therefore exceed the 256 KiB history-document constraint. Capture now omits only
that duplicate field. Scientific summaries, intervals, load, dates, source/run
references and the original reusable checkpoint remain intact. The existing
size constraint, retention pins, monotonic revisions and atomic activation remain.

Validation covers lane ordering, bounded scheduling, background progress,
uninterrupted shutdown, publication fencing, requested/expired demand and retry
cooldowns. The lifetime SQL regression captures an import checkpoint larger than
the former failing day bound and asserts exact scientific fields and retained
generation cache. Database CI rebuilds all migrations and runs the existing
concurrency/retention contracts. No scientific formulas or training settings change.

Apply the backward-compatible database migrations before deploying the worker
and API. The page-worker source fingerprint changes, so prepared page caches
refresh once. Activity, dashboard and learning source identities remain unchanged.
The queue change reduces waiting behind prewarming; it does not reduce the
generator's own execution time. Earlier offline replay timings are scenarios,
not a measured guarantee for this release.
