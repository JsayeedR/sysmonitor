"""
Real-time-ish offsite sync — pushes just the live db.sqlite3 to the remote
backup server every few minutes (see systemd/sysmonitor-realtime-sync.timer).

This is deliberately separate from backup.py's once-daily full backup:
- backup.py (00:01 BDT daily): full DB snapshot + project code tarball —
  the "large data" sync.
- realtime_sync.py (every 5 min via its timer): just the current
  db.sqlite3, so the offsite copy is never more than a few minutes stale —
  the "real-time type data sync".

Requires the same REMOTE_BACKUP_* environment variables as backup.py. If
they aren't set, this exits quietly (no-op) rather than failing the timer.
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
django.setup()

from monitor.backup import (
    DB_PATH, remote_sync_enabled, rsync_to_remote, BDT,
)
from monitor.models import Event
from datetime import datetime


def run():
    now_bdt = datetime.now(BDT)
    date_str = now_bdt.strftime('%d/%m/%Y %I:%M %p')

    if not remote_sync_enabled():
        # Nothing configured yet — exit quietly, this timer just no-ops.
        return

    if not os.path.exists(DB_PATH):
        print(f'[{date_str}] realtime_sync: db.sqlite3 not found, skipping')
        return

    ok, msg = rsync_to_remote(DB_PATH, date_str, 'live database (realtime)')
    if not ok:
        # Only log a dashboard event on failure — a successful sync every
        # 5 minutes would otherwise spam the event log.
        Event.objects.create(
            device=None,
            level='ALARM',
            message=f'Realtime offsite DB sync FAILED — {msg}'
        )


if __name__ == '__main__':
    run()
