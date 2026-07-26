# ⚡ SysMonitor — Power Outage & Network Monitoring System

**Developed by Jikrul Sayeed** · KUET · EEE · 2k15  
**Contact:** jikrul.sayeed@gmail.com · [linkedin.com/in/jikrulsayeed](https://linkedin.com/in/jikrulsayeed)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Device Roles — Holder vs NVR](#3-device-roles--holder-vs-nvr)
4. [Outage Cycle Logic](#4-outage-cycle-logic)
5. [Cycle Types Explained](#5-cycle-types-explained)
6. [Monitor Phase States](#6-monitor-phase-states)
7. [Data Storage & Retention](#7-data-storage--retention)
8. [Daily Backup System](#8-daily-backup-system)
9. [Project File Structure](#9-project-file-structure)
10. [Fresh Installation Guide](#10-fresh-installation-guide)
11. [Systemd Services](#11-systemd-services)
12. [Transferring to a New Machine](#12-transferring-to-a-new-machine)
13. [User Roles & Access Control](#13-user-roles--access-control)
14. [Dashboard Features](#14-dashboard-features)
15. [API Endpoints](#15-api-endpoints)
16. [Common Issues & Fixes](#16-common-issues--fixes)
17. [Known Limitations](#17-known-limitations)

---

## 1. Project Overview

SysMonitor is a Django-based real-time network and power outage monitoring system
designed for a facility that experiences frequent power cuts and uses a generator
and Automatic Transfer Switch (ATS) for backup power.

The system continuously pings two key network devices every 10 seconds, detects
power outage cycles, tracks generator runtime, logs all events, and presents
everything on a live auto-refreshing web dashboard.

**Core purpose:**
- Detect power outages automatically
- Track how long PDB (Power Distribution Board) was out
- Track generator runtime per outage
- Maintain a permanent historical log of all outage cycles
- Alert on abnormal situations (AVR stuck, total failure)
- Provide daily outage summaries with total downtime

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Ubuntu Linux Server                   │
│                                                         │
│  ┌─────────────────┐      ┌─────────────────────────┐  │
│  │ sysmonitor-ping │      │   sysmonitor-web         │  │
│  │ (ping_monitor.py)│     │   (Django on port 8000)  │  │
│  │                 │      │                          │  │
│  │ Pings every 10s │      │ Serves dashboard & API   │  │
│  │ Writes to DB    │      │ Reads from DB            │  │
│  └────────┬────────┘      └──────────────────────────┘  │
│           │                                             │
│           ▼                                             │
│  ┌─────────────────┐      ┌─────────────────────────┐  │
│  │   db.sqlite3    │      │  sysmonitor-backup       │  │
│  │                 │      │  (daily at 00:01 BDT)    │  │
│  │  All data lives │      │  copies db.sqlite3 to    │  │
│  │  here in UTC    │      │  backups/ folder         │  │
│  └─────────────────┘      └─────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
  192.168.30.56                  192.168.1.155
  Holder Device                  NVR Device
  (PDB indicator)                (Generator indicator)
```

---

## 3. Device Roles — Holder vs NVR

The entire outage detection logic is built around two devices:

### Holder (Primary — PDB Power Indicator)
- **IP:** `192.168.30.56`
- **Role:** Master device — its UP/DOWN state directly reflects whether
  PDB (grid power) is available
- **DOWN** = Power outage has started
- **UP** = Grid power has been restored

### NVR (Secondary — Generator Indicator)
- **IP:** `192.168.1.155`
- **Role:** Slave/cross-check device — runs on UPS/generator power
- **DOWN with Holder DOWN** = Full power outage confirmed
- **UP while Holder DOWN** = Generator has kicked in and is running
- **Brief DOWN after Holder UP** = ATS is switching back from generator
  to grid power (expected behavior)

### Why two devices?
A single device could give false readings (WiFi dropout, device reboot).
Using both together allows the system to distinguish between:
- Real power outage (both DOWN)
- ATS switching (NVR briefly DOWN, Holder UP)
- WiFi/network lag (Holder briefly DOWN, NVR UP)
- Generator running (NVR UP, Holder still DOWN)

> **Important:** Device names in the database must contain the words
> "holder" and "nvr" (case-insensitive) for the monitor to find them.
> Do not rename them to something unrelated.

---

## 4. Outage Cycle Logic

A full normal power outage cycle follows this sequence:

```
Step 1: Both Holder + NVR go DOWN
        → outage_start recorded
        → System status: OUTAGE 🔴
        → Phase: OUTAGE

Step 2: NVR comes back UP (generator started)
        → gen_start recorded
        → System status: GENERATOR 🟡
        → Phase: GEN_RUNNING

Step 3: Holder comes back UP (PDB/grid restored)
        → pdb_restored recorded
        → System status: ATS 🔵
        → Phase: PDB_RESTORED
        → Watching for ATS switchback...

Step 4: NVR briefly goes DOWN then comes back UP
        → ATS is switching from generator back to grid
        → cycle_end recorded
        → Cycle marked COMPLETE (type: NORMAL)
        → System status: NORMAL 🟢
        → Phase: NORMAL

Durations calculated:
  PDB outage duration = pdb_restored - outage_start
  Generator runtime   = cycle_end - gen_start
```

### Startup Recovery
When `ping_monitor.py` starts or restarts, it checks the current state
of both devices and recovers gracefully:

| Startup State | Action |
|---|---|
| Both UP | Close any orphaned incomplete cycles, set NORMAL |
| Holder DOWN + NVR UP | Resume latest incomplete cycle, restore timers |
| Both DOWN | Resume or create cycle, set OUTAGE |
| NVR DOWN + Holder UP | Close orphans, set NVR_BLIP, monitor |

---

## 5. Cycle Types Explained

| Type | Color | Meaning |
|---|---|---|
| `NORMAL` | 🟢 Green | Clean full cycle — outage, generator ran, PDB restored, ATS switched back |
| `ATS_ONLY` | 🔵 Blue | ATS switchover only, no real outage, NVR briefly down with holder up |
| `INCOMPLETE` | 🟡 Yellow | Still in progress — active outage cycle |
| `ALARM` | 🟠 Orange | AVR/ATS stuck — PDB restored but NVR still on generator after 5 minutes |
| `CRITICAL` | 🔴 Red | Total power failure — both devices DOWN for more than 10 minutes |

### Alarm Thresholds (in `ping_monitor.py`)
```python
ATS_BLIP_THRESHOLD  = 180   # seconds — NVR down less than this = ATS blip only
AVR_STUCK_THRESHOLD = 300   # seconds — PDB restored but NVR on gen after this = ALARM
CRITICAL_THRESHOLD  = 600   # seconds — both down longer than this = CRITICAL
```

---

## 6. Monitor Phase States

The in-memory `MonitorState` object tracks the current phase:

```
NORMAL       → Everything fine, both devices UP
NVR_BLIP     → NVR briefly down, Holder still up (ATS switching or AVR issue)
OUTAGE       → Both down, waiting for generator to start
GEN_RUNNING  → NVR back up on generator, Holder still down
PDB_RESTORED → Holder came back up, waiting for NVR ATS switchback blip
SWITCHBACK   → NVR briefly down after PDB restored (ATS switching to grid)
```

> **Note:** Phase state lives in memory only. On restart, it is restored
> from the database (incomplete OutageCycle records and current ping results).

---

## 7. Data Storage & Retention

All data is stored in `db.sqlite3` in UTC. Display is always converted
to Bangladesh Time (BDT = UTC+6) in 12-hour format.

| Table | What it stores | Retention |
|---|---|---|
| `DeviceStatus` | Raw ping results (UP/DOWN, response ms) | Last 1000 rows per device (~2.7 hours) |
| `OutageCycle` | Full outage cycle records with durations | **Forever** ✅ |
| `Event` | All state change events (OUTAGE, GEN-UP, etc.) | **Forever** ✅ |
| `ActivityLog` | User actions (login, device edits, etc.) | **Forever** ✅ |
| `SystemStatus` | Current live system state | 1 row only, updated in place |
| `Device` | Device list (name, IP, description) | Until manually deleted |
| `UserProfile` | User roles | Until manually deleted |

### Why DeviceStatus is kept short
DeviceStatus generates 8,640 rows per device per day. Keeping it forever
would bloat the database massively. Since it is only used for the live
dashboard display (current status light), only the latest 1000 rows are
needed. Historical outage data is preserved in `OutageCycle` and `Event`.

### Daily Summary totals
The daily summary panel on the dashboard shows total outage duration
counting only **NORMAL** completed cycles. ALARM and CRITICAL cycles
are shown in the list but flagged separately. ATS_ONLY cycles are not
counted toward outage duration as they are not real power cuts.

---

## 8. Daily Backup System

A daily backup of `db.sqlite3` runs automatically at **00:01 BDT** every night.

| Setting | Value |
|---|---|
| Script | `monitor/backup.py` |
| Trigger | `sysmonitor-backup.timer` (systemd) |
| Time | 00:01 BDT = 18:01 UTC |
| Location | `sysmonitor/backups/` |
| Filename format | `db_backup_YYYY-MM-DD_HH-MM.sqlite3` |
| Retention | Last 90 daily backups (older ones auto-deleted) |
| Success event | `INFO` event logged to dashboard |
| Failure event | `CRITICAL` event logged to dashboard |

### Manual backup anytime
```bash
sudo systemctl start sysmonitor-backup.service
```

### Restore from backup
```bash
# Stop services first
sudo systemctl stop sysmonitor-web sysmonitor-ping

# Replace database
cp backups/db_backup_YYYY-MM-DD_HH-MM.sqlite3 db.sqlite3

# Restart services
sudo systemctl start sysmonitor-web sysmonitor-ping
```

---

## 9. Project File Structure

```
sysmonitor/
├── db.sqlite3                  ← SQLite database (all data)
├── manage.py                   ← Django management
├── requirements.txt            ← Python dependencies
├── README.md                   ← This file
├── backups/                    ← Daily DB backups (auto-created)
│   └── db_backup_YYYY-MM-DD_HH-MM.sqlite3
│
├── systemd/                    ← Systemd service/timer files (for portability)
│   ├── sysmonitor-web.service
│   ├── sysmonitor-ping.service
│   ├── sysmonitor-backup.service
│   └── sysmonitor-backup.timer
│
├── monitor/                    ← Main Django app
│   ├── models.py               ← Database models
│   ├── views.py                ← Web views + API endpoints
│   ├── ping_monitor.py         ← Ping loop + outage detection logic
│   ├── backup.py               ← Daily backup script
│   ├── admin.py                ← Django admin config
│   ├── apps.py
│   ├── tests.py
│   ├── migrations/             ← Database migrations
│   │   ├── 0001_initial.py
│   │   ├── 0002_activitylog.py
│   │   └── 0003_outagecycle.py
│   └── templates/
│       └── monitor/
│           ├── dashboard.html          ← Main live dashboard
│           ├── login.html
│           ├── denied.html
│           ├── event_log.html          ← Full event history
│           ├── activity_log.html       ← Admin audit trail
│           ├── device_list.html
│           ├── device_form.html
│           ├── device_confirm_delete.html
│           ├── user_list.html
│           ├── user_form.html
│           └── user_confirm_delete.html
│
└── sysmonitor/                 ← Django project config
    ├── settings.py             ← Django settings
    ├── urls.py                 ← URL routing
    ├── wsgi.py
    └── asgi.py
```

---

## 10. Fresh Installation Guide

### Prerequisites
- Ubuntu 20.04+ or similar Linux
- Python 3.10+
- `ping` command available (`iputils-ping`)

### Step 1 — Clone or copy the project
```bash
cp -r sysmonitor/ ~/Desktop/sysmonitor
cd ~/Desktop/sysmonitor
```

### Step 2 — Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3 — Install dependencies
```bash
pip install -r requirements.txt
```

### Step 4 — Run database migrations
```bash
python manage.py migrate
```

### Step 5 — Create admin user
```bash
python manage.py createsuperuser
```

### Step 6 — Add devices via web panel
Start the server temporarily:
```bash
python manage.py runserver 0.0.0.0:8000
```
Go to `http://YOUR_IP:8000/devices/` and add:
- **Holder device** — name must contain the word `holder`, IP: your holder device IP
- **NVR device** — name must contain the word `nvr`, IP: your NVR device IP

Then stop the server (`Ctrl+C`) and proceed to systemd setup.

### Step 7 — Set up systemd services
```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo cp systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sysmonitor-web
sudo systemctl enable sysmonitor-ping
sudo systemctl enable sysmonitor-backup.timer
sudo systemctl start sysmonitor-web
sudo systemctl start sysmonitor-ping
sudo systemctl start sysmonitor-backup.timer
```

### Step 8 — Verify everything is running
```bash
sudo systemctl status sysmonitor-web
sudo systemctl status sysmonitor-ping
sudo systemctl list-timers | grep backup
```

Dashboard is now live at `http://YOUR_IP:8000/`

---

## 11. Systemd Services

### sysmonitor-web
Runs the Django development server on port 8000.
```bash
sudo systemctl start sysmonitor-web
sudo systemctl stop sysmonitor-web
sudo systemctl restart sysmonitor-web
sudo journalctl -u sysmonitor-web -f
```

### sysmonitor-ping
Runs `ping_monitor.py` — the outage detection loop.
```bash
sudo systemctl start sysmonitor-ping
sudo systemctl stop sysmonitor-ping
sudo systemctl restart sysmonitor-ping
sudo journalctl -u sysmonitor-ping -f
sudo journalctl -u sysmonitor-ping -n 100 --no-pager
```

### sysmonitor-backup (timer)
Runs `backup.py` daily at 00:01 BDT.
```bash
# Check next scheduled run
sudo systemctl list-timers | grep backup

# Run backup manually right now
sudo systemctl start sysmonitor-backup.service

# View backup logs
sudo journalctl -u sysmonitor-backup.service
```

### After any code change
```bash
sudo systemctl restart sysmonitor-ping
sudo systemctl restart sysmonitor-web
```

---

## 12. Transferring to a New Machine

### Step 1 — Copy the whole project folder
The `systemd/` folder inside the project contains all service files,
so the entire setup travels with the project in one folder.

```bash
# On old machine — pack it up
tar -czf sysmonitor_backup.tar.gz ~/Desktop/sysmonitor/

# Transfer to new machine (via USB, SCP, etc.)
scp sysmonitor_backup.tar.gz user@newmachine:~/Desktop/
```

### Step 2 — On new machine
```bash
cd ~/Desktop
tar -xzf sysmonitor_backup.tar.gz
cd sysmonitor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Update service files if username changed
If the new machine has a different username, edit the service files:
```bash
nano systemd/sysmonitor-web.service
nano systemd/sysmonitor-ping.service
nano systemd/sysmonitor-backup.service
```
Update the `User=` and `WorkingDirectory=` and `ExecStart=` lines
to match the new machine's username and path.

### Step 4 — Install services and start
```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo cp systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sysmonitor-web sysmonitor-ping sysmonitor-backup.timer
sudo systemctl start sysmonitor-web sysmonitor-ping sysmonitor-backup.timer
```

> **Note:** The `db.sqlite3` file travels with the project and contains
> all historical outage data. No data is lost during transfer.

---

## 13. User Roles & Access Control

| Role | Dashboard | Events | Activity Log | Devices | Users |
|---|---|---|---|---|---|
| `admin` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `user` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `viewer` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `guest` | ✅ (limited) | ❌ | ❌ | ❌ | ❌ |

- **admin** — Full access, can manage users and devices
- **user/viewer** — Can view dashboard and event log
- **guest** — Dashboard only, no event history

Superusers (created via `createsuperuser`) always have admin-level access
regardless of their `UserProfile` role.

All login attempts, user changes, and device changes are logged to
the Activity Log (visible to admin only).

---

## 14. Dashboard Features

### Current Status Banner
Live system status with color coding:
- 🟢 `NORMAL` — All systems normal
- 🔴 `OUTAGE` — Power outage in progress (pulses/blinks)
- 🟡 `GENERATOR` — Generator running, PDB still out
- 🔵 `ATS` — ATS switching, PDB restored, waiting for NVR
- 🟠 `DEVICE_DOWN` — A device is unreachable

The "Updated" timestamp shows the exact time the browser last
fetched fresh data — always in BDT 12-hour format, synced with
the 10-second refresh countdown.

### Last Power Cycle Banner
Shows the result of the most recent completed outage cycle
or indicates if a cycle is currently in progress.

### Device Status Cards
Live UP/DOWN indicators with ping response time in milliseconds.
Animated green pulse = UP, red pulse = DOWN.

### Daily Summary Panel (PDB Outage / Generator Runtime)
- Date selector buttons to browse historical days
- Table of all outage cycles for the selected date
- Each row: Start Time, End Time, Duration, Cycle Type
- Day status: 🟢 Complete / 🟡 Partial / 🟠 Alarm / 🔴 Critical
- Today always shows 🟡 Partial (day not finished yet)
- Total outage duration at bottom (NORMAL cycles only, in h/min and minutes)

### Recent Events Panel
Live scrolling event log with color-coded badges:
- `OUTAGE` 🔴, `GEN-UP` 🟡, `NORMAL` 🟢, `ATS` 🔵
- `CRITICAL` 🔴, `NOTICE` ⚪, `INFO` ⚪

Auto-refreshes every 10 seconds along with all other data.

---

## 15. API Endpoints

All endpoints require login.

### `GET /api/status/`
Returns live status for dashboard auto-refresh.
```json
{
  "devices":    [...],
  "overall":    "NORMAL",
  "note":       "All devices UP — normal operation",
  "updated_at": "11:45:32 PM",
  "events":     [...],
  "cycle":      { "state": "COMPLETE", ... }
}
```

### `GET /api/daily-summary/?date=YYYY-MM-DD`
Returns daily outage cycle summary. Date defaults to today in BDT.
```json
{
  "date":            "30/05/2026",
  "date_val":        "2026-05-30",
  "rows":            [...],
  "total_mins":      228,
  "day_complete":    false,
  "available_dates": ["2026-05-30", "2026-05-29"]
}
```

---

## 16. Common Issues & Fixes

### ping_monitor not detecting devices
```
ERROR: Holder or NVR device not found in database.
```
Make sure device names in the database contain the words `holder` and `nvr`.
Go to `http://YOUR_IP:8000/devices/` and check/rename them.

### Orphaned incomplete cycles showing on dashboard
This happens if ping_monitor crashed mid-outage. On next restart
with both devices UP, it auto-closes them. Or manually:
```bash
python manage.py shell
>>> from monitor.models import OutageCycle
>>> OutageCycle.objects.filter(is_complete=False, outage_start=None).delete()
```

### Dashboard shows wrong time
All times display in BDT (Asia/Dhaka). The "Updated" time on the
dashboard uses the browser's local clock — make sure the client
device's timezone is also set to Asia/Dhaka or the time will differ.

### Database getting large
DeviceStatus is auto-capped at 1000 rows per device. OutageCycle,
Event, and ActivityLog grow forever — this is intentional for history.
Run a manual backup and check size:
```bash
du -sh db.sqlite3
ls -lh backups/
```

### Service won't start after transfer to new machine
Edit the service files to update username and paths:
```bash
sudo nano /etc/systemd/system/sysmonitor-web.service
sudo systemctl daemon-reload
sudo systemctl restart sysmonitor-web
```

---

## 17. Known Limitations

- **SQLite** — suitable for single-facility use. For multi-location
  or high-concurrency use, migrate to PostgreSQL.
- **Django dev server** — the web service runs on Django's built-in
  server, not production-grade (Gunicorn/Nginx). Fine for LAN use.
- **In-memory state** — `MonitorState` phase is in RAM only. On crash
  it is reconstructed from DB on restart, but very short outages
  (under 10 seconds) during a restart window could be missed.
- **Two-device limit** — the monitor is hardcoded for one Holder and
  one NVR device. Multi-device outage correlation would need a rewrite
  of `ping_monitor.py`.
- **No email/SMS alerts** — all alerts are on-dashboard only.
  Push notifications or email on CRITICAL events are not implemented.

---

## Quick Reference Card

```
Start all services:
  sudo systemctl start sysmonitor-web sysmonitor-ping

Stop all services:
  sudo systemctl stop sysmonitor-web sysmonitor-ping

Restart after code change:
  sudo systemctl restart sysmonitor-web sysmonitor-ping

Watch live ping logs:
  sudo journalctl -u sysmonitor-ping -f

Watch live web logs:
  sudo journalctl -u sysmonitor-web -f

Manual backup now:
  sudo systemctl start sysmonitor-backup.service

Open Django shell:
  cd ~/Desktop/sysmonitor && source venv/bin/activate && python manage.py shell

Dashboard URL:
  http://YOUR_SERVER_IP:8000/
```

---

*SysMonitor © 2026 Jikrul Sayeed. All rights reserved.*
