"""
monitor/monthly_report_sender.py
──────────────────────────────────
Run via cron at 00:05 BDT on the 1st of each month (5 minutes after the
existing 00:01 daily-summary/backup cron jobs, so it doesn't race them):

    5 0 1 * * cd /home/nanolab/Desktop/sysmonitor && venv/bin/python monitor/monthly_report_sender.py >> logs/monthly_report.log 2>&1

Sends the report for the month that just ended to every active recipient
with monthly_report=True (email gets the Excel workbook attached; WhatsApp/
Telegram get a condensed text summary).
"""
import os
import sys
import django
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
django.setup()

import pytz
BDT = pytz.timezone('Asia/Dhaka')

from monitor.models import Event
from monitor.monthly_report import send_monthly_report, fmt_duration


def run():
    now_bdt  = datetime.now(BDT)
    date_str = now_bdt.strftime('%d/%m/%Y %I:%M:%S %p')

    try:
        summary, sent_count, failed = send_monthly_report()

        print(f'[{date_str}] Monthly report built for {summary["label"]} — '
              f'{summary["outage_count"]} event(s), '
              f'{fmt_duration(summary["grand_total"])} total downtime')
        print(f'[{date_str}] Sent to {sent_count} recipient(s).')
        if failed:
            print(f'[{date_str}] Failed for: {"; ".join(failed)}')

        level = 'INFO' if not failed else 'ALARM'
        note  = '' if not failed else f' | {len(failed)} recipient(s) failed: {"; ".join(failed)}'
        Event.objects.create(
            device=None,
            level=level,
            message=f'Monthly loadshedding report sent for {summary["label"]} '
                    f'to {sent_count} recipient(s) '
                    f'({fmt_duration(summary["grand_total"])} total downtime){note}'
        )

    except Exception as e:
        print(f'[{date_str}] Monthly report FAILED: {e}')
        Event.objects.create(
            device=None,
            level='CRITICAL',
            message=f'Monthly loadshedding report FAILED — {str(e)}'
        )
        sys.exit(1)


if __name__ == '__main__':
    run()
