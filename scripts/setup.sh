#!/usr/bin/env bash
# Cadence local setup. Brings up a self-contained dev stack:
#   - Supabase local (Postgres + GoTrue) via the Supabase CLI
#   - Python venv with backend deps
#   - Schema applied
#   - backend/.env populated with the local Supabase URL + service key
#
# Re-runnable. Safe to invoke from a fresh checkout or after pulling.
#
# Usage:
#   bash scripts/setup.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
SCHEMA_FILE="$BACKEND_DIR/schema.sql"
ENV_FILE="$BACKEND_DIR/.env"
VENV_DIR="$BACKEND_DIR/.venv"

bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
info()   { printf '  · %s\n' "$*"; }
ok()     { printf '\033[32m  ✓ %s\033[0m\n' "$*"; }
fail()   { printf '\033[31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

require() {
    command -v "$1" >/dev/null 2>&1 \
        || fail "Missing prerequisite: $1.${2:+ $2}"
}

bold "[1/5] Checking prerequisites"
require python3 "Install Python 3.11+."
require docker "Install Docker Desktop or Docker Engine, then make sure the daemon is running."
docker info >/dev/null 2>&1 || fail "Docker daemon isn't responding. Start Docker and re-run."
require supabase "Install with: brew install supabase/tap/supabase  (macOS) or see https://supabase.com/docs/guides/cli."
require psql "Install the Postgres client (libpq). On macOS: brew install libpq && brew link --force libpq."
ok "tooling looks good"

bold "[2/5] Python venv + backend deps"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    info "created $VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$BACKEND_DIR/requirements.txt"
ok "deps installed (this may take a minute on first run — tensorflow is large)"

bold "[3/5] Supabase local stack"
cd "$REPO_ROOT"
if [ ! -f "$REPO_ROOT/supabase/config.toml" ]; then
    supabase init >/dev/null
    info "supabase project initialized"
fi
# `supabase start` is idempotent — if the stack is already up it just
# prints status. If you ever want a clean slate: `supabase stop --no-backup`.
supabase start >/dev/null
ok "supabase local stack running"

# Pull connection details. `supabase status -o env` emits shell-friendly
# KEY=VALUE lines; we source it into a temp file to avoid eval'ing
# untrusted output.
STATUS_FILE="$(mktemp)"
trap 'rm -f "$STATUS_FILE"' EXIT
supabase status -o env >"$STATUS_FILE"
# shellcheck disable=SC1090
source "$STATUS_FILE"
DB_URL="${DB_URL:?supabase status did not return DB_URL}"
API_URL="${API_URL:?supabase status did not return API_URL}"
SERVICE_ROLE_KEY="${SERVICE_ROLE_KEY:?supabase status did not return SERVICE_ROLE_KEY}"

bold "[4/5] Applying schema"
psql "$DB_URL" -v ON_ERROR_STOP=1 -q -f "$SCHEMA_FILE"
ok "schema applied"

bold "[5/5] Writing $ENV_FILE"
# Resend isn't needed locally — demo mode bypasses the email send and
# returns the OTP in the API response. Set CADENCE_DEMO_MODE=0 and add
# a real RESEND_KEY when you want real emails.
cat >"$ENV_FILE" <<EOF
SUPABASE_URL=$API_URL
SUPABASE_KEY=$SERVICE_ROLE_KEY
RESEND_KEY=
CADENCE_DEMO_MODE=1
EOF
ok "wrote $ENV_FILE (demo mode on, resend disabled)"

cat <<'NEXT'

Setup complete.

To run the stack (two terminals):

  # backend (Flask, port 5001)
  cd backend
  source .venv/bin/activate
  python -c "from app import app; app.run(host='127.0.0.1', port=5001)"

  # frontend (static, port 5173)
  cd frontend
  python3 -m http.server 5173

Then open http://localhost:5173 and try a signup → login → 2FA cycle.
The OTP is shown in the green "Demo mode" banner on the 2FA page.

Useful commands:
  supabase status              # local URLs and keys
  supabase stop                # tear down the docker stack
  psql "$(supabase status -o env | grep ^DB_URL= | cut -d= -f2-)" \
       -c 'select count(*) from auth.users;'
NEXT
