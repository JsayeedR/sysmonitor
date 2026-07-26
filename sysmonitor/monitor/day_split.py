"""
monitor/day_split.py
─────────────────────
Shared helper for splitting an OutageCycle's duration across calendar-day
boundaries (in a given timezone, e.g. Asia/Dhaka).

Why this exists:
An outage that starts at 11:50 PM and ends at 12:20 AM the next day is one
continuous real-world event, but for reporting purposes ("how much outage
time happened on 11th July?") it needs to be counted as TWO pieces:
  - 11:50 PM -> 12:00 AM  (10 min, belongs to the 11th)
  - 12:00 AM -> 12:20 AM  (20 min, belongs to the 12th)

This module does NOT change the OutageCycle row itself — the real event
stays as one row with one true start/end, which is correct for generator
runtime tracking, notifications, etc. This is purely a reporting-layer cut.

Used by:
  - monitor/daily_summary.py      (00:01 cron "Generator Log" email)
  - monitor/views.py:api_daily_summary  (dashboard widget)
  - monitor/views.py:api_report         (report page)
"""

from datetime import datetime, time as dtime, timedelta


def split_cycle_by_day(cycle, tz, now=None):
    """
    Cuts a cycle's duration at every midnight it crosses (in `tz`).

    Returns a list of segment dicts, one per calendar day touched:
        {
            'date':          date object,
            'start':         tz-aware datetime (segment start),
            'end':           tz-aware datetime (segment end),
            'duration_sec':  int,
            'is_ongoing':    bool  # True only on the LAST segment, and only
                                    # if the cycle hasn't actually completed
                                    # yet (we're reporting "so far").
        }

    For an outage that doesn't cross midnight, this returns a single
    segment — behaviour is unchanged from before for the common case.

    `now` lets a caller pin down "the current moment" (mainly for tests /
    reproducibility); if omitted, django.utils.timezone.now() is used.
    """
    if not cycle.outage_start:
        return []

    if now is None:
        from django.utils import timezone as dj_timezone
        now = dj_timezone.now()

    # Figure out the effective end of this cycle for reporting purposes.
    if cycle.pdb_restored:
        end = cycle.pdb_restored
    elif cycle.cycle_end:
        end = cycle.cycle_end
    elif not cycle.is_complete:
        # Still actively ongoing — report duration "so far", up to now.
        end = now
    else:
        # Marked complete but has neither timestamp — nothing sane to report.
        return []

    if end <= cycle.outage_start:
        return []

    start_local = cycle.outage_start.astimezone(tz)
    end_local = end.astimezone(tz)

    segments = []
    cursor = start_local
    while cursor.date() < end_local.date():
        next_midnight = tz.localize(
            datetime.combine(cursor.date() + timedelta(days=1), dtime.min)
        )
        segments.append({
            'date': cursor.date(),
            'start': cursor,
            'end': next_midnight,
            'duration_sec': round((next_midnight - cursor).total_seconds()),
            'is_ongoing': False,
        })
        cursor = next_midnight

    segments.append({
        'date': cursor.date(),
        'start': cursor,
        'end': end_local,
        'duration_sec': round((end_local - cursor).total_seconds()),
        'is_ongoing': not cycle.is_complete,
    })

    return segments


def get_day_segment(cycle, target_date, tz, now=None):
    """Convenience: return just the segment matching target_date, or None."""
    for seg in split_cycle_by_day(cycle, tz, now=now):
        if seg['date'] == target_date:
            return seg
    return None
