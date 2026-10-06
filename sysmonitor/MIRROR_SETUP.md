# SysMonitor master + remote — setup guide

**What you get**

| | MASTER (your PC) | REMOTE (app.bsccl.com/sysmonitor) |
|---|---|---|
| Address | 192.168.30.48:8000 / 163.47.80.62:9696 | https://app.bsccl.com/sysmonitor |
| Runs pinging, Tuya, PAC, Kuma, notifications | ✅ only here | ❌ never (no duplicate alerts) |
| Where data is saved | ✅ the only place (SQLite) | ❌ (passes every Save to the master) |
| Users log in | ✅ | ✅ same usernames/passwords |
| Looks and works the same | ✅ | ✅ |
| Where you edit code | ✅ | ❌ (code arrives from GitHub) |

```
 user clicks Save on the REMOTE
        │  (your browser → remote site checks login + CSRF)
        ▼
 remote ──signed request, through a private SSH tunnel──► MASTER saves it (SQLite)
                                                              │
 remote page shows the result  ◄── new data pushed within ~2 s┘   (also every 30 s)

 code:  you edit on MASTER ─► you run ./deploy/publish.sh (checks, shows the list, asks "y")
                                  └─► GitHub ─► REMOTE pulls it (every 5 min) and restarts
```

**What users notice:** nothing, except a thin blue banner on the remote
("🌐 Remote site · … data is 5s old"). Pages that control the master machine
(System tools, Uptime Kuma, PAC) are fetched live from the master, so they look
the same too.

**If the master PC/internet is down:** the remote still shows the last data and
people can still log in, but a Save shows *"Master server unreachable — nothing
was changed, try again"*. When the master is back everything continues by itself.

**Safety built in**
- The master only accepts forwarded actions that carry a valid signature, arrive through the tunnel (127.0.0.1), are less than 2 minutes old, **and carry a request ID that was never used before** (a captured request cannot be replayed).
- Gateway tokens/passwords and login sessions are blanked in the copy sent to the remote. The remote never holds them.
- The remote **does not write to its database copy** (login uses a check that never re-saves anything; page counter, usage tracking, "last login" and the activity log are not written there). Instead the remote **reports** page views, logins/logouts and every action to the master, which records them (page counter, each user's total usage time, activity log with the user's real IP) exactly as if the user had used the master site. Tested: the file is byte-for-byte unchanged after login, 60+ page loads and logout. (The copy is technically a normal file, so anything written there by mistake would simply be thrown away at the next refresh — it can never reach the master.)
- Code reaches GitHub **only when you run `./deploy/publish.sh`**. It checks syntax of every file, every template and Django, shows you the list of changed files and asks before publishing. It stages **only** the `sysmonitor/` folder (plus `.gitignore`), never other projects in the same Git repository (e.g. `coxcls-access-system/`). The remote rolls back by itself if bad code still arrives some other way.
- `publish.sh` never runs `git pull`, `rebase` or `autostash`. It fetches first and stops safely if `origin/main` contains work that the local checkout does not already contain.
- Notification gateway secrets stay scrubbed from the mirror database. When a Save is forwarded from the remote, a blank token/password means **keep the existing master secret**; typing a new secret replaces it.
- `.env`, databases, media and backups are never committed to GitHub.
- Changing a password on the remote logs that user out on purpose ("Password changed. Please log in again with your new password.").

Do the steps **in order**. After each step run the **Check** and don't continue until it passes.
Commands marked **[MASTER]** are run on the 24/7 PC (user `nanolab`), **[REMOTE]** on the app.bsccl.com server.

---
## STEP 1 — [MASTER] Back up, check how the website runs, install the update

**1a. Backup (30 seconds, do not skip)**
```bash
cd /home/nanolab/Desktop
cp -a sysmonitor sysmonitor_before_mirror_$(date +%Y%m%d_%H%M)
```
**1b. Look at how your website is running right now** (so we do not restart the wrong thing):
```bash
systemctl status sysmonitor-web --no-pager | head -8
ps -ef | grep -E 'manage.py runserver|gunicorn' | grep -v grep
```
Write down what you see — it decides step 1e:
- **Case A:** `sysmonitor-web` is `active (running)` under systemd → normal case.
- **Case B:** the website runs from a terminal/other process (not from systemd) → we will **not** restart it with systemctl.

