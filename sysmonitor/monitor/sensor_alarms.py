"""Persistent environmental alarm detection; only fresh online Tuya readings.

Two consecutive matching readings by default; a recovery also needs two.
Each threshold condition has its own cooldown and persistent state.
"""
from datetime import timedelta
from django.utils import timezone
from monitor.models import Event, SensorAlarmConfig, SensorAlarmState
from monitor.notifications import dispatch


def evaluate_sensor_alarms(reading, send=dispatch):
    if reading is None or not reading.is_online:
        return []
    config = SensorAlarmConfig.objects.order_by('pk').first()
    if not config:
        return []
    confirmed = min(10, max(1, config.alarm_confirmation_readings))
    cooldown = timedelta(minutes=max(1, config.alarm_cooldown_minutes))
    definitions = (
        ('TEMP_LOW', reading.temperature_c, config.temperature_low, '<'),
        ('TEMP_HIGH', reading.temperature_c, config.temperature_high, '>'),
        ('HUM_LOW', reading.humidity_pct, config.humidity_low, '<'),
        ('HUM_HIGH', reading.humidity_pct, config.humidity_high, '>'),
    )
    results = []
    for key, value, limit, op in definitions:
        if limit is None or value is None:
            continue
        violating = value < limit if op == '<' else value > limit
        state, _ = SensorAlarmState.objects.get_or_create(device_id=reading.device_id, condition=key)
        status = None
        if violating:
            state.recovery_count = 0
            state.candidate_count = min(confirmed, state.candidate_count + 1)
            if state.candidate_count >= confirmed and (not state.active or state.last_sent_at is None or timezone.now() - state.last_sent_at >= cooldown):
                status = 'ALARM' if not state.active else 'REMINDER'
        else:
            state.candidate_count = 0
            if state.active:
                state.recovery_count = min(confirmed, state.recovery_count + 1)
                if state.recovery_count >= confirmed:
                    status = 'RECOVERED'
            else:
                state.recovery_count = 0
        if status:
            label = f'{key.replace("_", " ")} {op} {limit}'
            message = {'status': status, 'device': reading.device_name or reading.device_id,
                       'temperature': reading.temperature_c, 'humidity': reading.humidity_pct,
                       'threshold': label}
            dispatch_result = send('SENSOR_ALERT', extra=message)
            # A dispatch with failed sends should retry next poll rather than
            # pretending recipients were reached.
            delivery_ok = isinstance(dispatch_result, dict) and dispatch_result.get('failed', 0) == 0
            if delivery_ok:
                if status == 'RECOVERED':
                    state.active = False
                    state.recovery_count = 0
                    state.last_sent_at = None
                else:
                    state.active = True
                    state.last_sent_at = timezone.now()
                Event.objects.create(device=None, level='INFO' if status == 'RECOVERED' else 'ALARM',
                                     message=f'Colocation {status}: {label}; sensor={reading.device_id}')
                results.append((key, status))
            else:
                Event.objects.create(device=None, level='ALARM',
                                     message=f'Colocation notification delivery failed: {key} {status}')
        state.save()
    return results
