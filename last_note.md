What runs on this laptop now (all user services, all restart forever on failure):

┌─────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────┐
│     Service     │                                               Does                                               │
├─────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ space-backend   │ site + API + ML + chatbot wrapper on port 8000                                                   │
├─────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ space-tunnel    │ spaceapps.nasko.tech                                                                             │
├─────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ space-watchdog  │ every 2 min: restarts the backend or tunnel after 2 failed checks in a row; notifies you when    │
│                 │ the chatbot model goes down or comes back                                                        │
├─────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ space-bootcheck │ 90 s after boot: full check, desktop notification if anything is wrong                           │
└─────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────┘

The new bufaloai command works from any terminal: status, check, logs, restart, update, stop, start, enable, disable. restart, update and install refuse while someone has an active session unless you add --force. I added that after my install restart ended an open session, probably yours. Sorry about that.

Tutorials: TUTORIAL.md (English) and TUTORIAL.tr.md (Turkish) cover installing both machines, Cloudflare, boot automation, daily use, development, using the site, training a new model, moving the host, and troubleshooting. GETTING-STARTED.md now just points to them. setup-host.sh installs everything through bufaloai install.

Not tested yet: the watchdog's "restart a dead backend" path. A session was open both times I tried, so I didn't stop the backend; the reboot test will cover it.

What you need to do:

1. On this laptop (sudo):
sudo systemctl enable tailscaled        # running now, but would not start at boot
sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
   The second line stops the laptop sleeping when the lid closes; the change takes effect after a reboot. Also turn off GNOME Settings → Power → Automatic Suspend. A sleeping laptop takes the site down.
2. On the GPU server: I can't reach it over SSH, because its firewall only allows port 8000. Copy the updated backend/scripts/vllm.service and the new backend/scripts/gpu-server.sh to ~/Masaüstü/vllm/, by git or USB. Then:
cd ~/Masaüstü/vllm
./gpu-server.sh check                 # read-only, see what's missing
./gpu-server.sh install --no-sleep    # sudo: enables vllm, tailscaled, fix timer at boot; disables suspend
3. Reboot test: reboot the GPU server, wait 3 minutes, run ./gpu-server.sh check. Then reboot this laptop and don't log in for 2 minutes. Open https://spaceapps.nasko.tech on your phone and try an analysis. Then tell me and I'll check the boot logs here.

Nothing is committed.