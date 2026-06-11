#!/usr/bin/env bash
# Sync source-of-truth files from the GitLab checkout into the local GitHub
# deployment worktrees used by this project:
#   - Backend / Render: https://github.com/BhatMaya/cadence.git
#   - Frontend / Vercel: https://github.com/aryamanrtunjay/cadence.git
#
# This intentionally copies only source/docs/package artifacts. It does not
# copy .env files, virtualenvs, logs, .next output, or deployment-only scripts.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE_ROOT="$(cd "$REPO_ROOT/.." && pwd)"
BACKEND_DEPLOY="${CADENCE_BACKEND_DEPLOY_DIR:-$WORKSPACE_ROOT/cadence_github/cadence}"
FRONTEND_DEPLOY="${CADENCE_FRONTEND_DEPLOY_DIR:-$WORKSPACE_ROOT/github_cadence/cadence}"

info() { printf '  · %s\n' "$*"; }
ok() { printf '\033[32m  ✓ %s\033[0m\n' "$*"; }
fail() { printf '\033[31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

copy_file() {
    local source="$1"
    local target="$2"
    mkdir -p "$(dirname "$target")"
    cp "$source" "$target"
}

sync_common_tree() {
    local target="$1"
    [ -d "$target" ] || fail "Deployment directory not found: $target"

    copy_file "$REPO_ROOT/README.md" "$target/README.md"
    copy_file "$REPO_ROOT/docs/deployment.md" "$target/docs/deployment.md"
    copy_file "$REPO_ROOT/docs/release.md" "$target/docs/release.md"

    for file in .env.example ENDPOINTS.txt Procfile app.py model_service.py requirements.txt schema.sql; do
        copy_file "$REPO_ROOT/backend/$file" "$target/backend/$file"
    done
    copy_file "$REPO_ROOT/backend/tests/__init__.py" "$target/backend/tests/__init__.py"
    copy_file "$REPO_ROOT/backend/tests/test_model_service.py" "$target/backend/tests/test_model_service.py"
    copy_file "$REPO_ROOT/backend/tests/test_platform_api.py" "$target/backend/tests/test_platform_api.py"

    for file in apply_schema.sh check_deployments_synced.sh publish_package.sh setup.sh smoke_platform_api.py sync_deployments.sh; do
        copy_file "$REPO_ROOT/scripts/$file" "$target/scripts/$file"
        chmod +x "$target/scripts/$file"
    done

    for file in README.md package.json package-lock.json tsconfig.json vitest.config.ts; do
        copy_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done
    for file in src/api.ts src/capture.ts src/features.ts src/index.ts src/types.ts; do
        copy_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done
    for file in tests/api.test.ts tests/capture.test.ts tests/features.test.ts; do
        copy_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done
    for file in scripts/extract-features.ts scripts/generate-samples.ts; do
        copy_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done
    for file in data/features.json data/samples.json; do
        copy_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done

    mkdir -p "$target/frontend/vendor"
    cp "$REPO_ROOT"/frontend/vendor/* "$target/frontend/vendor/"
}

sync_backend_ci() {
    local target="$1"
    copy_file "$REPO_ROOT/render.yaml" "$target/render.yaml"
}

sync_frontend_tree() {
    local target="$1"
    [ -d "$target" ] || fail "Frontend deployment directory not found: $target"

    for file in package.json package-lock.json next.config.mjs vercel.json; do
        copy_file "$REPO_ROOT/frontend/$file" "$target/frontend/$file"
    done
    copy_file "$REPO_ROOT/frontend/components/SynergyzeApp.js" "$target/frontend/components/SynergyzeApp.js"
    copy_file "$REPO_ROOT/frontend/app/globals.css" "$target/frontend/app/globals.css"
    copy_file "$REPO_ROOT/frontend/app/layout.js" "$target/frontend/app/layout.js"
    copy_file "$REPO_ROOT/frontend/app/[[...route]]/page.js" "$target/frontend/app/[[...route]]/page.js"
}

info "Syncing common backend/package files to $BACKEND_DEPLOY"
sync_common_tree "$BACKEND_DEPLOY"
sync_backend_ci "$BACKEND_DEPLOY"

info "Syncing common backend/package files to $FRONTEND_DEPLOY"
sync_common_tree "$FRONTEND_DEPLOY"

info "Syncing Next.js frontend files to $FRONTEND_DEPLOY"
sync_frontend_tree "$FRONTEND_DEPLOY"

ok "deployment worktrees synced"
