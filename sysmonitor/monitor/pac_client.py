"""
monitor/pac_client.py
──────────────────────
Client for the SMW6PAC precision air-conditioning controllers.

These are three redundant Vertiv PAC controllers (192.168.254.12/.13/.14).
Only one is normally the active/serving unit at a time (the others may be
resting/standby in an N+2 redundancy setup), so this module tries each IP
in order and returns data from the first one that actually responds.

The controllers expose an internal AJAX API (no login required) that the
official web UI itself uses:
  - POST /anonymous/getvar.csv   — fetch named variables (fan%, temps, etc.)
  - GET  /anonymous/alarms.cgi?action=getActive — currently active alarms

We never touch the controller's own HTML/JS UI at all — only these two
data endpoints — so nothing in the response depends on that UI's login
button, top bar, or URL.
"""

import requests
from datetime import datetime
import pytz

BDT = pytz.timezone("Asia/Dhaka")


def _fmt_since(iso_str):
    """Parse a controller ISO 8601 UTC timestamp and format it in Asia/
    Dhaka local time, matching the rest of the app's convention."""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.astimezone(BDT).strftime("%d/%m/%Y %I:%M:%S %p")
    except (ValueError, TypeError):
        return iso_str

PAC_IPS = ["192.168.254.12", "192.168.254.13", "192.168.254.14"]
TIMEOUT = 4  # seconds — fail fast so the fallback chain doesn't hang the page

VAR_NAMES = [
    "FanReq_Mask", "FCReq_Mask", "DXCWReq_Mask", "HeatReq_Mask",
    "DehumReq_Mask", "HumReq_Mask",
    "Tw_TSetP", "Tw_RetT", "Tw_SupT", "Tw_RemT",
    "Tw_HSetP", "Tw_RetH", "Tw_RemH",
    "Cfg_tw.IdUnit",
]

NA_SENTINELS = {-32768.0, -3276.8}

ALARM_LABELS = {
    155: ("Unit On", "ok"),
    212: ("Standby", "info"),
    108: ("Network Fail", "danger"),
    211: ("Alarm Off", "warn"),
    207: ("Remote Off", "warn"),
    153: ("Power On", "info"),
    154: ("Power Off", "danger"),
    152: ("Ultracap Supply", "info"),
}


def _fmt_num(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _is_na(value):
    return value is None or any(abs(value - s) < 0.01 for s in NA_SENTINELS)


def _fetch_vars(base_url):
    data = "&".join(f"name={n}" for n in VAR_NAMES)
    resp = requests.post(
        f"{base_url}/anonymous/getvar.csv",
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        data=data,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    lines = resp.text.strip().splitlines()
    result = {}
    for line in lines[1:]:
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) < 6:
            continue
        name = parts[0]
        val = parts[-1]
        result[name] = _fmt_num(val)
    return result


def _fetch_active_alarms(base_url):
    resp = requests.get(
        f"{base_url}/anonymous/alarms.cgi",
        params={"action": "getActive"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    alarms = []
    for line in resp.text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            alarm_id = int(parts[0])
        except ValueError:
            continue
        raw_name = parts[1]
        since = parts[2]
        label, level = ALARM_LABELS.get(alarm_id, (raw_name, "info"))
        alarms.append({
            "id": alarm_id,
            "name": raw_name,
            "label": label,
            "level": level,
            "since": since,
        })
    alarms.sort(key=lambda a: a["since"], reverse=True)
    for a in alarms:
        a["since"] = _fmt_since(a["since"])
    return alarms

def _run_state(alarms):
    """Derive a simple ON / STANDBY / OFF run-state label + color from
    the active-alarm list (Al_UnitOn.Active=155, Al_Standby.Active=212)."""
    ids = {a["id"] for a in alarms}
    if 155 in ids:
        return {"label": "ON", "color": "green"}
    if 212 in ids:
        return {"label": "STANDBY", "color": "yellow"}
    return {"label": "OFF", "color": "red"}

def _fetch_one(ip):
    """Fetch status for a single PAC controller. Never raises — returns
    an 'ok': False dict on any failure so one bad unit never blocks the
    others."""
    base_url = f"http://{ip}"
    try:
        v = _fetch_vars(base_url)
        alarms = _fetch_active_alarms(base_url)
    except Exception as e:
        return {"ok": False, "ip": ip, "error": str(e)}

    temp_remote = v.get("Tw_RemT")
    hum_remote = v.get("Tw_RemH")

    return {
        "ok": True,
        "ip": ip,
        "unit_id": v.get("Cfg_tw.IdUnit"),
        "percentages": {
            "fan":   v.get("FanReq_Mask"),
            "fc":    v.get("FCReq_Mask"),
            "cool":  v.get("DXCWReq_Mask"),
            "heat":  v.get("HeatReq_Mask"),
            "dehum": v.get("DehumReq_Mask"),
            "humi":  v.get("HumReq_Mask"),
        },
        "temperature": {
            "setpoint": v.get("Tw_TSetP"),
            "return":   v.get("Tw_RetT"),
            "supply":   v.get("Tw_SupT"),
            "remote":   None if _is_na(temp_remote) else temp_remote,
        },
        "humidity": {
            "setpoint": v.get("Tw_HSetP"),
            "return":   v.get("Tw_RetH"),
            "remote":   None if _is_na(hum_remote) else hum_remote,
        },
        "alarms": alarms,
        "run_state": _run_state(alarms),
    }


def get_all_pac_status():
    """Fetch status for all 3 PAC controllers independently. Each one
    that fails to respond just gets 'ok': False — it never blocks the
    others from displaying."""
    return [_fetch_one(ip) for ip in PAC_IPS]
