# Cadence

Keystroke-dynamics second factor: a Python/Flask backend, a TypeScript
capture library, a small mock SaaS frontend ("Synergyze") that exercises
both, and a Keras siamese model that scores a fresh login attempt
against the user's prior successful samples.

```
cadence/
├── backend/           # Flask API (auth, 2FA, ML scoring)
├── frontend/          # Next.js mock landing + register/login UI
├── packages/capture/  # browser keystroke capture library
├── model.py           # siamese network architecture
├── train.py           # training loop
├── models/            # checkpointed weights + metrics
└── scripts/setup.sh   # local dependency setup
```

## Running Locally

The backend uses Supabase for auth + Postgres. To run end-to-end on a
single machine, the easiest grading path is to point `backend/.env` at
the provisioned Supabase project. The checked-in `backend/schema.sql` is
a legacy local bootstrap file; the current database schema was applied in
the Supabase SQL editor and should not be re-applied to the provisioned
project from this repo.

### Prerequisites

| Tool | Purpose | Install |
| --- | --- | --- |
| Python 3.12 | backend runtime (`backend/.python-version` pins 3.12.10) | `brew install python@3.12` / your distro's package |
| Docker | hosts the local Supabase stack | https://docs.docker.com/get-docker/ |
| Node.js/npm | frontend runtime and Supabase CLI fallback | https://nodejs.org/ |
| Supabase CLI | manages an optional local Supabase stack | Optional if using the provisioned Supabase project |
| `psql` | inspects or bootstraps an isolated local database | Optional |

### Fastest Grading Path

Create `backend/.env` with the project credentials, then install and run
the backend and frontend:

```bash
cat > backend/.env <<'EOF'
SUPABASE_URL=<provided-supabase-url>
SUPABASE_KEY=<provided-service-role-key>
RESEND_KEY=
CADENCE_DEMO_MODE=1
CADENCE_ALLOW_OPEN_ADMIN=1
CADENCE_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
EOF

cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -c "from app import app; app.run(host='127.0.0.1', port=5001)"
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>, register, and sign in. With
`CADENCE_DEMO_MODE=1`, verification codes are returned to the UI instead
of being emailed, which keeps local grading deterministic.

### Optional Isolated Supabase Setup

From the repo root:

```bash
bash scripts/setup.sh
```

The script:

1. Verifies prerequisites and that the Docker daemon is responding.
2. Creates `backend/.venv` and installs `requirements.txt`
   (TensorFlow makes this slow on first run).
3. Runs `supabase init` / `supabase start`, falling back to
   `npx supabase` if the CLI is not installed globally.
4. Skips `backend/schema.sql` by default because it is not the
   authoritative project schema.
5. Writes `backend/.env` with the local Supabase URL + service-role
   key, `CADENCE_DEMO_MODE=1`, `CADENCE_ALLOW_OPEN_ADMIN=1`, and local
   frontend CORS origins, so 2FA codes are returned in the API response
   (and shown in the UI banner) instead of emailed.

It's idempotent — safe to re-run after pulling.

For a throwaway local database only, opt into the legacy bootstrap SQL:

```bash
CADENCE_APPLY_LOCAL_SCHEMA=1 bash scripts/setup.sh
```

Do not use `scripts/apply_schema.sh` against the provisioned Supabase
project unless the SQL has first been refreshed from the live schema.

### Running the stack

Two terminals:

```bash
# 1. Backend (Flask, port 5001)
cd backend
source .venv/bin/activate
python -c "from app import app; app.run(host='127.0.0.1', port=5001)"

# 2. Frontend (Next.js, port 3000)
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>, register, and sign in. The Synergyze demo owns
the browser UI, then sends requests through its Next.js proxy route to the
backend's app-scoped `/signup`, `/authenticate`, `/code_verification`,
`/resend_code`, `/logout`, `/password/change`, and `/users/unblock`
endpoints.

### Useful commands

```bash
supabase status                 # show local URLs + keys
npx supabase status             # same, if the CLI is not installed globally
supabase stop                   # tear down the Docker stack
supabase stop --no-backup       # nuke the local Postgres data too

# legacy local-only schema bootstrap
CADENCE_APPLY_LOCAL_SCHEMA=1 bash scripts/setup.sh

# check the deployment folders are in sync before pushing them
bash scripts/check_deployments_synced.sh

# smoke-test a deployed platform API flow
CADENCE_API_BASE=https://api.example.com CADENCE_ADMIN_TOKEN=<admin-token> \
  python scripts/smoke_platform_api.py

# inspect the local DB
psql "$(supabase status -o env | sed -n 's/^DB_URL=//p' | tr -d '"')"

# or without local psql
docker exec -it "$(docker ps --format '{{.Names}}' | grep '^supabase_db_' | head -n 1)" \
  psql -U postgres -d postgres
```

### Going off demo mode

Demo mode short-circuits the email send and surfaces the OTP in the
API response — never enable in production. To use real email, set in
`backend/.env`:

```
CADENCE_DEMO_MODE=0
RESEND_KEY=<your resend api key>
```

