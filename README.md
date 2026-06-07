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
└── scripts/setup.sh   # one-shot local setup
```

## Running locally

The backend uses Supabase for auth + Postgres. To run end-to-end on a
single machine without touching the cloud, we use the **Supabase CLI**,
which spins up the same stack (Postgres, GoTrue, PostgREST, …) inside
Docker. The application code is unchanged; it just talks to
`http://localhost:54321` instead of `*.supabase.co`.

### Prerequisites

| Tool | Purpose | Install |
| --- | --- | --- |
| Python 3.11+ | backend runtime | `brew install python` / your distro's package |
| Docker | hosts the local Supabase stack | https://docs.docker.com/get-docker/ |
| Node.js/npm | frontend runtime and Supabase CLI fallback | https://nodejs.org/ |
| Supabase CLI | manages the local stack | Optional if `npx supabase` works; otherwise install from https://supabase.com/docs/guides/cli |
| `psql` | applies/inspects the schema | Optional; if missing, setup uses `psql` inside the Supabase database container |

### One-shot setup

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
4. Applies `backend/schema.sql` through `scripts/apply_schema.sh`, using
   local `psql` when available or the Supabase database container otherwise.
5. Writes `backend/.env` with the local Supabase URL + service-role
   key, `CADENCE_DEMO_MODE=1`, `CADENCE_ALLOW_OPEN_ADMIN=1`, and local
   frontend CORS origins, so 2FA codes are returned in the API response
   (and shown in the UI banner) instead of emailed.

It's idempotent — safe to re-run after pulling.

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

Open <http://localhost:3000>, register, sign in, and copy the OTP from
the green "Demo mode" banner on the 2FA page.

### Useful commands

```bash
supabase status                 # show local URLs + keys
npx supabase status             # same, if the CLI is not installed globally
supabase stop                   # tear down the Docker stack
supabase stop --no-backup       # nuke the local Postgres data too

# apply the schema to local Supabase or a production Postgres URL
DATABASE_URL=<postgres-url> bash scripts/apply_schema.sh

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

## Platform API quickstart

Cadence can also run as a platform API for other applications. An
confirmed developer account creates an application, gets a server-side
API key, and the integrating app sends typing samples captured by the
npm package to `/v1/enroll` and `/v1/score`.

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
and manual app/key management. Set `NEXT_PUBLIC_SYNERGYZE_API_BASE` for
the deployed API base. `CADENCE_ADMIN_TOKEN` is not required for normal
developer onboarding.

Use the generated `sk_live_...` key only from trusted server-side code.
Browser code should use `createCapture` to collect a `Sample`, then post
that sample to the application's own backend; that backend calls
Cadence.

```ts
import { createCadenceClient } from '@cadence-auth/cadence';

const cadence = createCadenceClient({
  apiBaseUrl: process.env.CADENCE_API_BASE!,
  apiKey: process.env.CADENCE_API_KEY!
});

await cadence.enroll({
  external_user_id: user.id,
  raw_data: sample,
  quality_score: sample.quality_score,
  flags: sample.flags
});

const result = await cadence.score({
  external_user_id: user.id,
  raw_data: sample
});

if (result.match) {
  console.log('Typing matched with confidence', result.confidence);
}
```

See `backend/ENDPOINTS.txt` for the full API notes,
`packages/capture/README.md` for npm package usage, and
`docs/release.md` for the production release and npm publishing
checklist.

## Project layout details

- **`backend/app.py`** — Flask routes for `/signup`, `/authenticate`,
  `/logout`, `/code_verification`, `/resend_code`, `/health`,
  `/model/health`, and the `/v1/*` platform API. See
  `backend/ENDPOINTS.txt` for the full contract.
- **`backend/model_service.py`** — wraps the Keras siamese model;
  fetches a user's prior successful samples from
  `public.login_attempts`, normalizes both sides, runs them through the
  twin towers, and returns the mean similarity.
- **`packages/capture/`** — TypeScript/ESM package that captures
  `keydown`/`keyup` timings into a `Sample` payload, extracts timing
  features, and exposes a typed Cadence API client. The frontend imports
  the prebuilt dist from `frontend/vendor/`.
- **`frontend/`** — Next.js app with client-side routes
  (`/`, `/register`, `/login`, `/twofa`, `/dashboard`). Posts to
  `http://localhost:5001` by default; override with
  `NEXT_PUBLIC_SYNERGYZE_API_BASE` or
  `localStorage.setItem('synergyze.api_base', '...')` in the browser
  console.
- **`model.py` / `train.py`** — the model architecture and training
  loop. Pretrained weights live in `models/`.