**1c. Install the update** — the file is `sysmonitor-mirror-update-v7.zip`:
```bash
cd /home/nanolab/Desktop/sysmonitor && source venv/bin/activate
unzip -o ~/Downloads/sysmonitor-mirror-update-v7.zip
chmod +x deploy/*.sh
python deploy/preflight.py
```
✅ **Check:** `preflight OK`. (The new files do nothing yet — the mirror features stay switched off until the settings in 1d exist. If your website auto-reloads it may blink once.)

**1d. Add the settings** to `.env`. First make a secret:
```bash
python3 -c "import secrets;print(secrets.token_urlsafe(40))"
```
Copy the printed text. Open `.env` (`nano .env`), and add the lines from `.env.mirror-local.example`, putting the copied text after `MIRROR_SHARED_SECRET=`. (Keep this secret — the remote needs the same one in Step 6.)

**1e. Make the website read the new settings**
- **Case A (systemd):** `sudo systemctl restart sysmonitor-web` (a few seconds of downtime; the ping/monitoring services keep running untouched).
- **Case B (`runserver` started by hand, auto-reload):** `touch sysmonitor/settings.py` — the auto-reload restarts it and re-reads `.env`. If it does not, restart it the way you normally start it.

✅ **Check:** open http://192.168.30.48:8000/ — log in, the dashboard works as before. The pinging, generator and notification services are not touched by this step.

---
## STEP 2 — [MASTER] Publish the new code to GitHub
```bash
cd /home/nanolab/Desktop/sysmonitor
./deploy/publish.sh
```
It prints the list of changed files (all inside `sysmonitor/`, plus `.gitignore`). **Read it** — if you see anything that is not SysMonitor, answer `n`. Answer `y` to publish.
If another project in the same Git repository (e.g. `coxcls-access-system`) has *staged* changes, the script stops and lists them — commit or unstage them (`git restore --staged <file>`) and run it again. Unstaged work in other projects is never touched.

✅ **Check:** it prints `Published.` Open https://github.com/JsayeedR/sysmonitor — the latest commit is "SysMonitor: …" and the folder `sysmonitor/deploy` exists. (If it prints `REFUSING` or `PREFLIGHT FAILED`, read the message — it tells you what to fix.)

---
## STEP 3 — [REMOTE] Prepare the server (nothing existing is touched)
This server already runs IPLC, PollProject and DailyLedger. We only **add** a new folder, a new Python environment and (later) one new service. Log in as `app-admin` (SSH port 3048).
```bash
# 3a. Safety: nothing may already use our port, and take a backup of the nginx settings
sudo ss -lntp | grep -E ":8020|:18000" || echo "ports 8020 and 18000 are free"
sudo cp -a /etc/nginx ~/nginx_backup_before_sysmonitor
# 3b. Tools (git, venv, rsync are probably there already; this only adds what is missing)
sudo apt install -y git python3-venv rsync
# 3c. The code (public repo, no password needed) and its own Python environment
git clone https://github.com/JsayeedR/sysmonitor /home/app-admin/sysmonitor/repo
python3 -m venv /home/app-admin/sysmonitor/venv
/home/app-admin/sysmonitor/venv/bin/pip install -r /home/app-admin/sysmonitor/repo/sysmonitor/requirements-mirror.txt
# 3d. Let ONLY the update job restart ONLY our website (no password prompt)
echo 'app-admin ALL=(root) NOPASSWD: /usr/bin/systemctl restart sysmonitor-remote-web' | sudo tee /etc/sudoers.d/sysmonitor-restart
sudo chmod 440 /etc/sudoers.d/sysmonitor-restart && sudo visudo -c
```
✅ **Check:** `ls /home/app-admin/sysmonitor/repo/sysmonitor/manage.py` shows the file; `/home/app-admin/sysmonitor/venv/bin/python -c "import django;print(django.__version__)"` prints `5.2.14`; `sudo visudo -c` says `parsed OK`.

