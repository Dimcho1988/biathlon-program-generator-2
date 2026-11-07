# Installable onFlows and optional password sign-in

## Behavior

onFlows can be installed from the browser as an app icon and opened in a standalone window. The navigation and login page contain an install button. When the browser provides `beforeinstallprompt`, the button opens its prompt only after a user click. Otherwise, it shows instructions for that browser. iPhone/iPad instructions use Safari → Share → Add to Home Screen; Mac Safari uses Add to Dock. Chrome/Edge installation works on supported desktops and Android. A manifest, regular 192/512 px icons, maskable 512 px icon and Apple 180 px icon use the existing onFlows brand.

This is an online application. No service worker or offline caching of athlete/API/HTML data was introduced. Only public icon assets use public cache headers. Manifest/icon routes bypass authentication refresh.

The existing Supabase SSR session already persists and refreshes through the Next.js proxy. No authentication duration was extended. Normal logout now uses `scope: local`, so it exits the current browser/app session without revoking other devices. The selected athlete remains a separate 30 day signed cookie, revalidated against account permissions; automatic renewal of this selection was not added.

Passwords are optional. Existing email magic links remain available for first login and password recovery. The login form offers email/password and email-link modes. A signed-in user can set/change their own password in Account → Вход и сигурност. Requests are same-origin POSTs, responses are private/no-store, raw passwords/tokens are not logged or returned, and only the verified current user is updated. Supabase stores the password hash; onFlows creates no password table.

Password changes require live `getUser`, a confirmed email, matching cryptographically verified claims and an actual credential authentication in the last 24 hours. JWT issue time and `token_refresh`/anonymous AMR entries cannot satisfy this check. The provider's current-password/weak-password/reauthentication rules remain in force. New passwords have a 12 character minimum and 72 UTF-8 byte maximum to avoid bcrypt truncation. No Supabase auth settings, paid plans, email templates or user passwords were changed during development.

Email PKCE links must return to the browser/profile that requested them; the form now explains this. Browser/PWA stores can be separate. WebKit documents copying cookies when installing on iOS 17.2+, with separate stores afterwards. Optional password login provides a flow entirely inside the installed app. No real-device storage-transfer guarantee is claimed.

## Validation

- Web regression suite: 424 tests across 51 files (378 existing + 46 new). ESLint, TypeScript and production build checked before staging rollout.
- New tests cover password authentication failures/rate limits, same-origin checks, verified-user isolation, stale/revoked/unconfirmed sessions, recent-credential AMR, Unicode byte bounds, provider security errors, form interactions and clearing password fields after save.
- PWA tests cover manifest scope/icons, generated PNG dimensions, install acceptance/dismissal/failure, no automatic prompt, duplicate click suppression, manual instructions, installed-mode hiding and listener cleanup. Maskable icon's full mark rectangle fits inside the central 40% radius safe circle.
- Proxy regression proves request/response cookie propagation and no-store headers using a mocked SDK callback with real NextRequest/Response. It is not an end-to-end expired-JWT refresh test.
- A new live staging browser tab retained the previous night's authenticated account without another email request; it also retained the account and selected athlete after deployment. User password creation and real iOS/Android/desktop installation require the user's own device and were not performed on their behalf.

## Verified staging rollout

PR #126 was merged after all checks passed. Staging web deployment `dep-db09ja2d0e5s73am73e0` is live on code commit `be03e29281f3e32034064d8313514bcb95f2965e`, at https://onflows-web-staging.onrender.com. API/worker code and configuration did not change in this feature rollout.

- GitHub CI passed: 424 web tests, 1121 API tests and 749 canonical/scientific tests, plus lint, TypeScript and production build.
- Live Chrome emitted the installation event and displayed “Инсталирай onFlows”; this confirms browser install promotion was available. The install confirmation itself was not accepted on the user's behalf.
- Account → Вход и сигурност displayed the optional password form and “Изход от това устройство”. Fields stayed empty; no real credential was entered or changed.
- Public manifest and all four generated PNG icons returned HTTP 200 with correct dimensions and no authentication cookies. Anonymous login returned both methods, correct username/current-password autocomplete and manifest/Apple icon metadata.
- Live synthetic requests verified invalid login input → 400, no-session password update → 401 and cross-origin update → 403. All responses were private/no-store and no account data was changed.
- No new application error logs were present between deployment becoming live and final verification.

## Primary references

- https://supabase.com/docs/guides/auth/server-side/creating-a-client
- https://supabase.com/docs/guides/auth/passwords
- https://supabase.com/docs/guides/auth/password-security
- https://supabase.com/docs/guides/auth/jwt-fields
- https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable
- https://web.dev/articles/install-criteria
- https://nextjs.org/docs/app/api-reference/file-conventions/metadata/manifest
- https://webkit.org/blog/14787/webkit-features-in-safari-17-2/

Checked current Supabase changelog before implementation. Passkeys are in beta and were not added in this iteration. No new runtime dependencies or infrastructure services.
