#!/usr/bin/env bash
# Hosts the whole app (site + API) on THIS Linux machine as a systemd user service, optionally with a public
# Cloudflare tunnel. Safe to re-run. Usage (from anywhere):
#
#   backend/scripts/setup-host.sh            # check, install, build, start on http://127.0.0.1:8000
#   backend/scripts/setup-host.sh --tunnel   # ... and publish it: named tunnel if ~/.cloudflared/bufaloai.yml
#                                            # exists (fixed address), else a https://*.trycloudflare.com link
#   (add --force to restart even while someone is using the site)
#   backend/scripts/setup-host.sh --check    # only run the checks
#
# The language model stays on the GPU server; this machine reaches it over Tailscale (VLLM_BASE_URL in .env).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT/backend/.env"
SITE="$ROOT/bufaloai-site"
PORT="${PORT:-8000}"
TUNNEL=0
CHECK_ONLY=0
FORCE=""
for a in "$@"; do
  case "$a" in
    --tunnel) TUNNEL=1 ;;
    --check) CHECK_ONLY=1 ;;
    --force) FORCE=--force ;;
    *) echo "Unknown option: $a" >&2; exit 2 ;;
  esac
done

ok() { printf '  \e[32m✓\e[0m %s\n' "$*"; }
warn() { printf '  \e[33m!\e[0m %s\n' "$*"; }
fail() { printf '  \e[31m✗\e[0m %s\n' "$*"; FAILED=1; }
FAILED=0

echo "== Checks ($ROOT)"
command -v uv >/dev/null && ok "uv $(uv --version | cut -d' ' -f2)" || fail "uv missing: curl -LsSf https://astral.sh/uv/install.sh | sh"
if command -v node >/dev/null; then
  NV="$(node -p 'process.versions.node')"
  node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>22||(a===22&&b>=12)?0:1)' \
    && ok "node $NV" || fail "node $NV is too old; need 22.12+ (e.g. via nvm or your distro)"
else
  fail "node missing (need 22.12+)"
fi
[ -f "$ENV_FILE" ] && ok "backend/.env present" || fail "backend/.env missing: copy it from the current host (it holds VLLM_API_KEY; never commit it)"
if [ -f "$ENV_FILE" ]; then
  grep -q '^ML_PROVIDER=xgboost' "$ENV_FILE" && ok "ML_PROVIDER=xgboost" || warn "ML_PROVIDER is not xgboost in backend/.env (real ML off)"
  grep -q '^VLLM_API_KEY=.\+' "$ENV_FILE" && ok "VLLM_API_KEY set" || fail "VLLM_API_KEY empty in backend/.env"
fi
if [ -f "$ROOT/ml/runs/LATEST" ] && [ -f "$(cat "$ROOT/ml/runs/LATEST")/model.ubj" ] 2>/dev/null; then
  ok "trained model: $(cat "$ROOT/ml/runs/LATEST")"
elif [ -f "$ROOT/ml/runs/LATEST" ] && [ -f "$ROOT/ml/runs/$(cat "$ROOT/ml/runs/LATEST")/model.ubj" ]; then
  ok "trained model: ml/runs/$(cat "$ROOT/ml/runs/LATEST")"
else
  fail "no trained model: ml/runs/LATEST must point to a run folder with model.ubj"
fi
if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
  ok "tailscale up"
else
  fail "tailscale not running: sudo systemctl enable --now tailscaled && sudo tailscale up (same tailnet as the GPU server)"
fi
if [ -f "$ENV_FILE" ]; then
  URL="$(grep '^VLLM_BASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)"
  KEY="$(grep '^VLLM_API_KEY=' "$ENV_FILE" | cut -d= -f2- || true)"
  URL="${URL:-http://100.96.165.43:8000/v1}"
  if curl -s -m 10 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" "$URL/models" | grep -q 200; then
    ok "language model reachable at $URL"
  else
    fail "language model NOT reachable at $URL (Tailscale? GPU server up? vllm.service running?)"
  fi
fi
if ! curl -4 -s -m 8 -o /dev/null https://power.larc.nasa.gov/; then
  fail "NASA POWER unreachable over IPv4 (no internet?)"
elif curl -6 -s -m 8 -o /dev/null https://power.larc.nasa.gov/ 2>/dev/null; then
  ok "IPv6 and IPv4 to NASA POWER fine"
else
  if grep -q '^precedence ::ffff:0:0/96' /etc/gai.conf 2>/dev/null; then
    ok "IPv6 broken but IPv4 preferred via /etc/gai.conf"
  else
    fail "IPv6 to NASA hangs (Python would time out). Fix: echo 'precedence ::ffff:0:0/96  100' | sudo tee -a /etc/gai.conf"
  fi
fi
if [ "$TUNNEL" = 1 ]; then
  command -v cloudflared >/dev/null && ok "cloudflared $(cloudflared --version 2>&1 | awk '{print $3}')" \
    || fail "cloudflared missing: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
fi
if [ "$FAILED" = 1 ]; then
  echo "Fix the ✗ items above and run again."
  [ "$CHECK_ONLY" = 1 ] || exit 1
fi
[ "$CHECK_ONLY" = 1 ] && exit 0

echo "== Install and build"
(cd "$ROOT" && uv sync)
# Node resolves IPv4 first: some networks hang on IPv6 to the npm registry
(cd "$SITE" && NODE_OPTIONS=--dns-result-order=ipv4first npm ci --no-audit --no-fund && npm run build)
grep -q '^SITE_DIR=' "$ENV_FILE" || { printf '\n# Built site served by the backend (one port for site + API)\nSITE_DIR=bufaloai-site/dist\n' >> "$ENV_FILE"; ok "SITE_DIR added to backend/.env"; }

echo "== Services (systemd user units, start at boot) via: bufaloai install"
# Named tunnel if ~/.cloudflared/bufaloai.yml exists (fixed address, e.g. spaceapps.nasko.tech), else a quick tunnel.
if [ "$TUNNEL" = 1 ]; then
  if [ -f "$HOME/.cloudflared/bufaloai.yml" ]; then
    "$ROOT/backend/scripts/bufaloai" install --named-tunnel "$HOME/.cloudflared/bufaloai.yml" --port "$PORT" $FORCE
  else
    "$ROOT/backend/scripts/bufaloai" install --quick-tunnel --port "$PORT" $FORCE
  fi
else
  "$ROOT/backend/scripts/bufaloai" install --no-tunnel --port "$PORT" $FORCE
fi
sleep 5
"$ROOT/backend/scripts/bufaloai" check || true
if [ "$TUNNEL" = 1 ] && [ ! -f "$HOME/.cloudflared/bufaloai.yml" ]; then
  for _ in $(seq 1 30); do
    LINK="$(journalctl --user -u space-tunnel --since '-2min' --no-pager 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
    [ -n "$LINK" ] && break; sleep 1
  done
  [ -n "${LINK:-}" ] && ok "public link: $LINK  (changes when the tunnel restarts)" \
    || warn "tunnel link not found yet: journalctl --user -u space-tunnel | grep trycloudflare"
fi
echo "Manage with: bufaloai status | logs | restart | update   (bufaloai help)"
