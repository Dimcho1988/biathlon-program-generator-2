# onFlows workspace UI — staging

The sidebar now lives in the root layout and remains available across analysis pages, activity details, loading and error states. On narrow screens it becomes a labelled Menu disclosure; Escape closes it and restores focus. Athlete selection still uses the existing protected account flow.

The home page defaults to a concise overview. Separate URL-addressable views expose load/volume, Recovery, the completed-work report and technical details. Readiness remains per component, with no invented overall score. The overview uses the existing Recovery values and reports the source date. Its seven-day volume sums recorded activity durations in the last seven calendar days ending at the analysis period end; unknown durations are marked incomplete. Trainability and subjective response have direct links to their full views.

The sidebar remembers only whitelisted date/view parameters in tab-local sessionStorage. It stores no athlete identifiers or credentials. Report dates survive view changes and the existing refresh redirect. All scientific calculations, source channels, permissions, generation activation and training data contracts remain unchanged.

Performance changes:

- Removed the unconditional browser round trip through `/api/v2/wake` on every home visit. Existing server readiness probing remains; a failed cold start uses the existing browser wake flow once. OAuth retains its own wake flow.
- Account name/roles load only in technical details. React `cache` shares the selected-athlete authorization query between layout and page within one server render, never across requests/users.
- Non-critical sync status has one bounded ten-second read after readiness instead of potentially delaying the whole page for repeated long requests.
- Only the selected dashboard view is rendered. The persistent sidebar disables eager prefetch for heavy analysis destinations.
- The user's painting is supplied as a 300,082-byte WebP background, with light/dark overlays and solid chart panels. The original painting is not altered.

Validation before staging: web tests (177), ESLint, TypeScript and Next.js production build. Navigation regressions cover the original disappearing-sidebar case, date retention, untrusted stored URLs and refresh return destinations. Live desktop/mobile-layout verification follows deployment.

Hosting: on 2026-09-15 both `onflows-web-staging` and `onflows-api-staging` use Free compute; the staging sync worker already uses Starter. Render documents idle spin-down after 15 minutes and about one minute to wake: https://render.com/docs/free. A workspace subscription alone does not remove Free compute limitations. No billing change is included in this UI deployment.

Live verification: the authenticated overview and Trainability page render with the persistent menu; the menu remains usable during loading. Selecting 1–15 September on Trainability, returning home and reopening the index preserves that period. Both light and dark themes were inspected. The staging Render web deployment of PR #62 is live. A follow-up aligns the inherited activity-header margins and adds slow-request timing (resource name and elapsed milliseconds only) to separate API readiness, data reads and account access. No identifiers, query strings or credentials are logged. The cloud browser does not expose viewport resizing; the narrow-screen CSS/disclosure implementation still needs a physical-phone spot check.

Cold-start correction, 2026-09-15: after the API spun down at 17:38 UTC, Render returned HTTP 429 to web-server health probes. Removing the old browser wake step exposed this failure; warm navigation checks had not reproduced it. The existing public `/api/v2/wake` browser flow restored access to the real dashboard. Failed starts now offer that flow, with one automatic attempt per 15-minute window and a manual retry. The return section/dates stay in tab-local storage and are validated before restoration. If storage is blocked, automatic cross-origin navigation is disabled to prevent loops. A health-probe 429 is preserved and does not trigger protected-resource retries or an extra settings query. The error page no longer claims to display an active version when no data loaded, and does not suggest enqueueing a data sync to wake the service. The API, calculations and billing are unchanged.
