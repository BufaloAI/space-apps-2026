#!/usr/bin/env bash
# Hosts the whole app (site + API) on THIS Linux machine as a systemd user service, optionally with a public
# Cloudflare tunnel. Safe to re-run. Usage (from anywhere):
#
#   backend/scripts/setup-host.sh            # check, install, build, start on http://127.0.0.1:8000
#   backend/scripts/setup-host.sh --tunnel   # ... and publish it with a public https://*.trycloudflare.com link
#   backend/scripts/setup-host.sh --check    # only run the checks
#
# The language model stays on the GPU server; this machine reaches it over Tailscale (VLLM_BASE_URL in .env).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT/backend/.env"
SITE="$ROOT/bufaloai-site"
UNIT_DIR="$HOME/.config/systemd/user"
PORT="${PORT:-8000}"
TUNNEL=0
CHECK_ONLY=0
for a in "$@"; do
  case "$a" in
    --tunnel) TUNNEL=1 ;;
    --check) CHECK_ONLY=1 ;;
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

echo "== systemd user services"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/space-backend.service" <<EOF
[Unit]
Description=Space Apps 2026 backend + site (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$ROOT
ExecStart=$(command -v uv) run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $PORT --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
ok "wrote $UNIT_DIR/space-backend.service"
if [ "$TUNNEL" = 1 ]; then
  cat > "$UNIT_DIR/space-tunnel.service" <<EOF
[Unit]
Description=Public Cloudflare quick tunnel to the Space Apps site
After=space-backend.service network-online.target
Wants=space-backend.service

[Service]
ExecStart=$(command -v cloudflared) tunnel --no-autoupdate --url http://127.0.0.1:$PORT
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
  ok "wrote $UNIT_DIR/space-tunnel.service"
fi
systemctl --user daemon-reload
systemctl --user enable --now space-backend.service
systemctl --user restart space-backend.service
[ "$TUNNEL" = 1 ] && systemctl --user enable --now space-tunnel.service && systemctl --user restart space-tunnel.service
loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes || warn "run 'sudo loginctl enable-linger $USER' so the services start at boot without logging in"

for _ in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/health" && break; sleep 1; done
curl -s "http://127.0.0.1:$PORT/api/health" >/dev/null && ok "running: http://127.0.0.1:$PORT" || fail "backend did not start: journalctl --user -u space-backend -n 50"
if [ "$TUNNEL" = 1 ]; then
  for _ in $(seq 1 30); do
    LINK="$(journalctl --user -u space-tunnel --since '-2min' --no-pager 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
    [ -n "$LINK" ] && break; sleep 1
  done
  [ -n "${LINK:-}" ] && ok "public link: $LINK  (changes when the tunnel restarts)" \
    || warn "tunnel link not found yet: journalctl --user -u space-tunnel | grep trycloudflare"
fi
