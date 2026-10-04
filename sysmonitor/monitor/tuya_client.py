"""
monitor/tuya_client.py
────────────────────────
Talks to the Tuya Cloud API to read the colocation room's temperature/
humidity sensor ("T & H Sensor (Colocation Room)", linked via the
SysMonitor cloud project on Tuya's Central Europe Data Center).

Credentials come from the environment (.env) — never hardcoded:
    TUYA_ACCESS_ID
    TUYA_ACCESS_SECRET
    TUYA_DEVICE_ID
    TUYA_API_BASE       (defaults to the Central Europe endpoint)

The HMAC-SHA256 request-signing implementation below was verified against
Tuya's own two published worked examples (token-request signing and
general-business-API signing) before ever being pointed at a real
device — both reproduced Tuya's documented signatures byte-for-byte.

IMPORTANT — why this uses the "shadow properties" endpoint:
This specific device (product category 'tdq', model 001TH02T1-3S) does
NOT support Tuya's "Standard Instruction Set" — confirmed via its own
Device Debugging page ("No data found") and the specifications API
returning "not support this device" (code 2009). Because of that, the
usual /v1.0/iot-03/devices/{id}/status endpoint comes back as an empty
list for this device, even though it's online and really reporting data
(visible in the IoT Platform's own Device Logs tab). The endpoint that
DOES work for this device is the device-properties "shadow" API:
    GET /v2.0/cloud/thing/{device_id}/shadow/properties
confirmed against the live sensor with real values:
    temp_current=227 (-> 22.7°C, needs /10), humidity_value=35 (already
    a plain %, no scaling), battery_state='middle' (an enum, not a %).

Usage:
    from monitor.tuya_client import get_sensor_reading
    reading = get_sensor_reading()
    # {'temperature_c': 22.7, 'humidity_pct': 35.0, 'battery_pct': None,
    #  'battery_state': 'middle', 'is_online': True, 'raw': [...], 'error': None}
"""
import hashlib
import hmac
import os
import time
import uuid

import requests
from django.core.cache import cache

TUYA_ACCESS_ID     = os.environ.get('TUYA_ACCESS_ID', '').strip()
TUYA_ACCESS_SECRET = os.environ.get('TUYA_ACCESS_SECRET', '').strip()
TUYA_DEVICE_ID     = os.environ.get('TUYA_DEVICE_ID', '').strip()
TUYA_API_BASE      = os.environ.get('TUYA_API_BASE', 'https://openapi.tuyaeu.com').strip()

_TOKEN_CACHE_KEY = 'tuya_access_token'


def tuya_configured():
    return bool(TUYA_ACCESS_ID and TUYA_ACCESS_SECRET and TUYA_DEVICE_ID)


# ── Signing (verified against Tuya's own documented test vectors) ──────────

def _content_sha256(body_bytes: bytes) -> str:
    return hashlib.sha256(body_bytes).hexdigest()


def _build_url(path: str, params: dict) -> str:
    if not params:
        return path
    parts = [f"{k}={v}" for k, v in sorted(params.items())]
    return path + "?" + "&".join(parts)


def _string_to_sign(method: str, content_sha256_hex: str, url: str) -> str:
    # No custom Signature-Headers are used here, so that section is empty —
    # which is why there's a blank line between it and the URL (see Tuya's
    # docs FAQ: "Why does a blank line exist in stringToSign?").
    return f"{method}\n{content_sha256_hex}\n\n{url}"


def _sign(t: str, nonce: str, string_to_sign: str, access_token: str = "") -> str:
    prefix = TUYA_ACCESS_ID + access_token + t + nonce
    full_str = prefix + string_to_sign
    digest = hmac.new(TUYA_ACCESS_SECRET.encode('utf-8'),
                       full_str.encode('utf-8'), hashlib.sha256).hexdigest()
    return digest.upper()


