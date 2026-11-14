#!/usr/bin/env bash
# vLLM sunucusunu API key ile başlatır. Key yoksa üretir.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3-8B-FP8}"
PORT="${PORT:-8000}"
KEY_FILE="${KEY_FILE:-$HOME/.vllm_api_key}"

# Key yoksa üret, sadece sahibi okuyabilsin
if [ ! -s "$KEY_FILE" ]; then
  ( umask 077; python3 -c "import secrets; print('sk-' + secrets.token_urlsafe(32))" > "$KEY_FILE" )
fi
export VLLM_API_KEY="$(cat "$KEY_FILE")"   # komut satırında görünmesin diye env ile veriyoruz
# CUDA toolkit (nvcc) kurulu olmayan makinelerde DeepGEMM açılışta çöküyor; kapatınca normal FP8 çekirdeği kullanılır.
export VLLM_USE_DEEP_GEMM=0
# Aynı nedenle FlashInfer örnekleyicisi (nvcc ile JIT derler) yerine vLLM'nin PyTorch örnekleyicisi kullanılır.
export VLLM_USE_FLASHINFER_SAMPLER=0

IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
echo "=========================================="
# Key yalnızca terminalde gösterilir; servis (systemd) olarak çalışınca günlüğe (journal) yazılmaz.
if [ -t 1 ]; then echo "API key : $VLLM_API_KEY"; else echo "API key : (gizli; ~/.vllm_api_key dosyasında)"; fi
echo "URL     : http://${IP:-<IP>}:$PORT/v1"
echo "Model   : $MODEL"
echo "=========================================="

# Modeli önce indir (önbellekte varsa hemen geçer). Başarısız olursa vLLM kendisi indirmeyi dener.
if command -v hf >/dev/null 2>&1; then
  echo ">> Model indiriliyor/kontrol ediliyor: $MODEL"
  hf download "$MODEL" || echo "UYARI: hf download başarısız, vLLM kendisi indirmeyi deneyecek."
fi

exec vllm serve "$MODEL" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.90 \
  --enable-prefix-caching \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
