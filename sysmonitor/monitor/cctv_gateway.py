import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

SECRET_DIR = Path("/etc/sysmonitor-mediamtx")
CREDENTIAL_FILE = SECRET_DIR / "nvr_credentials.json"
CONFIG_FILE = SECRET_DIR / "mediamtx.yml"

MASTER_WEBRTC_HOST = "192.168.30.48"
MASTER_WEBRTC_PORT = 8889


def _clean_host(value):
    value = (value or "").strip()

    if not value:
        return ""

    if "://" in value:
        parsed = urlsplit(value)
        return parsed.hostname or ""

    value = value.split("/")[0]

    if ":" in value:
        value = value.split(":")[0]

    return value.strip()


def _load_credentials():
    try:
        data = json.loads(CREDENTIAL_FILE.read_text())
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _write_credentials(data):
    SECRET_DIR.mkdir(parents=True, exist_ok=True)

    tmp = CREDENTIAL_FILE.with_suffix(".tmp")

    tmp.write_text(
        json.dumps(
            data,
            indent=2,
            sort_keys=True,
        ) + "\n"
    )

    os.chmod(tmp, 0o600)
    os.replace(tmp, CREDENTIAL_FILE)
    os.chmod(CREDENTIAL_FILE, 0o600)


def credential_info(nvr_id):
    row = _load_credentials().get(str(nvr_id), {})

    return {
        "username": row.get("username", ""),
        "has_password": bool(row.get("password")),
        "ready": bool(
            row.get("username") and
            row.get("password")
        ),
    }


def save_credentials(
    nvr_id,
    username="",
    password="",
):
    data = _load_credentials()
    key = str(nvr_id)

    existing = data.get(key, {})

    username = (username or "").strip()
    password = password or ""

    # Blank values on EDIT mean "keep existing".
    if username:
        existing["username"] = username

    if password:
        existing["password"] = password

    if existing:
        data[key] = existing
        _write_credentials(data)

    return credential_info(nvr_id)


def delete_credentials(nvr_id):
    data = _load_credentials()

    if str(nvr_id) in data:
        del data[str(nvr_id)]
        _write_credentials(data)


def player_url(camera):
    return (
        f"http://{MASTER_WEBRTC_HOST}:{MASTER_WEBRTC_PORT}"
        f"/camera-{camera.id}/"
    )


def rebuild_gateway_config():
    """
    Generate MediaMTX configuration from enabled CCTV cameras.

    Credentials remain outside Django's database and outside Git.
    Recording/storage/transcoding are not enabled.
    """
    from monitor.models import CCTVCamera

    credentials = _load_credentials()

    lines = [
        "logLevel: info",
        "",
        "api: false",
        "metrics: false",
        "pprof: false",
        "playback: false",
        "",
        "rtsp: false",
        "rtmp: false",
        "hls: true",
        "hlsAddress: 127.0.0.1:8888",
        "hlsEncryption: false",
        "srt: false",
        "moq: false",
        "",
        "webrtc: true",
        "webrtcAddress: :8889",
        "webrtcEncryption: false",
        'webrtcAllowOrigins: ["*"]',
        'webrtcAdditionalHosts: ["192.168.30.48"]',
        "",
        "webrtcLocalUDPAddress: :8189",
        'webrtcLocalTCPAddress: ""',
        "",
        "pathDefaults:",
        "  record: false",
        "  rtspTransport: tcp",
        "  sourceOnDemand: true",
        "  sourceOnDemandStartTimeout: 10s",
        "  sourceOnDemandCloseAfter: 10s",
        "",
        "paths:",
    ]

    count = 0

    cameras = (
        CCTVCamera.objects
        .select_related("nvr")
        .filter(
            enabled=True,
            nvr__enabled=True,
        )
        .order_by(
            "display_order",
            "id",
        )
    )

    for camera in cameras:
        secret = credentials.get(
            str(camera.nvr_id),
            {}
        )

        username = secret.get(
            "username",
            ""
        )

        password = secret.get(
            "password",
            ""
        )

        if not username or not password:
            continue

        host = _clean_host(
            camera.nvr.local_host
        )

        if not host:
            continue

        encoded_user = quote(
            username,
            safe="",
        )

        encoded_password = quote(
            password,
            safe="",
        )

        source = (
            f"rtsp://{encoded_user}:{encoded_password}"
            f"@{host}:{camera.nvr.rtsp_port}"
            f"/cam/realmonitor"
            f"?channel={camera.channel}"
            f"&subtype={camera.stream_type}"
        )

        lines.extend([
            f"  camera-{camera.id}:",
            f"    source: {json.dumps(source)}",
            "",
        ])

        count += 1

    if count == 0:
        # MediaMTX allows an empty paths map. Do not create a publisher
        # placeholder here because sourceOnDemand only applies to pulled
        # sources such as our Dahua RTSP streams.
        lines[-1] = "paths: {}"

    SECRET_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = CONFIG_FILE.with_suffix(".tmp")

    tmp.write_text(
        "\n".join(lines).rstrip() + "\n"
    )

    os.chmod(tmp, 0o600)

    os.replace(
        tmp,
        CONFIG_FILE,
    )

    os.chmod(
        CONFIG_FILE,
        0o600,
    )

    return count