def _request(method, path, params=None, access_token=""):
    params = params or {}
    url_for_sign = _build_url(path, params)
    body_sha = _content_sha256(b"")
    sts = _string_to_sign(method, body_sha, url_for_sign)

    t = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex
    signature = _sign(t, nonce, sts, access_token=access_token)

    headers = {
        'client_id': TUYA_ACCESS_ID,
        'sign': signature,
        'sign_method': 'HMAC-SHA256',
        't': t,
        'nonce': nonce,
    }
    if access_token:
        headers['access_token'] = access_token

    resp = requests.request(method, TUYA_API_BASE + url_for_sign,
                             headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ── Token management ────────────────────────────────────────────────────────

def get_access_token():
    """Returns a cached token, fetching a fresh one if expired. Tuya tokens
    are valid ~2 hours; cached for 100 minutes to be safe."""
    cached = cache.get(_TOKEN_CACHE_KEY)
    if cached:
        return cached

    data = _request('GET', '/v1.0/token', params={'grant_type': '1'})
    if not data.get('success'):
        raise RuntimeError(f"Tuya token request failed: {data.get('msg')} "
                            f"(code {data.get('code')})")
    token = data['result']['access_token']
    cache.set(_TOKEN_CACHE_KEY, token, 60 * 100)
    return token


# ── Device status ────────────────────────────────────────────────────────────

# Status-code aliases seen across different Tuya T&H sensor product
# categories/firmwares. Codes that report temperature/humidity as an
# integer x10 (e.g. 227 = 22.7°C) are scaled down; codes that already
# report a plain percentage are used as-is. battery_percentage/battery
# are plain 0-100 integers; battery_state is an enum ('low'/'middle'/
# 'high') used by devices (like this one) that don't report a numeric %.
_TEMP_CODES_SCALED      = ('va_temperature', 'temp_current')
_HUMIDITY_CODES_SCALED  = ('va_humidity',)
_HUMIDITY_CODES_PLAIN   = ('humidity_value',)
_BATTERY_CODES_PCT      = ('battery_percentage', 'battery')
_BATTERY_CODES_ENUM     = ('battery_state',)


def _parse_status(status_list):
    """status_list: [{'code': ..., 'value': ...}, ...] — works for both the
    iot-03 /status response and the /shadow/properties response, since
    both use 'code'/'value' keys per entry."""
    by_code = {item['code']: item['value'] for item in status_list}

    temperature_c = None
    for code in _TEMP_CODES_SCALED:
        if code in by_code:
            temperature_c = round(by_code[code] / 10.0, 1)
            break

    humidity_pct = None
    for code in _HUMIDITY_CODES_SCALED:
        if code in by_code:
            humidity_pct = round(by_code[code] / 10.0, 1)
            break
    if humidity_pct is None:
        for code in _HUMIDITY_CODES_PLAIN:
            if code in by_code:
                humidity_pct = round(float(by_code[code]), 1)
                break

    battery_pct = None
    for code in _BATTERY_CODES_PCT:
        if code in by_code:
            battery_pct = by_code[code]
            break

    battery_state = ''
    for code in _BATTERY_CODES_ENUM:
        if code in by_code:
            battery_state = str(by_code[code])
            break

    return temperature_c, humidity_pct, battery_pct, battery_state


def get_sensor_reading():
    """
    Returns {'temperature_c', 'humidity_pct', 'battery_pct', 'battery_state',
    'is_online', 'raw', 'error'}. Never raises — errors come back in 'error'
    so a polling script or view can log/display them without crashing.

    Tries the shadow/properties endpoint first (confirmed working for this
    device, which doesn't support the Standard Instruction Set), then falls
    back to the iot-03 status endpoint in case a future/different device
    linked here does support standardized status.
    """
    result = {
        'temperature_c': None, 'humidity_pct': None, 'battery_pct': None,
        'battery_state': '', 'is_online': False, 'raw': None, 'error': None,
    }
    if not tuya_configured():
        result['error'] = 'Tuya not configured (TUYA_ACCESS_ID/SECRET/DEVICE_ID missing in .env)'
        return result

    try:
        token = get_access_token()

        data = _request('GET', f'/v2.0/cloud/thing/{TUYA_DEVICE_ID}/shadow/properties',
                         access_token=token)
        status_list = None
        if data.get('success') and data.get('result', {}).get('properties'):
            status_list = data['result']['properties']
        else:
            # Fall back to the standardized status endpoint.
            data2 = _request('GET', f'/v1.0/iot-03/devices/{TUYA_DEVICE_ID}/status',
                              access_token=token)
            if data2.get('success') and data2.get('result'):
                status_list = data2['result']
            else:
                result['error'] = (
                    f"Tuya status request failed: {data.get('msg')} (code {data.get('code')}); "
                    f"fallback also failed: {data2.get('msg')} (code {data2.get('code')})")
                return result

        result['raw'] = status_list
        temperature_c, humidity_pct, battery_pct, battery_state = _parse_status(status_list)
        result['temperature_c'] = temperature_c
        result['humidity_pct']  = humidity_pct
        result['battery_pct']   = battery_pct
        result['battery_state'] = battery_state
        result['is_online'] = True

        if temperature_c is None and humidity_pct is None:
            result['error'] = (
                "Connected OK, but none of the known status codes matched. "
                "Raw status is in result['raw'] — send that back so the "
                "code list can be extended for this device.")
    except requests.exceptions.RequestException as e:
        result['error'] = f'Network error reaching Tuya: {e}'
    except Exception as e:
        result['error'] = f'Unexpected error: {e}'

    return result
