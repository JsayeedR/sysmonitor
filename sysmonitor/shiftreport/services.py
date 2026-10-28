from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re

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
        return f'{hours}h {minutes:02d}m {secs:02d}s'

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
            | Q(pdb_restored__isnull=True, cycle_end__gt=start)
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
            'start': max(cycle.outage_start, start).astimezone(BDT).strftime('%d/%m/%Y %I:%M:%S %p'),
            'end': min(cycle_end, end).astimezone(BDT).strftime('%d/%m/%Y %I:%M:%S %p'),
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
    # The poller explicitly marks stale/offline devices and clears stale values.
    # Use only online samples recorded inside this shift's time window.
    qs = SensorReading.objects.filter(
        recorded_at__gte=start, recorded_at__lt=end, is_online=True,
    )
    temperatures = qs.filter(temperature_c__gte=-40, temperature_c__lte=85)
    humidities = qs.filter(humidity_pct__gte=0, humidity_pct__lte=100)

    aggregate = {**temperatures.aggregate(
        temperature_min=Min('temperature_c'),
        temperature_max=Max('temperature_c'),
        temperature_avg=Avg('temperature_c'),
    ), **humidities.aggregate(
        humidity_min=Min('humidity_pct'),
        humidity_max=Max('humidity_pct'),
        humidity_avg=Avg('humidity_pct'),
    )}

    latest = qs.order_by('-recorded_at', '-id').first()

    def rounded(value):
        return round(float(value), 1) if value is not None else None

    result = {
        'reading_count': qs.count(),
        'temperature_reading_count': temperatures.count(),
        'humidity_reading_count': humidities.count(),
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
    """Calendar-day Generator Log from the SAME engine as email/Telegram.

    monitor.daily_summary.build_daily_summary is authoritative for manual
    cycles, generator-mode switches, cross-midnight clipping and seconds.
    Never infer this table separately from gen_start/gen_runtime_sec.
    Existing SENT report JSON snapshots are not mutated here.
    """
    from monitor.daily_summary import build_daily_summary, fmt_duration as daily_fmt

    local_start = start.astimezone(BDT)
    local_end = end.astimezone(BDT)
    day = local_start.date()
    expected_start = localize(datetime.combine(day, time.min))
    expected_end = localize(datetime.combine(day + timedelta(days=1), time.min))
    if local_start != expected_start or local_end != expected_end:
        raise ValueError('Generator Log requires one complete Bangladesh calendar day.')

    daily = build_daily_summary(day)
    totals = daily['totals']
    rows = [
        {
            'start': row['start'],
            'end': row['end'],
            'duration': daily_fmt(row['duration_sec']),
            'generator': row['generator'],
        }
        for row in daily['rows']
    ]
    return {
        'rows': rows,
        'Gen-01': daily_fmt(totals.get('Gen-01', 0)),
        'Gen-02': daily_fmt(totals.get('Gen-02', 0)),
        'Unknown': daily_fmt(totals.get('UNASSIGNED', 0)),
        'grand_total': daily_fmt(daily['grand_total']),
        'total_seconds': daily['grand_total'],
    }


def build_previous_day_summary(report_date):
    """Calendar day immediately before the Night Shift operational date."""
    previous_date = report_date - timedelta(days=1)
    start = localize(datetime.combine(previous_date, time(0, 0)))
    end = localize(datetime.combine(report_date, time(0, 0)))
    generator_start = localize(datetime.combine(report_date, time(0, 0)))
    generator_end = localize(datetime.combine(
        report_date + timedelta(days=1), time(0, 0)
    ))
    return {'date': previous_date.strftime('%d/%m/%Y'),
            'generator_date': report_date.strftime('%d/%m/%Y'),
            'outage': build_outage_summary(start, end),
            'generator_log': build_generator_runtime_log(
                generator_start, generator_end
            )}


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

    # Night Shift starts on report_date and PFE is read the next morning.
    # Never use a same-day or neighboring report as an accidental fallback.
    pfe_date = report_date + timedelta(days=1) if shift == 'NIGHT' else report_date
    sent = MnocPfeReport.objects.filter(
        report_date=pfe_date, status='SENT',
    ).first()
    if sent:
        return (
            f'PFE Data Sent to MNOC ({pfe_date:%d/%m/%Y}): '
            f'Voltage(V): {sent.voltage_v}; Current (mA): {sent.current_ma}.'
        )
    return 'PFE Data Send to MNOC'


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
    """Independent valid shift-window averages; no invented normality claims."""
    if not sensor_summary:
        return 'Colocation temperature and humidity readings unavailable for this shift.'
    def description(prefix, label, unit):
        lo = sensor_summary.get(prefix + '_min')
        hi = sensor_summary.get(prefix + '_max')
        avg = sensor_summary.get(prefix + '_avg')
        if None in (lo, hi, avg):
            return f'{label}: valid data unavailable.'
        return f'{label}: min {lo}{unit}, max {hi}{unit}, average {avg}{unit}.'
    return ' '.join((
        description('temperature', 'Temperature', '°C'),
        description('humidity', 'Relative humidity', '%RH'),
    ))


CIRCUIT_LIMITS = (20, 76, 1, 11)  # bootstrap only until the WET admin config exists


def wet_circuit_limits():
    """Read WET activation limits (not shift carrying counts)."""
    from .models import WetCircuitConfig
    config = WetCircuitConfig.objects.filter(pk=1).first()
    if config is None:
        return CIRCUIT_LIMITS
    return (config.gbe100, config.ge10, config.stm16, config.stm1)


CIRCUIT_BANDWIDTH = (Decimal('100'), Decimal('10'), Decimal('2.488'), Decimal('0.15552'))
TOTAL_CAPACITY_GBPS = Decimal('4650')


def parse_circuit_counts(text):
    """Read the carrying counts from new-format and historical report text."""
    values = []
    for modern, legacy in (
        ('100GbE', '100G'),
        ('10GbE (LAN PHY)', '10G'),
        ('STM-16', 'STM-16'),
        ('STM-1', 'STM-1'),
    ):
        # The optional denominator is informational: source-of-truth is WET.
        pattern = (r'(?mi)^\s*(?:' + re.escape(modern) + '|'
                   + re.escape(legacy) + r')\s*:\s*(\d+)'
                   r'(?:\s*/\s*\d+)?\s*$')
        match = re.search(pattern, text or '')
        if not match:
            # A new-format report must never silently revert STM-1 to the
            # old nine-circuit bootstrap value (or invent any other count).
            if is_new_circuit_block(text):
                raise ValueError(
                    f'Cannot read traffic-carrying {modern} count from the report.'
                )
            return (19, 51, 1, 9)  # legacy-only compatibility
        values.append(int(match.group(1)))
    return tuple(values)


CIRCUIT_BLOCK_HEADING = 'SMW4 CIRCUIT & BANDWIDTH STATUS'
CIRCUIT_RULE = '-' * 55
CIRCUIT_PENDING_SUBMISSION = '[Shift Report Submission Date & Time]'


def is_new_circuit_block(text):
    """Do not reinterpret previously SENT/legacy format reports."""
    return bool(text and text.startswith(CIRCUIT_RULE + '\n' + CIRCUIT_BLOCK_HEADING + '\n'))


def format_circuit_bandwidth(counts, submitted_at=None):
    """Exact compact SMW4 block; WET max and carrying entries are independent.

    This is used for NEW reports only. The send path freezes the formatted
    timestamp and values before first SMTP transmission. Old SENT rows are not
    rewritten, re-rendered in new format, or retroactively recalculated.
    """
    limits = wet_circuit_limits()
    if len(counts) != 4 or any(type(n) is not int or n < 0 or n > cap
                               for n, cap in zip(counts, limits)):
        raise ValueError('Traffic-carrying circuit counts must be within installed limits.')
    used = sum((n * rate for n, rate in zip(counts, CIRCUIT_BANDWIDTH)), Decimal(0))
    assigned = sum((n * rate for n, rate in zip(limits, CIRCUIT_BANDWIDTH)), Decimal(0))
    if assigned > TOTAL_CAPACITY_GBPS or used > assigned:
        raise ValueError('Wet-activated/used bandwidth exceeds available capacity.')
    no_traffic = assigned - used
    unassigned = TOTAL_CAPACITY_GBPS - assigned
    fmt = lambda n: f'{n.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):,.2f}'
    pct = lambda n: fmt(n * Decimal(100) / TOTAL_CAPACITY_GBPS)
    label = lambda key, value: f'{key:<21}: {value}'
    if submitted_at is None:
        timestamp = CIRCUIT_PENDING_SUBMISSION
    else:
        from django.utils import timezone
        timestamp = timezone.localtime(submitted_at, BDT).strftime('%d/%m/%Y %I:%M:%S %p BDT')
    return '\n'.join((
        CIRCUIT_RULE,
        CIRCUIT_BLOCK_HEADING,
        'Report Date & Time: ' + timestamp,
        CIRCUIT_RULE,
        '\t\t\tCapacity Summary',
        CIRCUIT_RULE,
        label('Total Capacity', f'{fmt(TOTAL_CAPACITY_GBPS)} Gbps'),
        label('Used Capacity', f'{fmt(used)} Gbps ({pct(used)}%)'),
        label('Assigned Capacity', f'{fmt(assigned)} Gbps ({pct(assigned)}%)'),
        label('Assigned, No Traffic', f'{fmt(no_traffic):>8} Gbps ({pct(no_traffic)}%)'),
        label('Unassigned Capacity', f'{fmt(unassigned)} Gbps ({pct(unassigned)}%)'),
        CIRCUIT_RULE,
        'Circuit Status (Traffic-Carrying/ Wet-Activated)',
        CIRCUIT_RULE,
        *(label(k, f'{n}/{cap}') for k, n, cap in zip(
            ('100GbE', '10GbE (LAN PHY)', 'STM-16', 'STM-1'), counts, limits)),
        CIRCUIT_RULE,
        label('Total Wet-Activated', str(sum(limits))),
        label('Carrying Traffic', str(sum(counts))),
        label('Not Carrying Traffic', str(sum(limits) - sum(counts))),
        CIRCUIT_RULE,
    ))



def bandwidth_from_post(post, fallback=''):
    keys = ('circuit_100gbe', 'circuit_10ge', 'circuit_stm16', 'circuit_stm1')
    if not any(key in post for key in keys):
        return post.get('bandwidth_status', fallback).strip()
    try:
        raw = [post[key].strip() for key in keys]
        if any(not re.fullmatch(r'\d+', x) for x in raw):
            raise ValueError()
        counts = tuple(map(int, raw))
    except (KeyError, ValueError):
        raise ValueError('Enter valid whole-number circuit counts for all four categories.')
    return format_circuit_bandwidth(counts)


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

    # Only a completed earlier shift may seed a new report. Never use
    # another engineer's draft or a later-dated SENT report.
    from .models import ShiftReport
    previous = ShiftReport.objects.filter(
        status='SENT', shift_start__lt=snapshot['start'],
    ).order_by('-shift_start', '-pk').first()
    carried = {
        field: getattr(previous, field) if previous else fallback
        for field, fallback in {
            'ac_shifting': automatic_ac_shifting(shift),
            'network_status': 'Normal',
            'cable_status': 'Normal',
            'dwdm_status': 'Normal',
            'maintenance_activity': 'None',
            'rain_water_leakage': 'No significant rain was observed',
            'bandwidth_status': DEFAULT_BANDWIDTH_STATUS,
        }.items()
    }
    return {
        'snapshot': snapshot,
        **carried,
        # These are always calculated for the NEW operational shift.
        'pfe_status': automatic_pfe_status(report_date, shift),
        'generator_status_text': automatic_generator_status(
            snapshot['start'], snapshot['end'],
        ),
        'temperature_text': automatic_temperature_text(snapshot['sensor']),
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
