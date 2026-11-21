# Automatically published training weeks

The planning engine remains the authority for eligibility, workload, methods,
recovery, periodization and available time. A valid generated program becomes
the current program without a separate coach-approval step. This applies to
initial generation and subsequent valid adaptations.

The stored `adaptation_mode` values remain compatible. They control the planning
window, not whether the user must approve the result:

| Value | Window | Publication |
| --- | --- | --- |
| `AUTO` | Rolling future window | Valid future prescriptions update automatically. |
| `REVIEW` | Week with a fixed end | Valid future prescriptions update within that window; the next window follows after its end. |

Changing a profile setting, calendar event or supported method preference is an
input to the same canonical generator. The assistant cannot directly invent
validated doses. Clear reversible edits use the application's existing
authorized commands and return the actual saved result. An ambiguous request
needs only the information necessary to determine its target and scope.

Publication retains revision checks and a checkpoint of the source inputs. A
concurrent edit invalidates an outdated calculation. Draft calculation and
current-program publication are atomic when a background generation job
completes. A result that fails the engine's eligibility checks remains
unpublished with its concrete reason; an approval click cannot override that
failure.

Existing draft-only profiles enter the worker's generation flow and are
revalidated against current inputs. A deployment does not blindly promote old
draft payloads. Paused programs stay paused. Background work does not restart a
completed program; an explicit request to generate a new valid period can start
it. Prior execution/history and explicit day decisions remain available.

The weekly page shows the last saved program while a fresh view is being
prepared. Its update notice distinguishes that saved program from fresh model
predictions. The assistant carries the same freshness information. A pending
page no longer hides existing work behind an indefinite full-screen loader.

Legacy approval endpoints and stored approval fields can remain for backward
compatibility and provenance. They do not define a required step in the normal
user flow.
