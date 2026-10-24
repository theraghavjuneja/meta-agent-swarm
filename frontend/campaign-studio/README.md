# Campaign Studio

React frontend for Campaign Studio, built from the frontend specification: React 18 + Vite, React Router v6, React Query (TanStack Query) for server state, Tailwind CSS, and react-hook-form + zod for the create form.

## Setup

```bash
npm install
npm run dev
```

The app runs at `http://localhost:5173` and talks to the backend at `http://localhost:8000` (configured in `.env` via `VITE_API_BASE_URL`). Change that value if your backend runs elsewhere.

Make sure the backend is running before loading the app — every page fetches from it on mount.

## What's implemented

- **Routing** — the full route map from the spec: `/campaigns`, `/campaigns/new`, `/campaigns/:id` (redirects to `research`), and the four nested tabs (`research`, `spec`, `assets`, `usage`), plus a catch-all 404.
- **Campaign list** — status filter and offset pagination synced to the URL query string, skeleton/empty/error states.
- **Campaign create** — client-side validation mirroring the backend's field limits, a repeatable verified-claims input, and an optional reference image upload (JPEG/PNG/WEBP, 5MB max). Submits as `multipart/form-data` when an image is attached, otherwise JSON.
- **Research tab** — steps timeline, sources list, and angle cards with a "select this angle" action; hides once an angle is already selected.
- **Creative spec tab** — read-only spec view with palette swatches; shows a pending state until the spec exists (backend 404).
- **Assets tab** — media grid with per-asset retry (with a confirmation step), disabled with an explanatory tooltip when an asset is already in progress or its dependent hero image hasn't completed yet.
- **Usage tab** — cost summary cards and a per-row usage table.
- **Polling** — campaign, research, and asset queries poll every 5 seconds via React Query's `refetchInterval` while the campaign (or the specific run/asset) is still in progress, and stop once it reaches a terminal state.
- **Global concerns** — persistent sidebar nav, toasts via `sonner` (success on create/select-angle, error messages read from the API's `message`/`detail` field), route-level error boundary with a "Try again" action, and standardized skeleton/empty states.

## Notes / assumptions

- The backend has no authentication (`main.py:108`), so no auth handling is implemented, as specified.
- The list view is read-only — there's no delete/archive/edit endpoint in the spec, so none is wired up.
- `AssetsResponse` doesn't include an explicit "hero image required" flag, so the assets page infers it: any non-hero asset's retry is disabled until the campaign's `hero_image` asset has `status: completed`. The backend's 409 response is still the source of truth and is surfaced as a toast if it fires anyway.
