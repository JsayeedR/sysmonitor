import os
import sys
from datetime import datetime, timedelta

import django
import pytz
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
django.setup()

from django.db.models import Min, Max, Avg

from monitor.models import SensorReading, Event
from monitor.notifications import dispatch as notify_dispatch


BDT = pytz.timezone('Asia/Dhaka')


def fmt_dt(reading):
    if not reading:
        return '—'
    return reading.recorded_at.astimezone(BDT).strftime(
        '%d/%m/%Y %I:%M:%S %p'
    )


def fmt_value(value, suffix=''):
    if value is None:
        return '—'
    return f'{value:.1f}{suffix}'


def build_colocation_report(start_dt, end_dt):
    qs = SensorReading.objects.filter(
        recorded_at__gte=start_dt,
        recorded_at__lt=end_dt,
    ).order_by('recorded_at')

    total = qs.count()

    if not total:
        return (
            '📈 *COLOCATION SENSOR DATA — 3 HOUR REPORT*\n\n'
            f'🕐 Period: {start_dt.astimezone(BDT).strftime("%d/%m/%Y %I:%M %p")}'
            f' → {end_dt.astimezone(BDT).strftime("%d/%m/%Y %I:%M %p")}\n\n'
            '⚠️ No sensor readings were recorded during this period.'
        )

    temp_qs = qs.filter(temperature_c__isnull=False)
    humidity_qs = qs.filter(humidity_pct__isnull=False)

    temp_stats = temp_qs.aggregate(
        minimum=Min('temperature_c'),
        maximum=Max('temperature_c'),
        average=Avg('temperature_c'),
    )

    humidity_stats = humidity_qs.aggregate(
        minimum=Min('humidity_pct'),
        maximum=Max('humidity_pct'),
        average=Avg('humidity_pct'),
    )

    latest = qs.order_by('-recorded_at').first()

    temp_high = temp_qs.order_by('-temperature_c', 'recorded_at').first()
    temp_low = temp_qs.order_by('temperature_c', 'recorded_at').first()
    humidity_high = humidity_qs.order_by('-humidity_pct', 'recorded_at').first()
    humidity_low = humidity_qs.order_by('humidity_pct', 'recorded_at').first()

    online_count = qs.filter(is_online=True).count()
    offline_count = qs.filter(is_online=False).count()

    return (
        '📈 *COLOCATION SENSOR DATA — 3 HOUR REPORT*\n\n'
        f'🕐 *Period:* '
        f'{start_dt.astimezone(BDT).strftime("%d/%m/%Y %I:%M %p")}'
        f' → {end_dt.astimezone(BDT).strftime("%d/%m/%Y %I:%M %p")}\n\n'

        f'📊 *Readings:* {total}\n'
        f'🟢 Online readings: {online_count}\n'
        f'🔴 Offline readings: {offline_count}\n\n'

        '🌡️ *Temperature*\n'
        f'• Minimum: {fmt_value(temp_stats["minimum"], " °C")}'
        f' — {fmt_dt(temp_low)}\n'
        f'• Maximum: {fmt_value(temp_stats["maximum"], " °C")}'
        f' — {fmt_dt(temp_high)}\n'
        f'• Average: {fmt_value(temp_stats["average"], " °C")}\n\n'

        '💧 *Humidity*\n'
        f'• Minimum: {fmt_value(humidity_stats["minimum"], " %")}'
        f' — {fmt_dt(humidity_low)}\n'
        f'• Maximum: {fmt_value(humidity_stats["maximum"], " %")}'
        f' — {fmt_dt(humidity_high)}\n'
        f'• Average: {fmt_value(humidity_stats["average"], " %")}\n\n'

        f'📍 *Latest Reading:* {fmt_dt(latest)}\n'
        f'🌡️ Temperature: {fmt_value(latest.temperature_c, " °C")}\n'
        f'💧 Humidity: {fmt_value(latest.humidity_pct, " %")}\n'
        f'🔋 Battery: {latest.battery_state or "—"}\n'
        f'{"🟢 Sensor Online" if latest.is_online else "🔴 Sensor Offline"}'
    )


def run():
    now_bdt = datetime.now(BDT)

    # Report the latest completed 3-hour scheduled slot:
    # 00-03, 03-06, 06-09, 09-12, 12-15, 15-18, 18-21, or 21-00 BDT.
    period_end = now_bdt.replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    period_end -= timedelta(hours=period_end.hour % 3)
    period_start = period_end - timedelta(hours=3)

    # Deterministic ID for this exact 3-hour slot.
    # Negative values are reserved for synthetic notification cycles.
    cycle_id = -int(period_end.strftime('%Y%m%d%H'))
    report_cycle = SimpleNamespace(id=cycle_id)

    date_str = now_bdt.strftime('%d/%m/%Y %I:%M:%S %p')

    try:
        report_text = build_colocation_report(period_start, period_end)

        print(
            f'[{date_str}] Building Colocation 3-hour report '
            f'for {period_start.strftime("%d/%m/%Y %H:%M")} '
            f'→ {period_end.strftime("%d/%m/%Y %H:%M")}'
        )

        notify_dispatch(
            'COLOCATION_DATA',
            cycle=report_cycle,
            extra=report_text,
        )

        print(f'[{date_str}] Colocation 3-hour report dispatched.')

        slot_qs = SensorReading.objects.filter(
            recorded_at__gte=period_start,
            recorded_at__lt=period_end,
        )

        reading_count = slot_qs.count()
        latest = slot_qs.order_by(
            '-recorded_at'
        ).first()

        period_label = (
            f'{period_start.strftime("%H:%M")}'
            f'–{period_end.strftime("%H:%M")}'
        )

        if latest:
            event_message = (
                f'Colocation report sent — {period_label}'
                f' | Temp {fmt_value(latest.temperature_c, "°C")}'
                f' | Hum {fmt_value(latest.humidity_pct, "%")}'
                f' | {reading_count} readings'
            )
        else:
            event_message = (
                f'Colocation report sent — {period_label}'
                ' | No sensor readings'
            )

        Event.objects.create(
            device=None,
            level='INFO',
            message=event_message,
        )

    except Exception as e:
        print(f'[{date_str}] Colocation 3-hour report FAILED: {e}')

        Event.objects.create(
            device=None,
            level='CRITICAL',
            message=f'Colocation 3-hour report FAILED — {str(e)}',
        )

        sys.exit(1)


if __name__ == '__main__':
    run()
