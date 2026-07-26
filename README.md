# ⚡ SysMonitor

A real-time power outage & network monitoring tool built for **COX's Bazar Cable Landing Station (COXCLS)**, operated by **BSCPLC** — watching grid power, generator, and ATS behaviour around the clock, passively and automatically.

> ⚠️ **Passive monitoring only.** SysMonitor has no direct connectivity to power infrastructure. It observes network reachability of existing monitoring points and infers power state — it never sends commands to the generator, ATS, or any power hardware.

---

## 1. What is SysMonitor?

SysMonitor is a lightweight, self-hosted power & network outage monitoring tool built specifically for COX's Bazar CLS (COXCLS), a facility that relies on grid power (PDB) with a generator + Automatic Transfer Switch (ATS) as backup.

It runs entirely on the local network. Instead of someone on shift needing to notice a power drop and log it manually, SysMonitor watches two always-on network devices and infers the facility's power state from their reachability — then keeps a permanent, timestamped record of everything, automatically.

- 🔍 **Detects outages automatically** — no manual logging, the system notices the moment power drops
- ⏱️ **Tracks exact durations** — PDB outage time and generator runtime, down to the second
- 📚 **Permanent history** — every outage cycle and event preserved for reporting and audits
- 📢 **Instant alerts** — Telegram, WhatsApp, and email notifications the moment something changes

## 2. How it works

SysMonitor is a Django web application paired with a lightweight background monitor process. The two run as separate, independent services and communicate only through the shared database — a restart of one never takes down the other.

1. **Background monitor pings two reference points** — a "Holder" device (reflects grid/PDB power) and an "NVR" device (reflects generator/UPS power), pinged every few seconds
2. **State changes are cross-checked between the two** — comparing both devices filters out false alarms like brief WiFi drops or a single device rebooting
3. **Confirmed changes are written to the database** — outage start, generator start, power restore, and ATS switch-back are each timestamped
4. **The dashboard reflects it live** — auto-refreshing status, live device health, and a running event log
5. **Notifications fire in parallel** — the background monitor calls the notification dispatcher directly the moment a cycle-defining event happens

## 3. Architecture

Three independent services, one shared database:

| Service | Job |
|---|---|
| `sysmonitor-ping` | Pings the Holder & NVR devices continuously, writes confirmed state changes to the database, fires notifications instantly |
| `sysmonitor-web` | The Django app — serves the dashboard, report, events, admin pages, and the public docs page |
| `sysmonitor-backup` | A timer that snapshots the database daily, so a bad write or disk issue never means losing history |

```
Holder Device ──┐
                 ├──> Background Monitor ──> SQLite Database ──> Django Web App ──> Browser / Android App
NVR Device ─────┘              │                                       │
                                └──> Notification Gateways      Backup Timer
                                     (Telegram / WhatsApp / Email)  (daily snapshot)
```

## 4. The detection logic

A full, healthy outage cycle: grid drops → generator picks up the load → grid comes back → ATS switches the load back to grid power.

| Cycle Type | Meaning |
|---|---|
| 🟢 Normal | Clean cycle — outage, generator ran, power restored, ATS switched back cleanly |
| 🔵 ATS-only | Just a brief ATS switch blip — not a real power outage |
| 🟡 Incomplete | An outage is currently in progress |
| 🟠 Alarm | Power was restored but generator/ATS didn't switch back as expected — flagged for review |
| 🔴 Critical | An extended total failure — both reference points unreachable for a long stretch |
| ⚪ Manual | Entered or corrected by hand via System Tools — for an outage the automatic monitor missed |

**Why two devices instead of one?** A single ping target is unreliable — a device rebooting or a WiFi hiccup can look identical to a real power outage. Requiring *both* the Holder and NVR to agree before confirming a state change filters out one-off blips automatically.

A **maintenance mode** can be switched on before planned network work, pausing outage detection and notifications so planned downtime is never logged as a false power outage.

## 5. Features

