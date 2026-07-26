import os
import sys
import django
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
django.setup()

import pytz
BDT = pytz.timezone('Asia/Dhaka')

from monitor.models import Event
from monitor.daily_summary import build_daily_summary, format_summary_text, fmt_duration
from monitor.notifications import dispatch as notify_dispatch


def run():
    now_bdt   = datetime.now(BDT)
    date_str  = now_bdt.strftime('%d/%m/%Y %I:%M:%S %p')

    # Summarize YESTERDAY (since this runs at 00:01, "today's outages so far"
    # would be empty — we want the full day that just ended)
    target_date = (now_bdt - timedelta(days=1)).date()

    try:
        summary = build_daily_summary(target_date)
        report_text = format_summary_text(summary)

        print(f'[{date_str}] Daily summary built for {summary["date"]} '
              f'— {len(summary["rows"])} cycle(s), {fmt_duration(summary["grand_total"])} total')

        notify_dispatch('DAILY_SUMMARY', cycle=None, extra=report_text)

        print(f'[{date_str}] Daily summary dispatched.')

        Event.objects.create(
            device=None,
            level='INFO',
            message=f'Daily summary sent for {summary["date"]} '
                    f'({fmt_duration(summary["grand_total"])} total outage)'
        )

    except Exception as e:
        print(f'[{date_str}] Daily summary FAILED: {e}')
        Event.objects.create(
            device=None,
            level='CRITICAL',
            message=f'Daily summary FAILED — {str(e)}'
        )
        sys.exit(1)


if __name__ == '__main__':
    run()