---
## STEP 4 — Let the master log in to the remote (key, no password)
**[MASTER]**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/sysmonitor_ed25519 -N ""
cat ~/.ssh/sysmonitor_ed25519.pub
```
Copy the one printed line. **[REMOTE]** — add it to the end of `~/.ssh/authorized_keys` **with these limits in front of it** (the key may not open a terminal, and may open only the one tunnel port):
```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo 'no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:18000" PASTE_THE_PUBLIC_KEY_LINE_HERE' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```
✅ **Check [MASTER]:** `ssh -i ~/.ssh/sysmonitor_ed25519 -p 3048 app-admin@app.bsccl.com echo connected` prints `connected`. (Your normal password/key login for `app-admin` is not affected.)

---
## STEP 5 — [MASTER] Send the first copy of the data
```bash
cd /home/nanolab/Desktop/sysmonitor && source venv/bin/activate
python monitor/mirror_push.py
python monitor/mirror_push.py --full      # sends profile pictures (code comes from GitHub)
```
✅ **Check [REMOTE]:** `ls /home/app-admin/sysmonitor/repo/sysmonitor` shows `mirror-xxxxxxxxxxxxxxxx.sqlite3`, `mirror_current`, `mirror_meta.json`, and `media/`.

---
## STEP 6 — [REMOTE] Start the website
**6a. Settings file**
```bash
cd /home/app-admin/sysmonitor/repo/sysmonitor
cp .env.mirror-remote.example .env
nano .env
chmod 600 .env
```
Replace the two `<…>` placeholders: a **new** secret key (`python3 -c "import secrets;print(secrets.token_urlsafe(50))"`) and the **same** `MIRROR_SHARED_SECRET` as the master's `.env`.

**6b. Start**
```bash
sudo cp /home/app-admin/sysmonitor/repo/sysmonitor/systemd/sysmonitor-remote-web.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sysmonitor-remote-web
curl -s -o /dev/null -w "%{http_code}\n" -H "SCRIPT_NAME: /sysmonitor" http://127.0.0.1:8020/sysmonitor/login/
```
✅ **Check:** prints `200`. (Other apps unaffected: `systemctl is-active gunicorn dailyledger pollproject` → three `active`.)

**6c. Public address (add to the EXISTING https://app.bsccl.com site — no new site, no certbot)**
First find which file holds the main app.bsccl.com HTTPS block (the one that sends `/` to port 4080):
```bash
sudo grep -ln "127.0.0.1:4080" /etc/nginx/sites-enabled/* /etc/nginx/conf.d/* 2>/dev/null
```
**Stop here and send me that file name and its content** (`sudo cat <file>`) — I will tell you the exact line to add and where. The change itself is only: copy `deploy/nginx-sysmonitor.conf` to `/etc/nginx/snippets/sysmonitor-locations.conf` and add one `include` line inside the 443 block; then `sudo nginx -t` must say `successful` before `sudo systemctl reload nginx`. If `nginx -t` complains, nothing is reloaded and nothing breaks.

✅ **Check:** open https://app.bsccl.com/sysmonitor/ → login page. Log in → dashboard with the blue banner. Then confirm https://app.bsccl.com/, /poll/ and /dailyledger/ still open as before. **Saving does not work yet** — that is the next step.

---
## STEP 7 — [MASTER] Open the private tunnel (this makes Save work)
```bash
cd /home/nanolab/Desktop/sysmonitor
sudo cp systemd/sysmonitor-mirror-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sysmonitor-mirror-tunnel
systemctl status sysmonitor-mirror-tunnel --no-pager | head -5
```
✅ **Check [REMOTE]:** `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18000/login/` prints `200` (that is the master's website, reached through the tunnel).

**Final test of Save** (harmless): on https://app.bsccl.com/sysmonitor open **Generator → Generator Log**, add an entry with note "remote test", then check it appears on the **master** page http://192.168.30.48:8000/generator-log/ and delete it there. Also look at **Activity log** on the master: your login shows *"(via remote site)"*, and the log entry shows your real IP address.

---
## STEP 8 — Switch on the automatic jobs
**[MASTER]**
```bash
cd /home/nanolab/Desktop/sysmonitor
sudo cp systemd/sysmonitor-mirror-push.* systemd/sysmonitor-mirror-full.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sysmonitor-mirror-push.timer sysmonitor-mirror-full.timer
systemctl list-timers 'sysmonitor-mirror*' --no-pager
```
**[REMOTE]**
```bash
sudo cp /home/app-admin/sysmonitor/repo/sysmonitor/systemd/sysmonitor-remote-update.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sysmonitor-remote-update.timer
```
✅ **Check:** on the master, change something on the master site (e.g. add a generator log entry): it appears on the remote within ~1 minute; the banner says "data is 12s old". Add a harmless comment line to any `.py` file, run `./deploy/publish.sh` (answer `y`): a new commit appears on GitHub, and ≤5 min later `journalctl -u sysmonitor-remote-update -n 3` on the remote says `updated …`.

**Done.** The remote is now a full working copy.

---
## Everyday use
- **Update the code:** edit and test on the master as usual. When you are happy with it: `./deploy/publish.sh` (answer `y`). Within 5 minutes it is live on the remote. To go faster: run `/home/app-admin/sysmonitor/repo/sysmonitor/deploy/remote-update.sh` on the remote. Half-finished work is never published by itself.
- **Is it healthy?** The banner on the remote shows how old the data is (red = master/tunnel problem).
- **Changed `requirements-mirror.txt`?** The remote installs it automatically.
- **Camera / other new features** later: just edit on the master — same path.

## Troubleshooting
| Problem | Fix |
|---|---|
| Banner red "data is N min old" | Master offline or push failing: `journalctl -u sysmonitor-mirror-push -n 20` on the master. After ~5 min of failures an ALARM appears in the master's event log. |
| Save on remote says "Master server unreachable" | Tunnel down: `systemctl status sysmonitor-mirror-tunnel` on the master (it restarts itself every 10 s). Check Step 4 key works. |
| Save on remote says 403 / "bad signature" | `MIRROR_SHARED_SECRET` differs between master and remote `.env`, or the two clocks differ by more than 2 min (`timedatectl` on both; "repeated request" = 409 means the same request was sent twice and is refused on purpose). Restart `sysmonitor-web` (master) / `sysmonitor-remote-web` (remote) after fixing. |
| Links go to `/login/` (404) on the remote | nginx `proxy_pass` must have **no** path, and `proxy_set_header SCRIPT_NAME /sysmonitor;` must be present. |
| Login on remote: CSRF error | `MIRROR_TRUSTED_ORIGINS=https://app.bsccl.com` and `MIRROR_HTTPS=1` in remote `.env`; open the site with https. |
| `publish.sh` says PREFLIGHT FAILED | Read the file/line it prints, fix it. Nothing broken is ever published. |
| Remote did not update | `journalctl -u sysmonitor-remote-update -n 20`. "rolled back" means the new GitHub code failed the check — fix on the master, it recovers by itself. |
| After changing a password on the remote you land on the login page | Expected and intended: log in again with the new password. |

## How to undo everything (nothing on the master is lost)
**[MASTER]** `sudo systemctl disable --now sysmonitor-mirror-tunnel sysmonitor-mirror-push.timer sysmonitor-mirror-full.timer`
and remove the `MIRROR_*` lines from `.env`, then `sudo systemctl restart sysmonitor-web`. The master then behaves exactly as before. Your pre-update backup is `~/Desktop/sysmonitor_before_mirror_*`.

## Notes
- Disk on the remote: up to ~20 database snapshots are kept for 10 min (≈ 20 × database size).
- Django admin (`/admin/`) works only on the master.
- Page views, user activity time and the activity log include what people do on the remote (reported to the master). The footer counter on the remote shows the master's number as of the last data refresh (≤ 30 s behind).
- The Uptime Kuma, PAC and System pages are fetched live from the master; if the master is down they show the "unreachable" message.