- 📊 **Live Dashboard** — auto-refreshing status, device health, and daily outage summary
- 📋 **Event & Activity Logs** — full history of state changes plus an admin audit trail
- 🔧 **Generator Shifting Entry** — manual log of which generator is in auto mode and when
- 🛠️ **System Tools** — live internal state viewer, journal inspection, manual cycle correction
- 🧾 **Reports** — daily / historical outage & generator runtime reporting
- 👥 **Role-Based Access** — Admin, User, Viewer, and Guest roles, each with different visible pages
- 🙋 **Self-Service Profiles** — users manage contact details; sensitive fields need admin approval
- 💾 **Automated Backups** — database backed up daily, automatically
- 🔔 **Multi-Channel Alerts** — Telegram, WhatsApp, and email, configured per recipient
- 📱 **Companion Android App** — auto-detects local vs external network
- 🚧 **Maintenance Mode** — pause detection during planned network work
- ℹ️ **Public Docs Page** — readable by anyone, logged in or not, no secrets exposed

## 6. Tech stack

| Layer | Tech |
|---|---|
| Backend | Django (Python) |
| Database | SQLite, backed up automatically every day |
| Monitoring | Independent Python ping loop, decoupled from the web app |
| Frontend | Vanilla HTML / CSS / JS — no build step |
| Time handling | pytz — Asia/Dhaka, all timestamps normalised to local time |
| Notifications | Telegram Bot API, Meta WhatsApp Cloud API, Gmail SMTP |
| Process management | systemd services & timers |
| Mobile | Native Android wrapper (Java), WebView-based |

## 7. Notifications

| Channel | Use case |
|---|---|
| 🤖 Telegram Bot | Instant push alerts to any number of subscribed chats |
| 💬 WhatsApp | Business API-based alerts to configured phone numbers |
| ✉️ Email | SMTP-based alerts for recipients who prefer email |

**What triggers an alert:**
- **Outage start** — sent once, the moment the Holder device goes down
- **Critical** — sent once if both reference devices are unreachable for an extended period
- **Alarm** — sent once per cycle if the generator or ATS doesn't behave as expected
- **Cycle complete** — sent once the cycle closes cleanly

Each fires **at most once per cycle** — the dispatcher tracks what's already been sent so recipients never get duplicate alerts for the same event.

To receive real-time Telegram alerts, message **"Hi"** to the SysMonitor notification bot: `@SysMonitorCOXCLS_bot`.

## 8. Android App

A lightweight Android wrapper app for quick access to the dashboard from a phone. It automatically detects whether you're on the facility's local network or an external connection and loads the right address.

Features: auto network detection · swipe-to-refresh · connection retry · in-app log viewer.

Installing requires enabling "Install from unknown sources," since it's a direct APK download rather than a Play Store listing. Dashboard login credentials are issued privately by the COXCLS NOC team.

## 9. Roles & Access Control

| Role | Can see |
|---|---|
| **Admin** | Everything — Dashboard, Report, Events, Generator Shifting Entry, Devices, Users, Notifications, Activity Log, System Tools, plus profile-change approvals |
| **User** | Dashboard, Report, Events, Generator Shifting Entry |
| **Viewer** | Dashboard, Report, Events |
| **Guest** | Dashboard, Report |

When a user updates a sensitive profile field (email, mobile), the change is held as **pending** until an admin reviews and approves or rejects it, right inside the admin's Users tab.

## 10. FAQ

**Does SysMonitor control the generator or ATS?**
No. It's strictly passive — it only pings network devices to infer power state.

**What's the difference between Viewer and Guest?**
Both see Dashboard and Report. Viewer additionally sees the Events log; neither sees Generator Shifting Entry or any admin page.

**How are false alarms filtered out?**
By requiring both the Holder and NVR devices to agree on a state change before it's confirmed.

**What happens during planned network maintenance?**
Turn on Maintenance Mode first — it pauses outage detection and notifications for the duration you set.

**How often is the database backed up?**
Automatically, once a day, via a dedicated systemd timer.

**How do I get an account?**
Accounts are created privately by the COXCLS NOC team, who also assign roles.

---

## Developer

**MD. Jikrul Sayeed Hossain**
Developer · KUET, EEE (2K15) · COXCLS NOC, BSCPLC

Designed, built, and maintains SysMonitor end-to-end — the Django backend, outage-detection logic, notification system, and companion Android app.

- ✉️ jikrul.sayeed@gmail.com
- 🔗 [LinkedIn](https://linkedin.com/in/jikrulsayeed)

---

© 2026 COXCLS NOC, BSCPLC. All rights reserved.
