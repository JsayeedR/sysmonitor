from datetime import datetime, time, timedelta

import pytz
from django.db.models import Avg, Max, Min, Q

from monitor.models import (
    GeneratorModeLog,
    OutageCycle,
    SensorReading,
)


BDT = pytz.timezone('Asia/Dhaka')


SHIFT_TIMES = {
    'MORNING': (time(9, 0), time(17, 0)),
    'EVENING': (time(17, 0), time(23, 0)),
    'NIGHT': (time(23, 0), time(9, 0)),
}


def localize(naive_dt):
    return BDT.localize(naive_dt)


def shift_window(report_date, shift):
    """
    Morning : report_date 09:00 -> 17:00
    Evening : report_date 17:00 -> 23:00
    Night   : report_date 23:00 -> next day 09:00
    """
    if shift not in SHIFT_TIMES:
        raise ValueError(f'Unknown shift: {shift}')

    start_time, end_time = SHIFT_TIMES[shift]

    start = localize(
        datetime.combine(report_date, start_time)
    )

    end_date = report_date

    if shift == 'NIGHT':
        end_date = report_date + timedelta(days=1)

    end = localize(
        datetime.combine(end_date, end_time)
    )

    return start, end


def current_shift(now=None):
    """
    Return:
        operational_date, shift_code, start, end

    00:00-08:59 belongs to the NIGHT shift that started the previous day.
    """
    now = now or datetime.now(BDT)

    if now.tzinfo is None:
        now = BDT.localize(now)
    else:
        now = now.astimezone(BDT)

    today = now.date()
    current_time = now.time().replace(tzinfo=None)

    if time(9, 0) <= current_time < time(17, 0):
        report_date = today
        shift = 'MORNING'

    elif time(17, 0) <= current_time < time(23, 0):
        report_date = today
        shift = 'EVENING'

    else:
        shift = 'NIGHT'

        if current_time < time(9, 0):
            report_date = today - timedelta(days=1)
        else:
            report_date = today

    start, end = shift_window(report_date, shift)

    return report_date, shift, start, end


def parse_email_list(value):
    """
    Normalize newline/comma/semicolon-separated email addresses.
    Preserve order and remove duplicates/blanks.
    """
    if not value:
        return []

    raw = (
        value
        .replace(';', ',')
        .replace('\n', ',')
        .split(',')
    )

    result = []
    seen = set()

    for part in raw:
        address = part.strip()

        if not address:
            continue

        key = address.casefold()

        if key in seen:
            continue

        seen.add(key)
        result.append(address)

    return result


def fmt_duration(seconds):
    seconds = int(seconds or 0)

    if seconds <= 0:
        return '0m'

    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f'{hours}h {minutes:02d}m'

    if minutes:
        return f'{minutes}m {secs:02d}s'

    return f'{secs}s'


def cycle_generator(cycle):
    """
    Match the existing SysMonitor generator assignment rule.

    Manual assignment wins. Otherwise use the most recent GeneratorModeLog
    at or before the outage start.
    """
    if cycle.manual_generator:
        return cycle.manual_generator

    if not cycle.outage_start:
        return None

    entry = (
        GeneratorModeLog.objects
        .filter(switched_at__lte=cycle.outage_start)
        .order_by('-switched_at', '-id')
        .first()
    )

    return entry.generator if entry else None


def _cycle_end_for_overlap(cycle, window_end):
    """
    Choose the best known outage end time.

    PDB restoration is preferred because shift downtime is PDB outage time.
    For an unfinished current outage, clip at the requested window end.
    """
    return (
        cycle.pdb_restored
        or cycle.cycle_end
        or window_end
    )


def _overlap_seconds(start_a, end_a, start_b, end_b):
    start = max(start_a, start_b)
    end = min(end_a, end_b)

    if end <= start:
        return 0

    return int((end - start).total_seconds())


