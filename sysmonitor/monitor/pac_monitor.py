"""
monitor/pac_monitor.py
────────────────────────
Independent background poller for the 3 SMW6PAC controllers. Runs
separately from ping_monitor.py (which owns Holder/NVR only) — this
script never touches the outage-detection heartbeat.

Every POLL_INTERVAL seconds, checks each PAC unit's run-state (ON /
STANDBY / OFF, derived from active alarms via pac_client.py) and
compares it to the last known state stored in PacRunState. If it
changed, dispatches a PAC_STATUS_CHANGE notification to whichever
recipients opted in (alert_pac_status=True) and updates the stored
state.
"""

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
import django
django.setup()

import pytz
from monitor.models import PacRunState
from monitor.pac_client import get_all_pac_status
from monitor.notifications import dispatch as notify_dispatch

BDT = pytz.timezone('Asia/Dhaka')
POLL_INTERVAL = 60  # seconds — matches the page's own auto-refresh cadence


def fmt(dt=None):
    dt = dt or datetime.now(BDT)
    return dt.strftime('%I:%M:%S %p')


def run():
    print(f'[{fmt()}] pac_monitor starting — polling every {POLL_INTERVAL}s')
    print('  Independent of ping_monitor.py — never touches Holder/NVR.')

    while True:
        units = get_all_pac_status()
        for idx, u in enumerate(units, start=1):
            if not u.get('ok'):
                continue  # unreachable this cycle — don't flap on transient misses

            ip = u['ip']
            new_label = u['run_state']['label']
            unit_name = f'Unit {idx}'

            stored, created = PacRunState.objects.get_or_create(
                ip=ip, defaults={'label': new_label}
            )

            if not created and stored.label != new_label:
                old_label = stored.label
                print(f'[{fmt()}] {unit_name} ({ip}): {old_label} -> {new_label}')
                stored.label = new_label
                stored.save()
                try:
                    notify_dispatch('PAC_STATUS_CHANGE', extra={
                        'unit': unit_name,
                        'old': old_label,
                        'new': new_label,
                    })
                except Exception as e:
                    print(f'[{fmt()}] Notify failed for {unit_name}: {e}')
            elif created:
                print(f'[{fmt()}] {unit_name} ({ip}): baseline state = {new_label}')

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt:
        print(f'\n[{fmt()}] pac_monitor stopped.')
