#!/usr/bin/env bash
# vLLM sunucusunu API key ile başlatır. Key yoksa üretir.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}"
PORT="${PORT:-8000}"
KEY_FILE="${KEY_FILE:-$HOME/.vllm_api_key}"

# Key yoksa üret, sadece sahibi okuyabilsin
if [ ! -s "$KEY_FILE" ]; then
  ( umask 077; python3 -c "import secrets; print('sk-' + secrets.token_urlsafe(32))" > "$KEY_FILE" )
fi
export VLLM_API_KEY="$(cat "$KEY_FILE")"   # komut satırında görünmesin diye env ile veriyoruz

echo "=========================================="
echo "API key : $VLLM_API_KEY"
IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
echo "URL     : http://${IP:-<IP>}:$PORT/v1"
echo "Model   : $MODEL"
echo "=========================================="

exec vllm serve "$MODEL" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --dtype float16 \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.90 \
  --enable-prefix-caching \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