def build_outage_summary(start, end):
    """
    Summarize PDB outages overlapping the requested shift window.

    Runtime is clipped to the exact shift boundary so an outage spanning
    two shifts is not fully charged to both.
    """
    cycles = (
        OutageCycle.objects
        .filter(outage_start__isnull=False)
        .filter(outage_start__lt=end)
        .filter(
            Q(pdb_restored__gt=start)
            | Q(cycle_end__gt=start)
            | Q(pdb_restored__isnull=True, cycle_end__isnull=True)
        )
        .order_by('outage_start', 'id')
    )

    rows = []
    total_seconds = 0
    generator_totals = {
        'Gen-01': 0,
        'Gen-02': 0,
        'Unknown': 0,
    }

    for cycle in cycles:
        cycle_end = _cycle_end_for_overlap(cycle, end)

        overlap = _overlap_seconds(
            cycle.outage_start,
            cycle_end,
            start,
            end,
        )

        if overlap <= 0:
            continue

        generator = cycle_generator(cycle) or 'Unknown'

        if generator not in generator_totals:
            generator_totals[generator] = 0

        generator_totals[generator] += overlap
        total_seconds += overlap

        rows.append({
            'id': cycle.id,
            'start': max(cycle.outage_start, start).astimezone(BDT).strftime('%d-%m %I:%M %p'),
            'end': min(cycle_end, end).astimezone(BDT).strftime('%d-%m %I:%M %p'),
            'duration_seconds': overlap,
            'duration': fmt_duration(overlap),
            'cycle_type': cycle.cycle_type,
            'generator': generator,
            'manual': bool(cycle.is_manual),
            'alarm_reason': cycle.alarm_reason or '',
        })

    return {
        'count': len(rows),
        'total_seconds': total_seconds,
        'total_duration': fmt_duration(total_seconds),
        'generator_seconds': generator_totals,
        'generator_duration': {
            key: fmt_duration(value)
            for key, value in generator_totals.items()
        },
        'rows': rows,
    }


def build_generator_summary(start, end, outage_summary=None):
    outage_summary = outage_summary or build_outage_summary(start, end)

    shifts = (
        GeneratorModeLog.objects
        .filter(switched_at__gte=start, switched_at__lt=end)
        .order_by('switched_at', 'id')
    )

    shift_rows = []

    for entry in shifts:
        shift_rows.append({
            'id': entry.id,
            'generator': entry.generator,
            'switched_at': entry.switched_at.astimezone(BDT).isoformat(),
            'note': entry.note or '',
            'added_by': entry.added_by or '',
        })

    return {
        'mode_change_count': len(shift_rows),
        'mode_changes': shift_rows,
        'Gen-01': outage_summary['generator_duration'].get(
            'Gen-01',
            '0m',
        ),
        'Gen-02': outage_summary['generator_duration'].get(
            'Gen-02',
            '0m',
        ),
        'Unknown': outage_summary['generator_duration'].get(
            'Unknown',
            '0m',
        ),
    }


def build_sensor_summary(start, end):
    qs = SensorReading.objects.filter(
        recorded_at__gte=start,
        recorded_at__lt=end,
    )

    aggregate = qs.aggregate(
        temperature_min=Min('temperature_c'),
        temperature_max=Max('temperature_c'),
        temperature_avg=Avg('temperature_c'),
        humidity_min=Min('humidity_pct'),
        humidity_max=Max('humidity_pct'),
        humidity_avg=Avg('humidity_pct'),
    )

    latest = qs.order_by('-recorded_at', '-id').first()

    def rounded(value):
        return round(float(value), 1) if value is not None else None

    result = {
        'reading_count': qs.count(),
        'temperature_min': rounded(aggregate['temperature_min']),
        'temperature_max': rounded(aggregate['temperature_max']),
        'temperature_avg': rounded(aggregate['temperature_avg']),
        'humidity_min': rounded(aggregate['humidity_min']),
        'humidity_max': rounded(aggregate['humidity_max']),
        'humidity_avg': rounded(aggregate['humidity_avg']),
        'latest': None,
    }

    if latest:
        result['latest'] = {
            'device': latest.device_name or latest.device_id,
            'temperature_c': latest.temperature_c,
            'humidity_pct': latest.humidity_pct,
            'battery_pct': latest.battery_pct,
            'battery_state': latest.battery_state,
            'online': latest.is_online,
            'recorded_at': latest.recorded_at.astimezone(BDT).isoformat(),
        }

    return result


