The tutorial

It's in GETTING-STARTED.md at the repo root. In short:

systemctl --user status space-backend        # backend on :8000 (already a service; restarted today)
cd bufaloai-site && npm run dev              # site on http://127.0.0.1:5180

Then: draw a field → fill in Farmer's knowledge → open AI intelligence → Analyse → ask the bots questions → try your own rotation under Evaluate my own rotation. The guide also covers production builds, pointing the site at another backend, the API list and troubleshooting.

What I built

Backend
- New endpoint GET /api/climate/daily?lat=&lon=&year=: daily NASA POWER climate data for a field's location. It reuses the downloader in ml/data_load and caches results in ml/cache/climate/. Past years are kept for good, the current year refreshes daily, and nearby fields share one cache entry. It doesn't need a session, so it never takes the single user slot. I added it to frontend-spec.md as section 5.7.

Site
- Backend connection (api.ts, backend.tsx): the API client plus session and queue handling, idle warning, freeing the slot when the tab closes, live bot text and reconnecting after a drop. Climate data is also cached in the browser.
- AI page (Intelligence.tsx):
  - Analysis progress steps.
  - Result cards with a high/medium/low confidence badge taken from the backend; low-confidence cards get a red dashed border.
  - Warnings and the disclaimer stay visible.
  - Rotation cards show estimated damages, and you can re-score with your own rotation and priorities.
  - The two bots appear side by side, with a follow-up question box.
- Farmers' own data: the field form has new inputs for soil texture, organic matter %, slope % and current rotation. These are sent with each analysis and raise the rotation confidence. The page shows how many of the 4 are filled in and lists the "information gaps".
- Climate pages: they now show real NASA POWER data for the selected field, labelled as such. Without a field, or if the data can't be fetched, they fall back to the old demo data and say so.
- The map's field card has a new AI analysis button.

Things you should know

1. IPv6 is broken on this machine. Connections to NASA POWER, Planetary Computer and the npm registry start over IPv6, then the secure handshake hangs; IPv4 works. Python and npm wait on IPv6, so fetching climate data fails after about 95 s, and the existing ML analysis is affected too. With IPv4 forced, POWER answered in about 2 s. The fix needs sudo, so I left it to you:
echo 'precedence ::ffff:0:0/96  100' | sudo tee -a /etc/gai.conf
systemctl --user restart space-backend
2. node_modules had been copied from Windows, so Vite couldn't run. I reinstalled it with NODE_OPTIONS=--dns-result-order=ipv4first npm ci because of the IPv6 problem.
3. Reloading the page ends the session. The spec asks for a "leave" signal when the tab closes, and the browser sends it on reload too. The site then joins a new session automatically, but the previous results are gone.
4. git status shows 5 deleted files in data-load/. I didn't touch that folder, so please check whether those deletions are intended before you commit. I haven't committed anything.