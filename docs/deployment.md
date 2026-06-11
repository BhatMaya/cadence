# Cadence Deployment

This repo remains the source of truth. The GitHub deployment worktrees are
updated with `scripts/sync_deployments.sh` and checked with
`scripts/check_deployments_synced.sh`.

## Backend: Render

Use the backend deployment worktree at `../cadence_github/cadence`.
`render.yaml` defines the Render web service and a small Redis-compatible
store for shared rate limiting.

Required manual values in Render:

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `CADENCE_ADMIN_TOKEN`
- `CADENCE_RSA_PRIVATE_KEY`
- `CADENCE_CORS_ORIGINS`
- `RESEND_KEY`
- `RESEND_FROM_EMAIL`
- `DATABASE_URL` for retention jobs

Operational settings:

- Keep `CADENCE_DEMO_MODE=0`.
- Keep `CADENCE_ALLOW_OPEN_ADMIN=0`.
- Confirm `CADENCE_RATE_LIMIT_STORAGE_URI` is populated from
  `cadence-rate-limit`.
- Confirm the health check path is `/health`.
- Apply schema changes in the Supabase SQL editor before deploying
  backend code that depends on them. `backend/schema.sql` is a legacy
  local bootstrap file, not the production schema source of truth.

## Frontend: Vercel

Use the frontend deployment worktree at `../github_cadence/cadence`.
Configure the Vercel project with Root Directory `frontend`; the
`frontend/vercel.json` file then sets the Next.js framework, `npm ci`, and
`npm run build`. Vercel documents Root Directory as a project setting for
monorepos, while `vercel.json` lives in the selected project root and can
override framework and build commands.

Required Vercel environment variables:

- `SYNERGYZE_API_BASE=<deployed Render API base URL>`
- `CADENCE_API_KEY=<Synergyze application sk_live_... API key>`

The developer portal must be reachable by external integrators. Disable
Vercel Deployment Protection for the production deployment, or configure a
public custom domain and an automation bypass token for smoke tests. A
plain request to `/developer` should return the app, not Vercel's
authentication page.

After deployment, open:

- `/` for the Synergyze demo flow.
- `/developer` for public app registration, request lookup, and operator
  administration.

## Release Order

1. Run the backend tests, package tests, and frontend build locally.
2. Run `bash scripts/sync_deployments.sh`.
3. Run `bash scripts/check_deployments_synced.sh`.
4. Commit and push the source, backend deployment, and frontend deployment
   worktrees.
5. Apply any required schema changes in the Supabase SQL editor.
6. Let Render and Vercel deploy from their GitHub deployment repos.
7. Verify the frontend is publicly reachable:

   ```bash
   curl -I https://<frontend-domain>/developer
   ```

   The response should be `200`, not a Vercel authentication `401`.
8. Run the platform smoke test:

   ```bash
   CADENCE_API_BASE=https://api.example.com \
   CADENCE_ADMIN_TOKEN=<admin-token> \
   python scripts/smoke_platform_api.py
   ```

9. Publish `@cadence-auth/cadence` when the API and frontend smoke tests pass.
