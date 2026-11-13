# Prepared calculation identities

Prepared dashboard, learning and page results use three independent release
identities. `RENDER_GIT_COMMIT` remains deployment provenance and is no longer a
calculation input. The database continues to fence every result by athlete,
input revision, local date, source generation and worker lease.

`apps/api/preparation_identity.py` declares the calculation roots for each
domain. It follows local Python imports, including imports inside functions,
and hashes the normalized syntax trees. A change to an imported calculation
therefore invalidates the dependent domains without relying on a manually
updated version constant. Comments and docstrings do not change the identity.
The two checked-in training-method catalogs are included only in domains that
import their readers. Numerical-library/Pydantic versions and the Recovery-v2
feature flag are also inputs.

The source-adapter and prepared HTTP contracts are hashed as terminal modules:
their source affects the contract, but their unrelated authentication, worker
and write-path imports do not pull the assistant into the calculation graph.
Authentication, transport, scheduling and telemetry are explicit traversal
boundaries. Access decisions are still made for every request.

Fingerprints are computed once before API readiness and reused for the process
lifetime. Only small module digests/import lists and three domain identities
are cached; no athlete state is cached here. An immutable deployment/restart is
required after source or numerical-library changes. A missing declared source
or catalog fails startup instead of accepting an incomplete identity.

The dependency tests cover all prepared callbacks and their local helpers,
new transitive imports, scientific catalog edits, configuration changes and
unrelated chat/frontend deployments. New calculation entry points belong in
`_ROOTS`; new static scientific assets belong in `_ASSETS`.

Granularity is intentionally conservative at module/domain level: changing a
shared adapter contract can refresh several domains, and changing any page
calculation refreshes the page domain. Symbol-level hashing or separate queue
keys for each page would require additional complexity. The first deployment
of this identity format requires one refresh of existing results; subsequent
chat/frontend-only deployments retain their identities.