Free-tier Resend only delivers to the email tied to your Resend account
until you verify a sending domain at <https://resend.com/domains>.

For deployed environments, start from `backend/.env.example`. Production
should set a stable `CADENCE_RSA_PRIVATE_KEY`, a non-empty
`CADENCE_ADMIN_TOKEN`, real Resend credentials, explicit
`CADENCE_CORS_ORIGINS`, and shared rate-limit storage through
`CADENCE_RATE_LIMIT_STORAGE_URI` using a `redis://` or `rediss://` URL.
Do not set
`CADENCE_ALLOW_OPEN_ADMIN=1` outside local development.

Deployment details for the Render backend, Vercel frontend, and sibling
GitHub worktree sync are in `docs/deployment.md`.

### Deployment Repositories

This GitLab checkout is the source-of-truth submission repository. The
deployed apps are hosted from two GitHub deployment repositories:

- Frontend on Vercel: <https://github.com/aryamanrtunjay/cadence.git>
- Backend on Render: <https://github.com/BhatMaya/cadence.git>

## App-scoped API quickstart

Cadence can also run as a platform API for other applications. A
confirmed developer account creates an application, gets a server-side
API key, and the integrating app calls the app-scoped password auth
routes with typing samples captured by the npm package.

Open `/developer`, create a developer account, confirm the Supabase email,
then sign in and register an application. Cadence creates the application
and returns the first `sk_live_...` key immediately. Store that key only in
trusted server-side code.

```bash
curl -X POST "$CADENCE_API_BASE/v1/developer/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"dev@example.com","password":"..."}'

curl -X POST "$CADENCE_API_BASE/v1/developer/apps" \
  -H "Authorization: Bearer <developer-access-token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Partner App","allowed_origins":["https://app.example.com"],"key_name":"production"}'
```

The admin-token-gated endpoints still exist for operator review, support,
and manual app/key management. `CADENCE_ADMIN_TOKEN` is not required for
normal developer onboarding.

For the Synergyze demo frontend, set `SYNERGYZE_API_BASE` for the deployed
API base and `CADENCE_API_KEY` for the Synergyze app's server-side API key.
The browser posts to `/api/synergyze/*`; that Next.js route attaches the
API key and forwards to the backend.

Use the generated `sk_live_...` key only from trusted server-side code.
Browser code should use `createCapture` or `createPasswordAuthController`
to collect a `Sample`, then post that sample to the application's own
backend or proxy.

```ts
import { createCadenceAuthClient } from '@cadence-auth/cadence';

const cadence = createCadenceAuthClient({
  apiBaseUrl: process.env.CADENCE_API_BASE!,
  apiKey: process.env.CADENCE_API_KEY!
});

await cadence.signup({
  email: 'user@example.com',
  username: 'alice',
  password: 'correct horse battery staple'
});

const result = await cadence.authenticate({
  username: 'alice',
  password: 'correct horse battery staple',
  raw_data: sample
});

if (result.status === 'accepted') {
  console.log('Logged in');
}

await cadence.changePassword({
  username: 'alice',
  current_password: 'correct horse battery staple',
  new_password: 'much better horse battery staple!2'
});

await cadence.unblockUser({ username: 'alice' });
```

See `backend/ENDPOINTS.txt` for the full API notes,
`packages/capture/README.md` for npm package usage, and
`docs/release.md` for the production release and npm publishing
checklist.

## Project layout details

- **`backend/app.py`** — Flask application setup, shared configuration,
  Supabase clients, rate limits, CORS, and route-section loading.
- **`backend/auth_flow_endpoints.py`** — app-scoped user auth routes:
  `/signup`, `/authenticate`, `/logout`, `/code_verification`,
  `/resend_code`, `/password/change`, `/users/unblock`, and the email
  recovery/reporting pages.
- **`backend/developer_portal_endpoints.py`** — developer signup/login,
  self-serve app creation, API key management, and manual app
  registration review routes.
- **`backend/platform_endpoints.py`** — health checks, admin
  app/key operations, app threshold changes, and app-scoped user status
  support endpoints.
- **`backend/internal_helpers.py`** — shared auth-flow helpers for OTP
  email, fraud/unblock links, password policy, replay detection,
  login-attempt creation, and model scoring glue.
- **`backend/model_service.py`** — wraps the Keras siamese model;
  fetches a user's prior successful samples from
  `public.login_attempts`, normalizes both sides, runs them through the
  twin towers, and returns the mean similarity.
- **`packages/capture/`** — TypeScript/ESM package that captures
  `keydown`/`keyup` timings into a `Sample` payload, extracts timing
  features, and exposes typed Cadence auth helpers. The frontend imports
  the prebuilt dist from `frontend/vendor/`.
- **`frontend/`** — Next.js app with client-side routes
  (`/`, `/register`, `/login`, `/recover`, `/twofa`, `/dashboard`).
  Synergyze browser requests go through `/api/synergyze/*`; that
  server-side route forwards to `SYNERGYZE_API_BASE` and attaches
  `CADENCE_API_KEY` for app-scoped backend routes.
- **`model.py` / `train.py`** — the model architecture and training
  loop. Pretrained weights live in `models/`.
