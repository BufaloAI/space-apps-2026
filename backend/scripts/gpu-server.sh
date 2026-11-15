#!/usr/bin/env bash
# GPU sunucusu (vLLM) için açılış otomasyonu ve kontrol. Sunucuda, bu klasörde (~/Masaüstü/vllm) çalıştırın.
# GPU server (vLLM): boot automation and health check. Run ON the server, from this folder.
#
#   ./gpu-server.sh check      read-only: GPU, model cache, services, local + Tailscale access (no sudo)
#   ./gpu-server.sh install    sudo: install/refresh vllm.service + vllm-tailscale-fix timer, enable everything
#                              at boot (tailscaled, vllm, fix timer). Safe to re-run.
#   ./gpu-server.sh install --no-sleep   also disable suspend/hibernate (a sleeping server = bots down)
#
# Needs next to it: vllm.service, vllm_serve.sh, vllm-tailscale-fix.sh/.service/.timer (from backend/scripts/).
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8000}"
MODEL_DIR="$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-8B-FP8"
ok() { printf '  \e[32m✓\e[0m %s\n' "$*"; }
bad() { printf '  \e[31m✗\e[0m %s\n' "$*"; FAILS=$((FAILS+1)); }
warn() { printf '  \e[33m!\e[0m %s\n' "$*"; }
FAILS=0

check() {
  echo "== GPU server check ($(hostname), $(date '+%F %T'))"
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then
    ok "GPU: $(nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader | head -1)"
  else bad "GPU not visible (nvidia driver?)"; fi
  [ -x /home/nasko/vllm-env/bin/vllm ] && ok "vLLM env /home/nasko/vllm-env" || bad "vLLM not found in /home/nasko/vllm-env"
  [ -d "$MODEL_DIR" ] && ok "model cached ($(du -sh "$MODEL_DIR" 2>/dev/null | cut -f1))" \
    || bad "model not in HF cache; the service runs offline (HF_HUB_OFFLINE=1): download it once by hand"
  [ -s "$HOME/.vllm_api_key" ] && ok "API key file ~/.vllm_api_key" || warn "no ~/.vllm_api_key yet (created on first start)"
  for u in tailscaled.service vllm.service vllm-tailscale-fix.timer; do
    local e a
    e="$(systemctl is-enabled "$u" 2>/dev/null)"; a="$(systemctl is-active "$u" 2>/dev/null)"
    if [ "$e" = enabled ] && [ "$a" = active ]; then ok "$u enabled + active"
    else bad "$u boot=$e now=$a  → ./gpu-server.sh install"; fi
  done
  if [ -s "$HOME/.vllm_api_key" ]; then
    local code
    code="$(curl -s -m 10 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $(cat "$HOME/.vllm_api_key")" "http://127.0.0.1:$PORT/v1/models")"
    [ "$code" = 200 ] && ok "vLLM answers on :$PORT" || bad "vLLM not answering on :$PORT (HTTP $code; first start takes 2-3 min) → sudo journalctl -u vllm -n 50"
  fi
  if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
    ok "tailscale up: $(tailscale ip -4 2>/dev/null | head -1)"
    local route
    route="$(ip route get 100.108.49.44 2>/dev/null | head -1)"
    echo "$route" | grep -q tailscale0 && ok "replies to the backend go via tailscale0" \
      || bad "replies to the backend do NOT go via tailscale0 ($route) → vllm-tailscale-fix timer"
  else bad "tailscale down → sudo systemctl enable --now tailscaled"; fi
  if command -v warp-cli >/dev/null; then warn "Cloudflare WARP: $(warp-cli status 2>/dev/null | head -1) (handled by vllm-tailscale-fix)"; fi
  if systemctl is-enabled sleep.target 2>/dev/null | grep -q masked; then ok "sleep disabled"
  else warn "the server may suspend when idle (bots down). Optional: ./gpu-server.sh install --no-sleep"; fi
  [ "$FAILS" = 0 ] && echo "All good." || echo "$FAILS problem(s)."
  return "$FAILS"
}

install() {
  for f in vllm.service vllm_serve.sh vllm-tailscale-fix.sh vllm-tailscale-fix.service vllm-tailscale-fix.timer; do
    [ -f "$DIR/$f" ] || { echo "missing $DIR/$f (copy it from backend/scripts/)"; exit 1; }
  done
  chmod +x "$DIR/vllm_serve.sh"
  sudo install -m 644 "$DIR/vllm.service" /etc/systemd/system/vllm.service
  sudo install -m 755 "$DIR/vllm-tailscale-fix.sh" /usr/local/bin/vllm-tailscale-fix.sh
  sudo install -m 644 "$DIR/vllm-tailscale-fix.service" "$DIR/vllm-tailscale-fix.timer" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now tailscaled.service
  sudo systemctl enable --now vllm-tailscale-fix.timer
  sudo systemctl enable vllm.service
  systemctl is-active --quiet vllm.service || sudo systemctl start vllm.service
  if [ "${1:-}" = "--no-sleep" ]; then
    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
    ok "suspend/hibernate disabled (undo: sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target)"
  fi
  ok "installed; vLLM needs 2-3 minutes to load the model"
  echo "Then: ./gpu-server.sh check"
}

case "${1:-check}" in
  check) check ;;
  install) shift; install "$@" ;;
  *) echo "usage: $0 check | install [--no-sleep]"; exit 2 ;;
esac
