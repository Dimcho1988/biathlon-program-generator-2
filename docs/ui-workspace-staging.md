# onFlows workspace UI — staging

The sidebar now lives in the root layout and remains available across analysis pages, activity details, loading and error states. On narrow screens it becomes a labelled Menu disclosure; Escape closes it and restores focus. Athlete selection still uses the existing protected account flow.

The home page defaults to a concise overview. Separate URL-addressable views expose load/volume, Recovery, the completed-work report and technical details. Readiness remains per component, with no invented overall score. The overview uses the existing Recovery values and reports the source date. Its seven-day volume sums recorded activity durations in the last seven calendar days ending at the analysis period end; unknown durations are marked incomplete. Trainability and subjective response have direct links to their full views.

The sidebar remembers only whitelisted date/view parameters in tab-local sessionStorage. It stores no athlete identifiers or credentials. Report dates survive view changes and the existing refresh redirect. All scientific calculations, source channels, permissions, generation activation and training data contracts remain unchanged.

Performance changes:

- Removed the unconditional browser round trip through `/api/v2/wake` on every home visit. Existing server readiness probing and bounded retries remain; OAuth retains its own wake flow.
- Account name/roles load only in technical details. React `cache` shares the selected-athlete authorization query between layout and page within one server render, never across requests/users.
- Non-critical sync status has one bounded ten-second read after readiness instead of potentially delaying the whole page for repeated long requests.
- Only the selected dashboard view is rendered. The persistent sidebar disables eager prefetch for heavy analysis destinations.
- The user's painting is supplied as a 300,082-byte WebP background, with light/dark overlays and solid chart panels. The original painting is not altered.

Validation before staging: web tests (177), ESLint, TypeScript and Next.js production build. Navigation regressions cover the original disappearing-sidebar case, date retention, untrusted stored URLs and refresh return destinations. Live desktop/mobile-layout verification follows deployment.

Hosting: on 2026-09-15 both `onflows-web-staging` and `onflows-api-staging` use Free compute; the staging sync worker already uses Starter. Render documents idle spin-down after 15 minutes and about one minute to wake: https://render.com/docs/free. A workspace subscription alone does not remove Free compute limitations. No billing change is included in this UI deployment.
