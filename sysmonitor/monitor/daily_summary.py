"""
monitor/daily_summary.py
─────────────────────────
Builds the daily Generator Log summary report by matching each completed
OutageCycle to the generator that was in "auto mode" during that cycle's
time window, based on manually logged GeneratorModeLog entries.

Matching rule: for each outage cycle's outage_start time, find the most
recent GeneratorModeLog entry with switched_at <= outage_start. That's
the generator that was active. If no log entry exists before the outage
at all (very first entry in history), it's marked UNASSIGNED.
"""

from datetime import datetime, timedelta
import pytz

BDT = pytz.timezone('Asia/Dhaka')


def fmt_time(dt):
    if not dt:
        return '—'
    return dt.astimezone(BDT).strftime('%I:%M:%S %p')


def fmt_duration(total_secs):
    """Exact duration string, e.g. '1h 02m 03s', '8m 15s', '45s'. No rounding."""
    total_secs = int(total_secs)
    h, r = divmod(total_secs, 3600)
    m, s = divmod(r, 60)
    if h:
        return f'{h}h {m:02d}m {s:02d}s'
    if m:
        return f'{m}m {s:02d}s'
    return f'{s}s'


def get_generator_for_cycle(cycle, mode_logs_sorted):
    """
    mode_logs_sorted: list of GeneratorModeLog objects sorted by switched_at ASC.
    Returns the generator name active at the time this cycle's outage started,
    using "last known generator before the gap" as the default rule.

    Manual/audited cycles (cycle.is_manual=True) carry their own explicit
    manual_generator value set by whoever entered them — that always wins
    over the automatic GeneratorModeLog inference, since a human directly
    stated which generator was in use.
    """
    if cycle.is_manual and cycle.manual_generator:
        return cycle.manual_generator

    if not cycle.outage_start:
        return 'UNASSIGNED'

    active_gen = None
    for log in mode_logs_sorted:
        if log.switched_at <= cycle.outage_start:
            active_gen = log.generator
        else:
            break  # logs are sorted ascending, no need to check further

    return active_gen or 'UNASSIGNED'


def build_daily_summary(target_date):
    """
    target_date: a date object (e.g. yesterday's date in BDT)
    Returns a dict: {
        'date': 'DD/MM/YYYY',
        'rows': [{'start', 'end', 'duration_min', 'generator', 'is_ongoing'}, ...],
        'totals': {'Gen-01': mins, 'Gen-02': mins, 'UNASSIGNED': mins},
        'grand_total': mins,
        'has_data': bool,
    }

    Midnight-crossing outages: a cycle that started before midnight and is
    still running (or ended after midnight) gets CLIPPED to only the portion
    that actually happened on target_date. The rest belongs to the next
    day's summary and is picked up automatically when that day runs.

    This also fixes a data-loss bug: previously only is_complete=True cycles
    were counted, so an outage still in progress exactly at the 00:01 cron
    run (e.g. started 11:50 PM, restored 12:20 AM) was skipped entirely for
    BOTH days. Now its pre-midnight portion is captured on target_date even
    if the cycle hasn't finished yet.
    """
    from django.utils import timezone as dj_timezone
    from monitor.models import OutageCycle, GeneratorModeLog
    from monitor.day_split import split_cycle_by_day

    day_start_bdt = BDT.localize(datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0))
    day_end_bdt   = day_start_bdt + timedelta(days=1)
    now = dj_timezone.now()

    # Candidates: anything that could have ANY overlap with target_date.
    # Look back a few days so a still-ongoing cycle that started earlier
    # (or a cycle that finished late) isn't missed by outage_start alone.
    candidates = OutageCycle.objects.filter(
        outage_start__lt=day_end_bdt,
        outage_start__gte=day_start_bdt - timedelta(days=3),
    ).order_by('outage_start')

    # Pull ALL mode logs up to end of target day (need history before today too,
    # in case the last switch happened on a previous day and is still active)
    mode_logs = list(GeneratorModeLog.objects.filter(
        switched_at__lt=day_end_bdt
    ).order_by('switched_at'))

    rows = []
    totals = {'Gen-01': 0, 'Gen-02': 0, 'UNASSIGNED': 0}

    for c in candidates:
        for seg in split_cycle_by_day(c, BDT, now=now):
            if seg['date'] != target_date:
                continue
            secs = seg['duration_sec']
            if secs <= 0:
                continue

            gen = get_generator_for_cycle(c, mode_logs)
            rows.append({
                'start': seg['start'].strftime('%I:%M:%S %p'),
                'end': 'ongoing…' if seg['is_ongoing'] else seg['end'].strftime('%I:%M:%S %p'),
                'duration_sec': secs,
                'generator': gen,
                'is_ongoing': seg['is_ongoing'],
            })
            totals[gen] = totals.get(gen, 0) + secs

    grand_total = sum(totals.values())

    return {
        'date': target_date.strftime('%d/%m/%Y'),
        'rows': rows,
        'totals': totals,
        'grand_total': grand_total,
        'has_data': len(rows) > 0,
    }


def format_summary_text(summary):
    """Formats the summary dict into the exact text-report style requested."""
    lines = []
    width = 55
    lines.append('-' * width)
    lines.append(f"Generator Log: {summary['date']}")
    lines.append('-' * width)

    if not summary['has_data']:
        lines.append('No outages recorded — all systems normal.')
        lines.append('-' * width)
        return '\n'.join(lines)

    lines.append(f"{'Start Time':<11} | {'End Time':<11} | {'Duration':<10} | GEN")
    lines.append('-' * width)
    has_ongoing = False
    for r in summary['rows']:
        marker = ' *' if r.get('is_ongoing') else ''
        dur_str = fmt_duration(r['duration_sec'])
        lines.append(f"{r['start']:<11} | {r['end']:<11} | {dur_str:<10} | {r['generator']}{marker}")
        if r.get('is_ongoing'):
            has_ongoing = True
    lines.append('-' * width)
    lines.append('Total Duration:')
    for gen in ('Gen-01', 'Gen-02'):
        lines.append(f"{gen}: {fmt_duration(summary['totals'].get(gen, 0))}")
    if summary['totals'].get('UNASSIGNED', 0) > 0:
        lines.append(f"Unassigned: {fmt_duration(summary['totals']['UNASSIGNED'])} "
                      f"(no generator mode log found for this period)")
    lines.append(f"Grand Total: {fmt_duration(summary['grand_total'])}")
    if has_ongoing:
        lines.append('-' * width)
        lines.append('* Outage was still ongoing at report time — duration is')
        lines.append("  partial (up to midnight). Remainder appears in tomorrow's report.")
    lines.append('-' * width)

    return '\n'.join(lines)
