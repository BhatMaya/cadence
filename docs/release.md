# Cadence Release Checklist

This repo is the source of truth. The sibling GitHub worktrees are
deployment copies and should be updated from this checkout before they
are pushed.

## One-time production setup

1. Create or claim the npm organization scope for
   `@cadence-auth/cadence`.
2. Make sure the machine that will publish has npm access to
   `@cadence-auth/cadence`.
3. Provision production Supabase and apply the schema:

   ```bash
   DATABASE_URL=<postgres-url> bash scripts/apply_schema.sh
   ```

4. Configure backend secrets from `backend/.env.example`, including
   `DATABASE_URL`, `CADENCE_ADMIN_TOKEN`, `CADENCE_RSA_PRIVATE_KEY`,
   `RESEND_FROM_EMAIL`, `CADENCE_CORS_ORIGINS`, and
   `CADENCE_RATE_LIMIT_STORAGE_URI`. Use a `redis://` or `rediss://`
   rate-limit storage URL, and confirm `CADENCE_ALLOW_OPEN_ADMIN` is
   unset or `0`.
5. Configure the Render and Vercel projects from `docs/deployment.md`.
6. Configure frontend deployment with `SYNERGYZE_API_BASE=<backend-url>`
   and the Synergyze app's server-side `CADENCE_API_KEY`.

## Per-release flow

1. Update the source checkout and confirm the package version in
   `packages/capture/package.json`.
2. Run the normal local checks:

   ```bash
   SUPABASE_URL=http://localhost SUPABASE_KEY=test \
     python -m unittest discover backend/tests
   cd packages/capture && npm test && npm pack --dry-run
   cd ../../frontend && npm run build
   ```

3. Sync deployment worktrees:

   ```bash
   bash scripts/sync_deployments.sh
   bash scripts/check_deployments_synced.sh
   ```

4. Review all three worktrees:

   ```bash
   git -C . status --short
   git -C ../cadence_github/cadence status --short
   git -C ../github_cadence/cadence status --short
   ```

5. Commit and push the GitLab source tree, the backend deployment tree,
   and the frontend deployment tree.
6. Apply any production schema changes before deploying backend code:

   ```bash
   DATABASE_URL=<postgres-url> bash scripts/apply_schema.sh
   ```

7. Publish the npm package after tests pass and the desired version is
   final:

   ```bash
   bash scripts/publish_package.sh --dry-run
   NPM_TOKEN=<token> bash scripts/publish_package.sh
   ```

   The script checks the npm registry first and fails if the exact
   package version already exists.

## Smoke tests

After deploy, verify:

1. `GET /health` returns a healthy response.
2. `GET /model/health` reports loaded model state.
3. The frontend `/developer` page loads against the deployed API and is not
   blocked by Vercel Deployment Protection.
4. A developer registration request can be submitted.
5. An operator can approve the request and receive the first
   `sk_live_...` key once.
6. The Synergyze proxy can call `/signup`, `/authenticate`,
   `/code_verification`, `/resend_code`, `/password/change`, and
   `/users/unblock` with the configured `CADENCE_API_KEY`.
7. Email security links for `/unblock-login/<login_attempt_id>` and
   `/report-fraud/<login_attempt_id>` render public confirmation pages.

Run the API flow with:

```bash
CADENCE_API_BASE=https://api.example.com \
CADENCE_ADMIN_TOKEN=<admin-token> \
python scripts/smoke_platform_api.py
```

The smoke test creates a registration, verifies its lookup token before
and after approval, verifies operator usage counts, and revokes the
generated key unless `--keep-key` is set.

## Rollback notes

- Backend schema changes are written to be idempotent, but rollback
  should usually deploy the prior backend code rather than dropping
  columns.
- API keys are only shown once. If a partner loses a key after rollback,
  create a new key and revoke the old one.
- npm versions are immutable. Publish a new patch version instead of
  trying to overwrite a broken package.