def build_shift_snapshot(report_date, shift):
    start, end = shift_window(report_date, shift)

    outage = build_outage_summary(start, end)

    return {
        'start': start,
        'end': end,
        'outage': outage,
        'generator': build_generator_summary(
            start,
            end,
            outage_summary=outage,
        ),
        'sensor': build_sensor_summary(start, end),
    }


def build_generator_runtime_log(start, end):
    """Actual generator-running intervals, independent of PDB outage duration."""
    rows = []
    totals = {'Gen-01': 0, 'Gen-02': 0, 'Unknown': 0}
    cycles = (OutageCycle.objects.filter(gen_start__isnull=False,
              gen_start__lt=end).order_by('gen_start', 'id'))
    for cycle in cycles:
        # In an unfinished cycle, only count up to the requested window end.
        running_end = (cycle.gen_start + timedelta(seconds=cycle.gen_runtime_sec)
                       if cycle.gen_runtime_sec and cycle.gen_runtime_sec > 0
                       else cycle.cycle_end or cycle.pdb_restored or end)
        if running_end <= start:
            continue
        begin = max(cycle.gen_start, start)
        finish = min(running_end, end)
        if finish <= begin:
            continue
        seconds = int((finish - begin).total_seconds())
        generator = cycle_generator(cycle) or 'Unknown'
        totals[generator] = totals.get(generator, 0) + seconds
        rows.append({
            'start': begin.astimezone(BDT).strftime('%I:%M:%S %p'),
            'end': finish.astimezone(BDT).strftime('%I:%M:%S %p'),
            'duration': fmt_duration(seconds),
            'generator': generator,
        })
    return {'rows': rows,
            'Gen-01': fmt_duration(totals['Gen-01']),
            'Gen-02': fmt_duration(totals['Gen-02']),
            'Unknown': fmt_duration(totals['Unknown']),
            'grand_total': fmt_duration(sum(totals.values())),
            'total_seconds': sum(totals.values())}


def build_previous_day_summary(report_date):
    """Calendar day immediately before the Night Shift operational date."""
    previous_date = report_date - timedelta(days=1)
    start = localize(datetime.combine(previous_date, time(0, 0)))
    end = localize(datetime.combine(report_date, time(0, 0)))
    return {'date': previous_date.isoformat(),
            'outage': build_outage_summary(start, end),
            'generator_log': build_generator_runtime_log(start, end)}


def latest_completed_shift(now=None):
    """
    Return the most recently completed operational shift.

    00:00-08:59 -> previous day's Evening
    09:00-16:59 -> previous day's Night
    17:00-22:59 -> today's Morning
    23:00-23:59 -> today's Evening
    """
    now = now or datetime.now(BDT)

    if now.tzinfo is None:
        now = BDT.localize(now)
    else:
        now = now.astimezone(BDT)

    today = now.date()
    current_time = now.time().replace(tzinfo=None)

    if current_time < time(9, 0):
        report_date = today - timedelta(days=1)
        shift = 'EVENING'

    elif current_time < time(17, 0):
        report_date = today - timedelta(days=1)
        shift = 'NIGHT'

    elif current_time < time(23, 0):
        report_date = today
        shift = 'MORNING'

    else:
        report_date = today
        shift = 'EVENING'

    start, end = shift_window(
        report_date,
        shift,
    )

    return report_date, shift, start, end


DEFAULT_BANDWIDTH_STATUS = (
    'Total Capacity: 4,650 Gbps\n'
    'Used Capacity: 2413.875 Gbps (51.91%) '
    '(Currently Carrying Traffic)\n'
    'Free Capacity: 2,236.125 Gbps (48.09%)\n'
    'Assigned Capacity: Approx. 2,725 Gbps '
    '(Ready for service)\n'
    'Total Active Circuits: 80\n'
    '100G: 19\n'
    '10G: 51\n'
    'STM-16: 1\n'
    'STM-1: 9'
)


