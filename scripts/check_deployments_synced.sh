#!/usr/bin/env bash
# Verify that source-of-truth files match the local GitHub deployment worktrees:
#   - Backend / Render: https://github.com/BhatMaya/cadence.git
#   - Frontend / Vercel: https://github.com/aryamanrtunjay/cadence.git

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE_ROOT="$(cd "$REPO_ROOT/.." && pwd)"
BACKEND_DEPLOY="${CADENCE_BACKEND_DEPLOY_DIR:-$WORKSPACE_ROOT/cadence_github/cadence}"
FRONTEND_DEPLOY="${CADENCE_FRONTEND_DEPLOY_DIR:-$WORKSPACE_ROOT/github_cadence/cadence}"

info() { printf '  · %s\n' "$*"; }
ok() { printf '\033[32m  ✓ %s\033[0m\n' "$*"; }
fail() { printf '\033[31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

compare_file() {
    local source="$1"
    local target="$2"
    [ -f "$source" ] || fail "Missing source file: $source"
    [ -f "$target" ] || fail "Missing deployment file: $target"
    if ! cmp -s "$source" "$target"; then
        fail "Out of sync: ${target#$WORKSPACE_ROOT/} differs from ${source#$REPO_ROOT/}"
    fi
}

compare_common_tree() {
    local target="$1"
    [ -d "$target" ] || fail "Deployment directory not found: $target"

    compare_file "$REPO_ROOT/README.md" "$target/README.md"
    compare_file "$REPO_ROOT/docs/deployment.md" "$target/docs/deployment.md"
    compare_file "$REPO_ROOT/docs/release.md" "$target/docs/release.md"

    for file in .env.example ENDPOINTS.txt Procfile app.py model_service.py requirements.txt schema.sql; do
        compare_file "$REPO_ROOT/backend/$file" "$target/backend/$file"
    done
    for file in __init__.py test_model_service.py test_platform_api.py; do
        compare_file "$REPO_ROOT/backend/tests/$file" "$target/backend/tests/$file"
    done

    for file in apply_schema.sh check_deployments_synced.sh publish_package.sh setup.sh smoke_platform_api.py sync_deployments.sh; do
        compare_file "$REPO_ROOT/scripts/$file" "$target/scripts/$file"
    done

    for file in README.md package.json package-lock.json tsconfig.json vitest.config.ts; do
        compare_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done
    for file in src/api.ts src/capture.ts src/features.ts src/index.ts src/types.ts; do
        compare_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done
    for file in tests/api.test.ts tests/capture.test.ts tests/features.test.ts; do
        compare_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done
    for file in scripts/extract-features.ts scripts/generate-samples.ts; do
        compare_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done
    for file in data/features.json data/samples.json; do
        compare_file "$REPO_ROOT/packages/capture/$file" "$target/packages/capture/$file"
    done

    for source in "$REPO_ROOT"/frontend/vendor/*; do
        compare_file "$source" "$target/frontend/vendor/$(basename "$source")"
    done
}

compare_frontend_tree() {
    local target="$1"
    [ -d "$target" ] || fail "Frontend deployment directory not found: $target"

    for file in package.json package-lock.json next.config.mjs vercel.json; do
        compare_file "$REPO_ROOT/frontend/$file" "$target/frontend/$file"
    done
    compare_file "$REPO_ROOT/frontend/components/SynergyzeApp.js" "$target/frontend/components/SynergyzeApp.js"
    compare_file "$REPO_ROOT/frontend/app/globals.css" "$target/frontend/app/globals.css"
    compare_file "$REPO_ROOT/frontend/app/layout.js" "$target/frontend/app/layout.js"
    compare_file "$REPO_ROOT/frontend/app/[[...route]]/page.js" "$target/frontend/app/[[...route]]/page.js"
}

info "Checking common backend/package files in $BACKEND_DEPLOY"
compare_common_tree "$BACKEND_DEPLOY"
compare_file "$REPO_ROOT/render.yaml" "$BACKEND_DEPLOY/render.yaml"

info "Checking common backend/package files in $FRONTEND_DEPLOY"
compare_common_tree "$FRONTEND_DEPLOY"

info "Checking Next.js frontend files in $FRONTEND_DEPLOY"
compare_frontend_tree "$FRONTEND_DEPLOY"

ok "deployment worktrees are synced"
