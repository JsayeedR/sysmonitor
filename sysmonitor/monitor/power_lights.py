"""Dashboard indicator states from existing, recorded SysMonitor data.

Never infer an NVR power-on condition from an IP that is not actually polled.
"""

def indicators(devices, active_cycle):
    pdb = next((d for d in devices if d.get('ip') == '192.168.30.56'), None)
    nvr = next((d for d in devices if 'nvr' in (d.get('name') or '').casefold()
                or 'nvr' in (d.get('desc') or '').casefold()), None)
    # A cycle has no discrete generator-stop field; completed cycle marks STOP.
    if active_cycle is None:
        generator = 'OFF'
    elif active_cycle.gen_start and not active_cycle.cycle_end:
        generator = 'ON'
    elif active_cycle.gen_start and active_cycle.cycle_end:
        generator = 'OFF'
    else:
        generator = 'OFF'
    def device_state(record):
        if record is None: return 'UNKNOWN'
        raw = record.get('status')
        return raw if raw in ('UP','DOWN') else 'UNKNOWN'
    return {'pdb': device_state(pdb), 'generator': generator,
            'equipment': device_state(nvr),
            'equipment_source': 'Recorded NVR ping' if nvr else 'NVR monitor not configured'}
