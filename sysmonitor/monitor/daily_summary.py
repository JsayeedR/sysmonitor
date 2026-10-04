"""
monitor/daily_summary.py
─────────────────────────
Builds the daily Generator Log summary report by matching each completed
OutageCycle to the generator(s) that were in "auto mode" during that
cycle's time window, based on manually logged GeneratorModeLog entries.

Matching rule: for each portion of a cycle, find the most recent
GeneratorModeLog entry with switched_at <= that portion's start. If a
generator switch happens WHILE the cycle is still ongoing (e.g. Gen-01
hands off to Gen-02 mid-outage), the cycle's duration is split at that
switch point so each generator only gets credited for the time it was
actually running (see get_generator_segments()). If no log entry exists
before the outage at all, it's marked UNASSIGNED.
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
    Returns the generator active at the moment this cycle's outage started.
    Kept for anywhere that only needs a single best-guess label (e.g. a
    quick UI hint) — for anything that needs to be accurate when a
    generator changeover happened DURING the outage, use
    get_generator_segments() instead, which splits the window properly.

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


def get_generator_segments(cycle, seg_start, seg_end, mode_logs_sorted):
    """
    Splits the time window [seg_start, seg_end) into one or more
    (start, end, generator) pieces, cut at every GeneratorModeLog switch
    point that falls strictly inside the window.

    This is what correctly handles an outage that spans a generator
    changeover mid-cycle (e.g. Gen-01 -> Gen-02 while Holder is still
    down) — instead of the whole span being stamped with whichever
    generator happened to be active at the very start (which is what
    get_generator_for_cycle() alone would do, and is wrong for exactly
    this case — see the 24/09/2026 09:09-12:02 outage, which needed a
    manual split for this reason before this function existed).

    Manual/audited cycles (cycle.is_manual=True) are never split — the
    human who logged it already stated the one generator responsible for
    that whole entry, and that always wins.
    """
    if cycle.is_manual and cycle.manual_generator:
        return [(seg_start, seg_end, cycle.manual_generator)]

    if seg_end <= seg_start:
        return []

    # Generator active at the moment this window begins.
    current_gen = 'UNASSIGNED'
    for log in mode_logs_sorted:
        if log.switched_at <= seg_start:
            current_gen = log.generator
        else:
            break

    # Switch points strictly inside the window, already ascending since
    # mode_logs_sorted is sorted by switched_at.
    switches_inside = [log for log in mode_logs_sorted
                        if seg_start < log.switched_at < seg_end]

    pieces = []
    cursor = seg_start
    for sw in switches_inside:
        pieces.append((cursor, sw.switched_at, current_gen))
        cursor = sw.switched_at
        current_gen = sw.generator
    pieces.append((cursor, seg_end, current_gen))
    return pieces


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

            pieces = get_generator_segments(c, seg['start'], seg['end'], mode_logs)
            for i, (p_start, p_end, gen) in enumerate(pieces):
                secs = int((p_end - p_start).total_seconds())
                if secs <= 0:
                    continue
                is_ongoing_piece = seg['is_ongoing'] and (i == len(pieces) - 1)

                rows.append({
                    'start': p_start.astimezone(BDT).strftime('%I:%M:%S %p'),
                    'end': 'ongoing…' if is_ongoing_piece else p_end.astimezone(BDT).strftime('%I:%M:%S %p'),
                    'duration_sec': secs,
                    'generator': gen,
                    'is_ongoing': is_ongoing_piece,
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
