# Assistant timing and preparation status

The assistant request log now separates authorized data reads, provider payload
construction, budget reservation/settlement, HTTP transport, and reply parsing.
`assistant_model` encloses the provider call; its children overlap it and must
not be added to it. Transport includes network and remote generation time; it
cannot separate provider queueing from generation. Fixed labels contain no
prompts, athlete identifiers, API keys or returned data. Existing model, token,
request-count and financial limits are unchanged.

Profile availability interpretation is one pure canonical service used by both
prepared HTTP views and conversational reads. Raw editable profile values are
preserved. AUTO_HISTORY, explicit MANUAL, permitted weekdays and independent
planning constraints retain their previous meanings.

Management pages poll a scoped, no-store status route while preparation is
pending, including initial loading and saved-program fallback. The SQL STATUS
operation shares READ's atomic input revision, local date, model-key and dirty
event checks. It selects no scientific payload. The browser waits 5–30 seconds
between completed polls, skips hidden tabs, aborts abandoned requests and stops
on lost access/scope. A ready transition triggers one page refresh. Transient
network errors retry; after 15 minutes a manual retry remains. Week and overview
variants retain distinct preparation identities.

Validation includes status/READ parity through real source/date/model changes,
service-only grants, denied browser scopes, timeout/unmount behavior, and shared
profile interpretation. A 100-poll synthetic SQL cohort checks bounded response
size; it is not a capacity estimate for 100 simultaneous live athletes. Real
capacity testing requires a separate controlled workload and must not consume
athlete model budgets or change live training inputs.

## Saved planning diagnostics

The internal onFlows assistant exposes `management`, `view=diagnostics` only to
readers with both plan and recovery access. Its default response is a section
index. Explicit `section` reads expose the saved allocation and search report,
daily load constraints and rejected alternatives, complete saved dose evidence,
and frozen method source cards. Follow-up pages carry the index's `revision`;
a changed publication returns 409 instead of combining different plans.
Current and candidate plans retain separate roles. Reads neither recalculate a
plan nor substitute the current method catalog. The existing public projection
still removes private learning observations. Ordinary week/day reads stay compact.

The ChatGPT onFlows connector is a separate surface. Its current tools expose
the application guide, connected athlete, saved training day, race list and an
authorized race-date change. It does not expose this diagnostics view. This
change does not extend connector permissions or add connector tools.