def automatic_pfe_status(report_date, shift):
    """
    Generate a friendly PFE status for the Shift Handover.

    MNOC PFE is a separate workflow. A sent PFE record is reflected here
    automatically so the engineer does not need to type the same fact again.
    """
    from .models import MnocPfeReport

    candidate_dates = [report_date]

    _, shift_end = shift_window(
        report_date,
        shift,
    )

    end_date = shift_end.astimezone(BDT).date()

    if end_date not in candidate_dates:
        candidate_dates.append(end_date)

    sent = (
        MnocPfeReport.objects
        .filter(
            report_date__in=candidate_dates,
            status='SENT',
        )
        .order_by('-report_date', '-id')
        .first()
    )

    if sent:
        return 'Normal. Data sent to MNOC.'

    return 'Not sent to MNOC.'


def automatic_generator_status(start, end):
    """
    Produce a useful starting text from the generator mode history.
    The engineer may edit the text before sending.
    """
    latest = (
        GeneratorModeLog.objects
        .filter(switched_at__lte=end)
        .order_by('-switched_at', '-id')
        .first()
    )

    if not latest:
        return 'No generator mode information available.'

    generator = latest.generator or 'Generator'

    when = latest.switched_at.astimezone(BDT)

    lines = [
        f'{generator} is the latest selected generator.',
        f'Last generator mode record: {when:%I:%M %p, %d %b %Y}.',
    ]

    if latest.note:
        lines.append(latest.note)

    return '\n'.join(lines)


def automatic_temperature_text(sensor_summary):
    """
    Convert sensor summary into the wording used in the report composer.
    """
    if not sensor_summary:
        return 'No colocation sensor readings were recorded during the shift.'

    low = sensor_summary.get('temperature_min')
    high = sensor_summary.get('temperature_max')

    if low is None or high is None:
        return 'No colocation sensor readings were recorded during the shift.'

    return (
        'No temperature anomalies were observed during the shift; '
        f'the recorded temperature remained around {low}~{high}°C.'
    )


def automatic_ac_shifting(shift):
    """
    Prefill the AC rotational-turn wording used in the supplied
    Morning / Evening / Night Shift Report formats.
    """
    if shift == 'NIGHT':
        rotation = 'N-3-5-9-12-15-16'
    else:
        rotation = 'E: 3-5-8-11-14-17'

    return (
        'Shifting of active AC as per rotational turn was performed. '
        f'({rotation})'
    )


def report_composer_defaults(report_date, shift):
    """
    Prefill most of Section B automatically.
    """
    snapshot = build_shift_snapshot(
        report_date,
        shift,
    )

    return {
        'snapshot': snapshot,

        'ac_shifting': automatic_ac_shifting(
            shift
        ),

        'network_status': 'Normal',
        'cable_status': 'Normal',

        'pfe_status': automatic_pfe_status(
            report_date,
            shift,
        ),

        'dwdm_status': 'Normal',

        'maintenance_activity': 'None',

        'generator_status_text': automatic_generator_status(
            snapshot['start'],
            snapshot['end'],
        ),

        'temperature_text': automatic_temperature_text(
            snapshot['sensor'],
        ),

        'rain_water_leakage': (
            'No significant rain was observed'
        ),

        'bandwidth_status': DEFAULT_BANDWIDTH_STATUS,
    }


def load_shedding_text(outage_summary):
    """
    Text fallback for the email composer.
    The HTML email also renders the detailed outage table.
    """
    outage_summary = outage_summary or {}

    rows = outage_summary.get('rows', [])

    if not rows:
        return 'No load shedding occurred during the shift.'

    return (
        f'{len(rows)} outage cycle(s) recorded during the shift. '
        f'Total outage duration: '
        f'{outage_summary.get("total_duration", "0m")}.'
    )
