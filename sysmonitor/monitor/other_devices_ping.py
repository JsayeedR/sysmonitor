"""
monitor/other_devices_ping.py
──────────────────────────────
Lightweight, independent status checker for every active Device EXCEPT
Holder and NVR. Those two are the critical outage-detection heartbeat
and stay fully owned by ping_monitor.py — this script never touches
them and has no outage-cycle logic at all.

Purpose: simply keep DeviceStatus up to date for devices shown on the
dashboard (Mikrotik router, NOC router, Tenda routers, etc.) so they
don't sit stuck on "UNKNOWN" forever. Runs as its own systemd service,
independently restartable without any risk to the main PDB/NVR monitor.
"""

import os
import sys
import subprocess
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
import django
django.setup()

import pytz
from monitor.models import Device, DeviceStatus

BDT = pytz.timezone('Asia/Dhaka')
PING_INTERVAL = 60  # seconds — deliberately slower than the main Holder/NVR monitor since these aren't safety-critical, and to keep resource usage well separated from the critical heartbeat


def fmt(dt=None):
    dt = dt or datetime.now(BDT)
    return dt.strftime('%I:%M:%S %p')


def ping(ip):
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '2', ip],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'time=' in line:
                    ms = float(line.split('time=')[1].split(' ')[0])
                    return True, int(ms)
            return True, None
        return False, None
    except Exception:
        return False, None


def get_other_devices():
    """Every active device except Holder/NVR — those belong to
    ping_monitor.py's outage-detection heartbeat, not here."""
    return Device.objects.filter(is_active=True) \
        .exclude(name__icontains='holder') \
        .exclude(name__icontains='nvr')


def save_status(device, is_up, ms):
    DeviceStatus.objects.create(
        device=device,
        status='UP' if is_up else 'DOWN',
        response_ms=ms
    )
    # Keep only latest 1000 records per device, same as ping_monitor.py
    ids_to_keep = DeviceStatus.objects.filter(
        device=device
    ).order_by('-checked_at').values_list('id', flat=True)[:1000]
    DeviceStatus.objects.filter(
        device=device
    ).exclude(id__in=list(ids_to_keep)).delete()


def run():
    print(f'[{fmt()}] other_devices_ping starting — sweep every {PING_INTERVAL}s')
    print('  This script only handles devices OTHER than Holder/NVR.')
    print('  Holder/NVR outage detection is handled separately by ping_monitor.py.')

    while True:
        devices = list(get_other_devices())
        for device in devices:
            is_up, ms = ping(device.ip_address)
            save_status(device, is_up, ms)
            status_str = f'UP ({ms}ms)' if is_up and ms else ('UP' if is_up else 'DOWN')
            print(f'[{fmt()}] {device.name:35} {device.ip_address:16} {status_str}')
        time.sleep(PING_INTERVAL)


if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt:
        print(f'\n[{fmt()}] other_devices_ping stopped.')
