# Fixed and adaptive weeks

Policy `fixed-or-adaptive-v1`, engine `training-management-v56-fixed-weeks`.

The profile's existing `adaptation_mode` storage values remain compatible:

| Value | Interface | Publication |
| --- | --- | --- |
| `AUTO` | Адаптивна седмица | Eligible future work adapts automatically; changed rules require approval. |
| `REVIEW` | Фиксирана седмица | Approved dates, methods, blocks, intensities and doses stay unchanged until explicit approval. |

`week_window` records the approved start/end. A fixed week's candidate is generated from today through that same end. The ordinary generator, method limits, taper, recovery and component budgets are reused; there is no alternative dosing algorithm. Its whole-microcycle lookahead remains provisional and is not part of the approved week.

The worker still reconciles real activity, including additional work, and evaluates the current inputs. It saves fresh `monitoring` separately from the original `plan`. Identical eligible remaining prescriptions need no reapproval. Changed candidates, incomplete coverage, illness/learning holds and rule changes require review. A changed candidate is a reason for review, not proof that the original dose is physiologically unsafe. Monitoring forecasts describe the proposed schedule; forecasts inside the preserved plan describe its original approval, not current readiness.

The main calendar and compact overview select `plan` even when `proposal` exists. Proposals have their own section, reasons, dose summary and eligibility gate. Current actual history comes from monitoring, while planned volume remains from approved prescriptions. HR and speed Q are not added together, and speed Q remains comparison-only.

The daily view renders only the approved week. A pending proposal is a compact notice with its reason and a “Прегледай” button. Opening it shows affected days, approved/proposed doses, optional full task comparisons and a colocated “Утвърди предложението” action. Unchanged days and the full proposed summary are available on demand. Proposal details are mounted only when opened; the API payload and calculation policy are unchanged. A new revision closes the previous review, so an updated proposal must be opened again. Warnings remain outside the collapsed review in both modes, and blocked approval explains the required next step. Adaptive changes already published are labeled separately from proposals awaiting approval.

Approval promotes the exact fresh proposal after revision, profile, analysis and response checks. A correction within a fixed week retains its original end. Past work stays in immutable revisions and reconciled outcomes. A recording today is reported immediately, without declaring the day complete or erasing the original prescription on the next refresh. Calendar-day matching is not proof of exact workout completion; partial multiple-session days still require review.

After a fixed week ends, the next seven-day window is anchored to the previous end and proposed from current actual data. Approval is required. Delayed approval does not move its end. After longer absence, expired windows are skipped without catch-up work. Pause and program completion retain their existing guards.

The week header explicitly separates the **mode of the approved programme** from the **saved choice in the profile**. A mismatch remains visible until approval, with the next step for refresh, review, pause or completion. Review-required, stale and paused states never claim that automatic adaptation is currently taking place. Profile editing distinguishes an unsaved choice from the saved choice and explains that saving a mode does not switch an existing approved programme. A no-change refresh receipt preserves the reminder that an existing proposal still awaits review.

The header reuses the profile already loaded with the week. A newer versioned profile frozen in a subsequently received plan/proposal supersedes older initial page props for this display only. Legacy proposal mode is read from its own frozen profile when the explicit field is missing, never from a different current profile. No extra requests, planning calculations, approval policy, or stored modes are changed.

Changing the profile mode previews the effect first; the active mode changes only on approval. Existing `REVIEW` records derive the first fixed window from their approved plan. Existing `AUTO` records remain adaptive. No athlete is silently switched to fixed mode.

Validation covers immutable doses under extra/unplanned load, freshness without changes, same-day recording, late imports, mode changes in both directions, fixed end after delayed approval, missing-history rejection, explicit day decisions, duplicate refreshes, bounded generation, and approved-versus-proposed UI/SQL projections. Database fixtures run in a rollback transaction and preserve service-only permissions.
