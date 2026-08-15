# onFlows web dashboard

Next.js App Router frontend for the existing `training-status-v1` API contract. The frontend validates and presents model output; it does not calculate physiological values.

## Local development

Requires Node.js 22 and npm.

```bash
cd apps/web
npm ci --no-audit --no-fund
ONFLOWS_DATA_MODE=fixture npm run dev
```

Open <http://localhost:3000>. Fixture mode is selected **only** when `ONFLOWS_DATA_MODE=fixture` and is visibly labelled **„Демо данни“**.

## Branding and themes

The header theme control switches between the complete light and dark palettes. Before React paints, the application restores the manual choice from the `onflows-theme` local-storage key; when no choice exists, it follows `prefers-color-scheme`. Semantic theme tokens for surfaces, text, states, shadows, and zones are centralized in `app/globals.css`. The transparent official logo is stored at `public/brand/onflows-mark.png`, with the application icon generated from the same asset.

## FastAPI mode

Start the repository's FastAPI service separately, then provide its origin:

```bash
# repository root, separate terminal
uvicorn apps.api.main:app --reload

# apps/web
ONFLOWS_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

When fixture mode is not explicitly selected, `ONFLOWS_API_BASE_URL` is required. The server fetches `/api/v1/demo/training-status` with an eight-second timeout. Request, HTTP, JSON, and contract-validation failures render an error and never fall back to the fixture.

## Environment variables

| Variable | Value | Purpose |
| --- | --- | --- |
| `ONFLOWS_DATA_MODE` | `fixture` | Explicitly use the deterministic local contract fixture. Omit for API mode. |
| `ONFLOWS_API_BASE_URL` | e.g. `http://127.0.0.1:8000` | FastAPI origin used in API mode. |

## Verification

```bash
npm run lint
npm run type-check
npm test
npm run build
```
