#!/usr/bin/env bash
# Tailscale üzerinden vLLM'e (port 8000) erişimi, WARP ve nft engellerine rağmen açık tutar.
# Tekrar tekrar çalıştırılabilir (idempotent): kural varsa dokunmaz. root ile çalışmalı.
set -u
PORT="${PORT:-8000}"
TS_NET="100.64.0.0/10"   # Tailscale adres aralığı
TS_TABLE=52              # Tailscale'in yönlendirme tablosu
PRIO=5200                # WARP kuralından (5209) önce

# 1) Yönlendirme: Tailscale adreslerine giden trafik WARP'tan önce Tailscale tablosuna baksın.
#    Tabloda bulunmayan hedefler bir sonraki kurala düşer, başka trafik etkilenmez.
ip rule show | grep -q "to $TS_NET lookup $TS_TABLE" \
  || ip rule add to "$TS_NET" lookup "$TS_TABLE" priority "$PRIO"

# 2) nft: yalnızca tailscale0 arayüzünden, yalnızca bu port için izin ver.
add_rule() { # $1=aile $2=tablo $3=zincir $4=kural (nft sözdizimi)
  nft list chain "$1" "$2" "$3" >/dev/null 2>&1 || return 0   # tablo/zincir yoksa (örn. WARP kapalı) atla
  nft list chain "$1" "$2" "$3" | grep -qF -- "$4" || nft "insert rule $1 $2 $3 $4"
}
add_rule inet filter         input  "iifname \"tailscale0\" tcp dport $PORT accept"
add_rule inet cloudflare-warp input  "iifname \"tailscale0\" tcp dport $PORT accept"
add_rule inet cloudflare-warp output "oifname \"tailscale0\" tcp sport $PORT accept"
