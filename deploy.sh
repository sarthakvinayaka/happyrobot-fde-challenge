#!/usr/bin/env bash
# Deploy helper: Railway (default) or Fly.io.
#
# Railway:
#   npm i -g @railway/cli   # or brew install railway
#   railway login
#   railway init   # once, in repo
#   export API_KEY=... FMCSA_KEY=... DB_PASS=...  # use Railway Postgres/Redis URLs in dashboard
#   ./deploy.sh railway
#
# Fly.io (API container only; add Postgres/Redis via `fly postgres create` / Upstash):
#   brew install flyctl
#   fly auth login
#   fly apps create YOUR_APP   # once
#   ./deploy.sh fly

set -euo pipefail

MODE="${1:-railway}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

case "$MODE" in
  railway)
    command -v railway >/dev/null 2>&1 || {
      echo "Install Railway CLI: https://docs.railway.com/develop/cli"
      exit 1
    }
    echo "Setting variables from environment (if set): API_KEY, FMCSA_KEY, FMCSA_WEB_KEY, DATABASE_URL, REDIS_URL"
    [[ -n "${API_KEY:-}" ]] && railway variables --set "API_KEY=${API_KEY}" || true
    [[ -n "${FMCSA_KEY:-}" ]] && railway variables --set "FMCSA_KEY=${FMCSA_KEY}" || true
    [[ -n "${FMCSA_WEB_KEY:-}" ]] && railway variables --set "FMCSA_WEB_KEY=${FMCSA_WEB_KEY}" || true
    [[ -n "${DATABASE_URL:-}" ]] && railway variables --set "DATABASE_URL=${DATABASE_URL}" || true
    [[ -n "${REDIS_URL:-}" ]] && railway variables --set "REDIS_URL=${REDIS_URL}" || true
    railway up
    ;;
  fly)
    command -v fly >/dev/null 2>&1 || {
      echo "Install Fly CLI: https://fly.io/docs/hands-on/install-flyctl/"
      exit 1
    }
    fly deploy --config fly.toml --build-target api
    ;;
  *)
    echo "Usage: $0 [railway|fly]"
    exit 1
    ;;
esac
