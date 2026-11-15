# Getting started: backend + BUFALOAI site

This repo has three parts that work together:

| Part | Folder | Runs on | What it does |
|---|---|---|---|
| Backend (FastAPI) | `backend/` | `127.0.0.1:8000` | Sessions and queue, ML analysis jobs, the two advisor bots, climate data |
| ML | `ml/` | inside the backend process | NASA POWER, MODIS NDVI, XGBoost forecast, risk and rotation scores |
| Site (React + Vite) | `bufaloai-site/` | `127.0.0.1:5180` (dev) | Globe intro, map, fields, climate pages, AI page |

The LLM (Qwen3-8B on vLLM) runs on a separate GPU machine reached over Tailscale. The backend talks to it; the browser never does.

```
browser ──/api──► Vite dev server (5180) ──proxy──► backend (8000) ──► vLLM (Tailscale)
                                                         └──► ml/ (NASA POWER, MODIS, XGBoost)
```

---

## 1. One-time setup

```bash
# from the repo root
uv sync                                   # Python env for backend + ml (.venv, Python 3.12)
cp backend/.env.example backend/.env      # then put VLLM_API_KEY in backend/.env (skip if it exists)

cd bufaloai-site
npm ci                                    # Node 22.12+; run this on each OS (node_modules is OS-specific)
```

> If `node_modules` was copied from another computer (e.g. Windows), Vite fails with
> `Cannot find module @rollup/rollup-linux-x64-gnu`. Run `npm ci` again on this machine.

---

## 2. Start the backend

On this machine the backend is already a **systemd user service** that starts automatically:

```bash
systemctl --user status space-backend      # is it running?
systemctl --user restart space-backend     # after changing backend code or backend/.env
journalctl --user -u space-backend -f      # live logs
curl -s localhost:8000/api/health          # {"ok":true,"active":false,"queue_length":0}
```

To run it by hand instead (stop the service first so port 8000 is free):

```bash
systemctl --user stop space-backend
uv run uvicorn app.main:app --app-dir backend --port 8000 --workers 1   # from the repo root
```

`--workers 1` is required: sessions and jobs live in memory.

Interactive API docs: <http://localhost:8000/docs>

---

## 3. Start the site

```bash
cd bufaloai-site
npm run dev            # http://127.0.0.1:5180
```

`vite.config.ts` forwards every `/api/...` request to `http://127.0.0.1:8000`, so CORS is not
involved. To use a backend somewhere else:

```bash
BUFALOAI_API=http://other-host:8000 npm run dev
```

Production build: `npm run build` (type check + `dist/`), then `npm run preview` (port 4180, same proxy).
For static hosting without the proxy, build with `VITE_API_BASE=https://your-backend` and add that
site's address to `CORS_ORIGINS` in `backend/.env`.

---

## 4. Using it

### Draw a field
1. Open **Earth atlas** or **My fields**, pick **Polygon** or **Rectangle**, and draw the parcel.
2. In the dialog, name the field. Under **Farmer's knowledge**, fill in whatever you know:
   irrigation, soil texture, organic matter %, slope %, current rotation.
   Each known value raises the confidence of the rotation advice; leave unknowns blank.
   Fields are saved in the browser (localStorage) and can be exported as GeoJSON.

### Climate pages (no session needed)
**Climate analysis**, **Agricultural indicators** and **Data explorer** now use real
**NASA POWER** daily data for the selected field's centre:

- Pick a field and a year. The green "NASA POWER" label means the data is live.
- Past years are cached for good (backend `ml/cache/climate/` and the browser); the current year is
  refreshed daily.
- With no field selected, or if POWER is unreachable, the page shows the synthetic demo series and
  says so ("Demonstration data").

### AI page (analysis + advisors)
1. Open **AI intelligence**. The page joins a backend session automatically.
   Only **one user at a time** can be active; others see "Your place in line: N" and get in automatically.
2. Pick the field and press **Analyse**. Progress steps appear (climate data, satellite NDVI, soil,
   crop history, model). Satellite NDVI can take up to 2 minutes; a repeat on the same day is instant.
3. Results:
   - **Layer cards** (NDVI forecast, risk, climate), each with a **confidence badge**
     (high / medium / low, decided by the backend). Low confidence cards have a red dashed border.
   - **Warnings** (satellite delay, missing sources) and the **disclaimer** stay visible.
   - **Two advisors** write side by side (Risk & climate, Rotation). Each shows which data it sees.
4. **Ask a follow-up** below the advisors, to both of them or to one.
5. **Rotations**: suggestion cards with score, risk, estimated yield losses and benefits.
   Under **Evaluate my own rotation**, pick a rotation and set priority sliders, then **Re-score**.
   This is instant, and the rotation advisor sees your choice on the next question.
   **Information gaps** lists what is unknown; add it in **Farmer's data** to raise confidence.
6. The session closes after **5 minutes of inactivity** (a warning with a **Stay** button appears
   at 60 s). Closing the tab or **Leave session** frees the slot for the next person.

---

## 5. API quick reference

Full contract: `backend/frontend-spec.md`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/session` | join (active or queued) |
| GET | `/api/session/{id}` / `/events` | status / queue updates (SSE) |
| POST | `/api/session/{id}/keepalive`, `/leave` | stay active / free the slot |
| GET | `/api/agents` | bot list |
| POST | `/api/analysis` | start analysis → `job_id` |
| POST | `/api/ask` | follow-up question → `job_id` |
| GET | `/api/jobs/{job_id}/events?session_id=` | job progress and bot text (SSE) |
| GET | `/api/rotations/catalog` | crops, rotations, priorities |
| POST | `/api/rotations` | re-score with the farmer's choice |
| GET | `/api/climate/daily?lat=&lon=&year=` | **new**: cached NASA POWER daily series |

Try it without the site:

```bash
curl -s "localhost:8000/api/climate/daily?lat=41.0&lon=29.0&year=2025" | head -c 400
```

Frontend code: `src/api.ts` (client and climate cache), `src/backend.tsx` (session and job state),
`src/Intelligence.tsx` (AI page), `src/climate.ts` (`useClimate`).

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| AI page: "Cannot reach the server" | Backend not running: `systemctl --user status space-backend` |
| Climate page says "Demonstration data" with a field selected, backend log shows `handshake operation timed out` | See **IPv6** below |
| Analysis is slow (1–2 min) | MODIS NDVI service is slow; same field on the same day is cached |
| "Wait for the current job to finish" | One job at a time; wait for the bots to finish |
| Advisor shows "Model server unreachable" | vLLM GPU machine or Tailscale is down; the other advisor keeps working |
| `Cannot find module @rollup/rollup-linux-x64-gnu` | Run `npm ci` in `bufaloai-site/` on this machine |

### IPv6 handshake timeouts (seen 2026-11-15)
From this machine, **IPv6 connections to CloudFront and Azure hosts open but the TLS handshake
stalls**; IPv4 works. `curl` falls back to IPv4, but Python tries IPv6 first and times out. This
affects NASA POWER (the climate pages and the ML analysis) and Planetary Computer NDVI.

Check:
```bash
curl -6 -sS -o /dev/null --max-time 10 https://power.larc.nasa.gov/ && echo ipv6 ok
curl -4 -sS -o /dev/null --max-time 10 https://power.larc.nasa.gov/ && echo ipv4 ok
```

Fix (system-wide, prefer IPv4 for name lookups; needs sudo):
```bash
echo 'precedence ::ffff:0:0/96  100' | sudo tee -a /etc/gai.conf
systemctl --user restart space-backend
```
Remove that line later if the IPv6 route gets fixed.
