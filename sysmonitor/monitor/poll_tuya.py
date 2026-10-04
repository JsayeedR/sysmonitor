"""
monitor/poll_tuya.py
───────────────────────
Polls the colocation room's Tuya T&H sensor and saves a SensorReading row.
Run on a schedule (e.g. every 2 minutes) via its systemd timer.

Usage:
    python monitor/poll_tuya.py           # normal run — poll, save, exit
    python monitor/poll_tuya.py --debug   # also print the raw Tuya status
                                           # payload, useful for the first
                                           # run to confirm the status codes
                                           # this specific device reports
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
django.setup()

from datetime import datetime
import pytz

BDT = pytz.timezone('Asia/Dhaka')

from monitor.models import SensorReading, Event
from monitor.tuya_client import get_sensor_reading, tuya_configured, TUYA_DEVICE_ID

DEVICE_NAME = 'T & H Sensor (Colocation Room)'


def run(debug=False):
    now_str = datetime.now(BDT).strftime('%d/%m/%Y %I:%M:%S %p')

    if not tuya_configured():
        print(f'[{now_str}] Tuya not configured — set TUYA_ACCESS_ID, '
              f'TUYA_ACCESS_SECRET, TUYA_DEVICE_ID in .env. Skipping.')
        return

    reading = get_sensor_reading()

    if debug:
        print(f'[{now_str}] Raw Tuya response:')
        print(reading.get('raw'))

    if reading['error']:
        print(f'[{now_str}] Tuya poll FAILED: {reading["error"]}')
        # Only log a dashboard event if we've gone from working to
        # failing — avoid spamming the event log on a transient blip.
        last = SensorReading.objects.filter(device_id=TUYA_DEVICE_ID).first()
        if last and last.is_online:
            Event.objects.create(
                device=None, level='ALARM',
                message=f'Colocation sensor poll failed — {reading["error"]}'
            )
        SensorReading.objects.create(
            device_id=TUYA_DEVICE_ID, device_name=DEVICE_NAME,
            is_online=False, raw_status=reading.get('raw'),
        )
        return

    SensorReading.objects.create(
        device_id=TUYA_DEVICE_ID,
        device_name=DEVICE_NAME,
        temperature_c=reading['temperature_c'],
        humidity_pct=reading['humidity_pct'],
        battery_pct=reading['battery_pct'],
        battery_state=reading.get('battery_state', ''),
        is_online=reading['is_online'],
        raw_status=reading['raw'],
    )
    battery_display = reading.get('battery_state') or reading['battery_pct']
    print(f'[{now_str}] Sensor reading saved — '
          f'{reading["temperature_c"]}°C, {reading["humidity_pct"]}%, '
          f'battery {battery_display}')


if __name__ == '__main__':
    run(debug='--debug' in sys.argv)
