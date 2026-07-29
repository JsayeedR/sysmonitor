from uptime_kuma_api import UptimeKumaApi
from django.conf import settings


def _connect():
    api = UptimeKumaApi(settings.KUMA_URL)
    api.login(settings.KUMA_USERNAME, settings.KUMA_PASSWORD)
    return api


def _target_for(m):
    """Pick the right display field based on monitor type instead of
    blindly preferring 'url' (which Kuma pre-fills with 'https://' even
    for non-HTTP monitor types)."""
    mtype = str(m.get("type", "")).lower()

    if mtype in ("http", "keyword", "json-query"):
        url = m.get("url")
        return url if url and url != "https://" else "—"

    if mtype in ("port", "tcp"):
        host = m.get("hostname")
        port = m.get("port")
        if host:
            return f"{host}:{port}" if port else host
        return "—"

    if mtype in ("ping", "dns", "docker"):
        return m.get("hostname") or "—"

    # fallback: try hostname first, then url, skipping the useless default
    host = m.get("hostname")
    if host:
        return host
    url = m.get("url")
    if url and url != "https://":
        return url
    return "—"


def get_kuma_monitors():
    api = _connect()
    try:
        monitors = api.get_monitors()
        heartbeats = api.get_heartbeats()

        result = []
        for m in monitors:
            mid = m["id"]
            beats = heartbeats.get(mid)

            # Fallback: cache didn't have this monitor yet, ask directly
            if not beats:
                try:
                    beats = api.get_monitor_beats(mid, 1)
                except Exception:
                    beats = []

            last = beats[-1] if beats else None

            if last is None:
                status = "UNKNOWN"
                down_since = None
            elif last["status"] == 1:
                status = "UP"
                down_since = None
            else:
                status = "DOWN"
                down_since = last["time"]

            result.append({
                "id": mid,
                "name": m["name"],
                "target": _target_for(m),
                "status": status,
                "down_since": down_since,
                "active": m.get("active", True),
            })

        # Sort alphabetically by name (case-insensitive)
        result.sort(key=lambda x: x["name"].lower())
        return result
    finally:
        api.disconnect()


def get_monitor_log(monitor_id, hours=24):
    api = _connect()
    try:
        beats = api.get_monitor_beats(monitor_id, hours)
        return [
            {
                "time": b["time"],
                "status": "UP" if b["status"] == 1 else "DOWN",
                "msg": b.get("msg", ""),
                "ping": b.get("ping"),
            }
            for b in beats  # keep chronological order for the chart
        ]
    finally:
        api.disconnect()
