cd ~/Projeler/space-apps-2026/bufaloai-site
npm run dev

The backend now also serves the built site, so the site and API share one port (8000). With the repository on the laptop:

1. Copy backend/.env from this PC by USB or scp. It holds the API key; don't commit it.
2. Install uv, Node.js 22.12 or newer, Tailscale and cloudflared.
3. Join Tailscale to the same network as the GPU server: sudo systemctl enable --now tailscaled && sudo tailscale up
4. Run:
backend/scripts/setup-host.sh --check     # each ✗ line says how to fix it
backend/scripts/setup-host.sh --tunnel    # installs, builds, starts the services, prints the public link
sudo loginctl enable-linger $USER         # services start at boot without logging in

The script checks the tools, the model, Tailscale, that the language model answers, and the IPv6 problem we hit on this PC. It then builds everything and installs two services: space-backend (site and API) and space-tunnel (the public https://….trycloudflare.com link). I tested its check mode and the single-port serving here; I haven't run it on the laptop.

- The public link changes whenever the tunnel restarts. For a fixed address, use tailscale funnel 8000 or a named Cloudflare tunnel.
- Anyone with the link can use the site. There's no login, and visitors queue one at a time.
- Run only one host at a time. Before the laptop goes live, stop this PC's backend with systemctl --user disable --now space-backend.

The full steps, including how to do it without the script, are in section 7 of GETTING-STARTED.md. I also updated frontend-spec.md. Nothing is committed.