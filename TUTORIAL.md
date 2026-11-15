# BUFALOAI tutorial: install, run, publish, automate

Türkçe sürüm: [TUTORIAL.tr.md](TUTORIAL.tr.md)

Everything you need to install the project from scratch, put it online at **https://spaceapps.nasko.tech**, keep it
running by itself after every reboot, update it, and fix it when something breaks.

**Contents**
1. [How it fits together](#1-how-it-fits-together)
2. [Quick reference](#2-quick-reference)
3. [Install the host (site + API machine)](#3-install-the-host)
4. [Install the GPU server (chatbot model)](#4-install-the-gpu-server)
5. [Publish with Cloudflare](#5-publish-with-cloudflare)
6. [Start everything at boot](#6-start-everything-at-boot)
7. [Daily operations](#7-daily-operations)
8. [Development (changing the code)](#8-development)
9. [Using the site](#9-using-the-site)
10. [Training and deploying a new model](#10-training-and-deploying-a-new-model)
11. [Moving the host to another machine](#11-moving-the-host-to-another-machine)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. How it fits together

Two machines, five pieces:

```
                         ┌──────────────────────── HOST (Fedora laptop) ───────────────────────┐
 visitors ──https──►  Cloudflare ──tunnel──► space-tunnel ──► space-backend  :8000             │
 spaceapps.nasko.tech                       (cloudflared)     FastAPI: site + /api + ML         │
                         │                                    │  (NASA POWER, MODIS NDVI,       │
                         │                                    │   SoilGrids, XGBoost, rotations)│
                         │       space-watchdog (every 2 min), space-bootcheck (after boot)     │
                         └────────────────────────────────────┼─────────────────────────────────┘
                                                              │ Tailscale (private network)
                         ┌──────────── GPU SERVER (archlinux, RTX 5060 Ti) ─┼──────────────────┐
                         │  vllm.service :8000  (Qwen3-8B-FP8, the chatbot "brain")               │
                         │  tailscaled + vllm-tailscale-fix.timer (keeps Tailscale working with WARP) │
                         └────────────────────────────────────────────────────────────────────────┘
```

| Piece | Where | What it is | Service |
|---|---|---|---|
| Site | host | React app (`bufaloai-site/`), built to `bufaloai-site/dist`, served by the backend | part of `space-backend` |
| Backend = chatbot wrapper API | host | FastAPI (`backend/`): sessions/queue, analysis jobs, the two advisor bots, climate API | `space-backend` (user) |
| ML | host, inside the backend | `ml/`: data download, XGBoost NDVI forecast, risk, weather outlook, rotations | part of `space-backend` |
| Tunnel | host | `cloudflared` named tunnel `bufaloai` → `spaceapps.nasko.tech` | `space-tunnel` (user) |
| Language model | GPU server | vLLM serving Qwen3-8B-FP8 with an API key | `vllm` (system) |

The browser only ever talks to the host. The API key for the language model stays on the host (`backend/.env`)
and the GPU server (`~/.vllm_api_key`).

---

## 2. Quick reference

On the **host**:

```bash
bufaloai status          # what runs, starts at boot or not, and a full health check
bufaloai check           # health check only (site, API, Tailscale, chatbot model, public URL, NASA)
bufaloai logs backend    # follow logs (backend | tunnel | watchdog)
bufaloai restart         # restart backend + tunnel (refuses while someone uses the site; add --force)
bufaloai update          # git pull, install, rebuild the site, restart, check
bufaloai stop | start    # take everything offline / back online
bufaloai enable | disable   # start at boot on / off
```

On the **GPU server** (in `~/Masaüstü/vllm`):

```bash
./gpu-server.sh check    # GPU, model, services, Tailscale route
sudo journalctl -u vllm -f
```

Health in a browser: <https://spaceapps.nasko.tech/api/health> → `{"ok":true,...}`

---

## 3. Install the host

The host runs the site, the API and the ML. Today that is the Fedora laptop at
`/home/nasko/Projeler/space-apps-2026`. Any Linux machine works (see [§11](#11-moving-the-host-to-another-machine)).

### 3.1 Tools

| Tool | Why | Install (Fedora) |
|---|---|---|
| git | get/update the code | `sudo dnf install git` |
| [uv](https://docs.astral.sh/uv/) | Python 3.12 + packages (never use pip here) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js 22.12+ | build the site | `sudo dnf install nodejs` (check `node -v`) |
| Tailscale | private link to the GPU server | `sudo dnf install tailscale` |
| cloudflared | public tunnel | no sudo: `curl -fL -o ~/.local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x ~/.local/bin/cloudflared` |

### 3.2 Code, secrets, model

```bash
git clone <repo-url> ~/Projeler/space-apps-2026 && cd ~/Projeler/space-apps-2026
cp backend/.env.example backend/.env       # or copy the real one from the old host (USB/scp)
```

Edit `backend/.env`. The important lines:

```ini
VLLM_BASE_URL=http://100.96.165.43:8000/v1   # GPU server's Tailscale address
VLLM_API_KEY=sk-...                          # from ~/.vllm_api_key on the GPU server. NEVER commit .env
VLLM_MODEL=Qwen/Qwen3-8B-FP8
ML_PROVIDER=xgboost                          # real ML (fake = test data)
SITE_DIR=bufaloai-site/dist                  # backend serves the built site (one port for everything)
```

The trained model is not in git. `ml/runs/LATEST` must point to a folder containing `model.ubj`
(copy `ml/runs/<run>/` from the machine that trained it, then `realpath ml/runs/<run> > ml/runs/LATEST`).

### 3.3 Network

```bash
sudo systemctl enable --now tailscaled     # enable = also at every boot (important!)
sudo tailscale up                          # log into the same tailnet as the GPU server
tailscale status                           # must list "archlinux"
```

**IPv6 trap:** on some networks IPv6 connects but TLS hangs, so Python (the ML) and npm time out while
`curl` works. Test: `curl -6 -m 10 https://power.larc.nasa.gov/` hangs but `curl -4 …` works → run once:

```bash
echo 'precedence ::ffff:0:0/96  100' | sudo tee -a /etc/gai.conf
```

### 3.4 Install, build and start everything

```bash
backend/scripts/setup-host.sh --check      # every ✗ line tells you how to fix it
backend/scripts/setup-host.sh --tunnel     # uv sync, npm ci, npm run build, services, public tunnel, check
sudo loginctl enable-linger $USER          # once: services start at boot even without logging in
```

`setup-host.sh` calls `bufaloai install`, which writes all services (see [§6](#6-start-everything-at-boot)) and puts
the `bufaloai` command in `~/.local/bin`. With `--tunnel` it uses the named tunnel `~/.cloudflared/bufaloai.yml` if it
exists ([§5](#5-publish-with-cloudflare)), otherwise a temporary `trycloudflare.com` link.

Done: `bufaloai status` should end with **All good.**

---

## 4. Install the GPU server

The GPU server (Arch Linux, hostname `archlinux`, Tailscale `100.96.165.43`, RTX 5060 Ti 16 GB) runs only the
language model. Files live in `~/Masaüstü/vllm`.

### 4.1 First-time setup (already done on archlinux)

```bash
uv venv ~/vllm-env && uv pip install --python ~/vllm-env vllm        # vLLM's own env, separate from the repo
~/vllm-env/bin/hf download Qwen/Qwen3-8B-FP8                         # ~9 GB into ~/.cache/huggingface
sudo pacman -S tailscale && sudo systemctl enable --now tailscaled && sudo tailscale up
```

The API key is created on the first start in `~/.vllm_api_key`; put the same value in the host's `backend/.env`.

### 4.2 Copy the scripts

From the repo's `backend/scripts/` copy these to `~/Masaüstü/vllm/` on the server (git pull there, USB, or scp):
`vllm.service`, `vllm_serve.sh`, `vllm-tailscale-fix.sh`, `vllm-tailscale-fix.service`, `vllm-tailscale-fix.timer`,
`gpu-server.sh`.

### 4.3 Start at boot

```bash
cd ~/Masaüstü/vllm
./gpu-server.sh install --no-sleep    # installs + enables vllm, tailscaled, the fix timer; disables suspend
./gpu-server.sh check                 # wait 2-3 min for the model to load, then all ✓
```

What it sets up:

| Unit | Does | Boot |
|---|---|---|
| `tailscaled` | private network | enabled |
| `vllm.service` | `vllm_serve.sh` → `vllm serve Qwen/Qwen3-8B-FP8` on :8000, offline (`HF_HUB_OFFLINE=1`), restarts forever on failure | enabled |
| `vllm-tailscale-fix.timer` | every 30 s re-applies the routing/firewall rules that Cloudflare WARP keeps removing (details in `backend/architecture.md`) | enabled |

`--no-sleep` masks suspend/hibernate: a sleeping server means the bots stop answering. Undo:
`sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target`.

---

## 5. Publish with Cloudflare

The site is published through a **named tunnel** (fixed address, survives restarts). Current setup:

| Item | Value |
|---|---|
| Domain | `nasko.tech` (Cloudflare account) |
| Tunnel | `bufaloai`, id `23efdb43-3898-4ce2-bc91-f6cf6100c1e3` |
| Credentials | `~/.cloudflared/23efdb43-3898-4ce2-bc91-f6cf6100c1e3.json` (secret, do not share) |
| Config | `~/.cloudflared/bufaloai.yml` |
| DNS | CNAME `spaceapps` → `23efdb43-3898-4ce2-bc91-f6cf6100c1e3.cfargotunnel.com`, proxied |

To create it from scratch (or for another domain):

```bash
cloudflared tunnel login                         # browser: pick the domain (cert in ~/.cloudflared/cert.pem)
cloudflared tunnel create bufaloai               # writes ~/.cloudflared/<ID>.json
cloudflared tunnel route dns bufaloai spaceapps.nasko.tech
```

If `route dns` says `Authentication error`, add the CNAME by hand in the Cloudflare dashboard: DNS → Records →
Add: **CNAME**, name `spaceapps`, target `<ID>.cfargotunnel.com` (exactly; no `https://`, no slash), **Proxied**.

`~/.cloudflared/bufaloai.yml`:

```yaml
tunnel: <ID>
credentials-file: /home/nasko/.cloudflared/<ID>.json
ingress:
  - hostname: spaceapps.nasko.tech
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Then `bufaloai install --named-tunnel ~/.cloudflared/bufaloai.yml` (or `setup-host.sh --tunnel`). The old
`~/.cloudflared/config.yml` (a deleted SSH tunnel) is not used.

No domain? `bufaloai install --quick-tunnel` publishes on a random `https://…trycloudflare.com` link
(changes on every restart; find it with `journalctl --user -u space-tunnel | grep trycloudflare`).

---

## 6. Start everything at boot

After a power-on nothing has to be done by hand. Order of events:

| When | Host | GPU server |
|---|---|---|
| boot | `tailscaled` (system) | `tailscaled`, `vllm.service` (model loads 2-3 min) |
| +20 s | | `vllm-tailscale-fix.timer` first run, then every 30 s |
| boot (linger) | `space-backend` → site + API on :8000 | |
| after backend | `space-tunnel` → spaceapps.nasko.tech | |
| +90 s | `space-bootcheck` → full check; desktop notification if something is wrong | |
| +3 min, then every 2 min | `space-watchdog` → restarts the backend or tunnel if they stop answering (2 failed checks in a row); notifies when the chatbot model goes down/up | |

Host units live in `~/.config/systemd/user/` and are written by `bufaloai install` (do not edit them by hand; edit
the `bufaloai` script and re-run `bufaloai install`). All of them restart forever on failure.

Requirements (once):

```bash
sudo systemctl enable --now tailscaled        # host: Tailscale at boot
sudo loginctl enable-linger $USER             # host: user services at boot without login
```

Laptop as host: the site goes down when it sleeps. Keep it on power and stop lid/idle suspend: GNOME Settings →
Power → Automatic Suspend **off**, and for closing the lid:
`sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf` (then reboot).

**Reboot test** (do it once after any change):
1. Reboot the GPU server, wait 3 minutes, run `./gpu-server.sh check`.
2. Reboot the host, do not log in for 2 minutes, then open <https://spaceapps.nasko.tech> from your phone.
3. On the host: `bufaloai status` → **All good.**; `journalctl --user -u space-bootcheck -b` shows the boot check.

---

## 7. Daily operations

| Task | Command |
|---|---|
| Is everything OK? | `bufaloai status` |
| Logs | `bufaloai logs backend` · `bufaloai logs tunnel` · `bufaloai logs watchdog` |
| After `git pull` / code change | `bufaloai update` (pulls, `uv sync`, rebuilds the site if needed, restarts, checks) |
| Rebuild site without new commits | `bufaloai update --rebuild` |
| Changed `backend/.env` | `bufaloai restart` |
| Take the site offline | `bufaloai stop` (back: `bufaloai start`) |
| Offline and no autostart | `bufaloai stop && bufaloai disable` |
| Chatbot model logs | on the GPU server: `sudo journalctl -u vllm -f` |
| Restart the model | on the GPU server: `sudo systemctl restart vllm` |

`restart`, `update` and `install` refuse while someone has an active session (sessions live in memory and would be
lost). Add `--force` to do it anyway.

The site serves **one user at a time**; others wait in a queue. Sessions close after 5 minutes of inactivity.
There is no login: anyone with the link can use it. Auto-update on boot is deliberately **not** enabled: a bad
commit should not take the live site down by itself.

---

## 8. Development

Work on the code with hot reload while the live site keeps running:

```bash
cd bufaloai-site && npm run dev          # http://127.0.0.1:5180, proxies /api to the backend on :8000
uv run pytest backend/tests ml/tests -q  # 130+ tests, no GPU or network needed
```

| Folder | What | Docs |
|---|---|---|
| `backend/app/` | FastAPI: `main.py` (API), `ml.py` (ML adapter), `climate.py`, `sessions.py`, `llm.py` | `backend/architecture.md`, API contract `backend/frontend-spec.md` |
| `backend/agents.toml` | the two bots: names, prompts, which data each sees | |
| `ml/` | data loaders, model, risk, `outlook.py`, `rotation.py`, `crops.toml`, `crop_library.toml` | `ml/README.md`, `ml/DECISIONS.md` |
| `bufaloai-site/src/` | `api.ts`, `backend.tsx`, `Intelligence.tsx`, `Planner.tsx`, `FarmerTools.tsx`, `i18n.ts` | |

Rules: `uv`, never `pip`; every user-visible text in the ML is written in Turkish and English (`ml/model/i18n.py`);
the frontend shows numbers, the backend/ML decides them. Publish your change with `git push`, then `bufaloai update`
on the host.

---

## 9. Using the site

1. **Earth atlas / My fields:** draw the parcel (Polygon or Rectangle), name it, fill in **Farmer's knowledge**
   (irrigation, soil texture, organic matter, slope, current rotation). Each known value raises confidence.
2. **Climate analysis, Agricultural indicators, Data explorer:** real NASA POWER data for the selected field, plus
   **Next 4 weeks** (temperature, rain, frost and hot days vs the seasonal normal).
3. **AI intelligence:** pick the field → **Analyse**. You get the NDVI forecast, risk, climate and weather-outlook
   cards with confidence badges, two advisors writing side by side, follow-up questions, rotation suggestions
   (catalogue / auto-generated / yours) with estimated damages, **Evaluate my own rotation**, and crops suggested
   for this field.
4. **Crops & rotations:** your crops, the crop library (20 ready-made crops), suggestions for the analysed field,
   and the rotation builder. Saved in the browser; used in the next analysis.
5. **TR / EN** switch in the header: everything, including the advisors' answers. Switch before you press Analyse.

---

## 10. Training and deploying a new model

Train on the GPU laptop (needs the data and an NVIDIA GPU; see `ml/README.md`):

```bash
uv run python -m ml.run_all --check-only     # GPU, Earthdata login, API keys, data
uv run python -m ml.run_all                  # full run; result in ml/runs/<timestamp>/
```

Deploy to the host: copy `ml/runs/<timestamp>/` to the host's `ml/runs/`, then on the host:

```bash
realpath ml/runs/<timestamp> > ml/runs/LATEST
bufaloai restart
```

---

## 11. Moving the host to another machine

1. On the new machine: [§3](#3-install-the-host) (tools, repo, copy `backend/.env` and `ml/runs/<run>/`, Tailscale).
2. Copy the tunnel files: `~/.cloudflared/bufaloai.yml` and `~/.cloudflared/<ID>.json` (fix the path inside the
   `.yml` if the user name differs).
3. **On the old host first:** `bufaloai stop && bufaloai disable` (two tunnels on one name split visitors between
   machines, and two backends would both use the single GPU).
4. On the new host: `backend/scripts/setup-host.sh --tunnel`, then `sudo loginctl enable-linger $USER`.

---

## 12. Troubleshooting

Start with `bufaloai check` (host) and `./gpu-server.sh check` (GPU server): every ✗ says what to do.

| Symptom | Cause → fix |
|---|---|
| Bots: "The language model server is unreachable" | Tailscale off on the host (`sudo systemctl enable --now tailscaled`) or the GPU server is off/asleep/still loading (`./gpu-server.sh check`) |
| Browser: Cloudflare error **1016** | DNS record points to a wrong target → CNAME must be `<tunnel ID>.cfargotunnel.com` |
| Browser: error **1033** / **530** | tunnel not running → `bufaloai logs tunnel`, `bufaloai restart` |
| Browser: **502 Bad Gateway** | tunnel runs, backend does not → `bufaloai logs backend` |
| The host itself cannot open the site for a while after DNS changes | stale local DNS cache → `resolvectl flush-caches` |
| AI page: "Cannot reach the server" | backend down → `bufaloai status` |
| Analysis fails / climate page shows "Demonstration data" / `handshake operation timed out` in logs | IPv6 trap → [§3.3](#33-network) |
| `npm ci` hangs | IPv6 trap for Node → `NODE_OPTIONS=--dns-result-order=ipv4first npm ci` (bufaloai update does this) |
| `Cannot find module @rollup/rollup-linux-x64-gnu` | `node_modules` copied from Windows → `cd bufaloai-site && npm ci` |
| `bufaloai restart` says someone is using the site | wait, or `bufaloai restart --force` |
| "Wait for the current job to finish" | one job at a time; wait for the bots |
| English mode: an advisor answers in Turkish | rare; ask again |
| Analysis takes 1-2 min | first analysis of a field per day downloads satellite data; repeats are instant |
| Crop catalogue unavailable | `ML_PROVIDER` is not `xgboost` in `backend/.env`, or no model in `ml/runs/LATEST` |
| `cloudflared tunnel route dns` → Authentication error | old certificate cannot edit DNS → add the CNAME in the dashboard ([§5](#5-publish-with-cloudflare)) |

Deeper background: `backend/architecture.md` (server networking, WARP, vLLM settings), `ml/DECISIONS.md`
(model and data choices), `backend/frontend-spec.md` (every API endpoint).
