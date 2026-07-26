import os
import sys
import django
import subprocess
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
django.setup()

import pytz
BDT = pytz.timezone('Asia/Dhaka')

from monitor.models import (
    Device, DeviceStatus, Event, SystemStatus, OutageCycle, MaintenanceMode
)
from monitor.notifications import dispatch as notify_dispatch


def is_maintenance_active():
    """Returns True if maintenance mode is currently active in DB."""
    from django.utils import timezone
    m = MaintenanceMode.objects.filter(id=1).first()
    if not m or not m.is_active:
        return False
    # Auto-expire if past expiry time
    if m.expires_at and timezone.now() > m.expires_at:
        m.is_active = False
        m.save()
        print('[MAINTENANCE] Auto-expired — maintenance mode OFF')
        return False
    return True

# ── Constants ─────────────────────────────────────────────────────────────────
PING_INTERVAL       = 3    # seconds between pings
CONFIRM_THRESHOLD   = 2    # consecutive failures needed to confirm outage
ATS_BLIP_THRESHOLD  = 180  # NVR down less than this = just ATS blip
AVR_STUCK_THRESHOLD = 300  # PDB restored but NVR still on gen = alarm
CRITICAL_THRESHOLD  = 600  # both down longer than this = critical


# ── Helpers ───────────────────────────────────────────────────────────────────
def now_utc():
    return datetime.now(timezone.utc)


def fmt(dt):
    if not dt:
        return '—'
    return dt.astimezone(BDT).strftime('%I:%M:%S %p')


def duration_fmt(seconds):
    if not seconds:
        return '0s'
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f'{h}h {m:02d}m {s:02d}s'
    if m:
        return f'{m}m {s:02d}s'
    return f'{s}s'


def ping(ip):
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '2', ip],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'time=' in line:
                    ms = float(line.split('time=')[1].split(' ')[0])
                    return True, int(ms)
            return True, None
        return False, None
    except Exception:
        return False, None


def log_event(device, level, message):
    Event.objects.create(device=device, level=level, message=message)
    t = datetime.now(BDT).strftime('%I:%M:%S %p')
    print(f'[{t}] [{level}] {message}')


def set_system_status(status, note=''):
    SystemStatus.objects.update_or_create(
        id=1, defaults={'status': status, 'note': note}
    )


# ── State ─────────────────────────────────────────────────────────────────────
class MonitorState:
    def __init__(self):
        self.nvr_up           = None
        self.holder_up        = None
        self.cycle            = None
        self.nvr_down_at      = None
        self.both_down_at     = None
        self.gen_start_at     = None
        self.pdb_restored_at  = None
        self.avr_warned       = False
        self.critical_warned  = False

        # ── 2-ping confirmation counters ──────────────────────────────────────
        # These count consecutive failures before triggering an outage event.
        # Restorations are still instant (1 ping success = restored).
        self.holder_fail_count = 0   # consecutive holder ping failures
        self.nvr_fail_count    = 0   # consecutive NVR ping failures

        # Phase values:
        # NORMAL       → everything fine
        # NVR_BLIP     → NVR briefly down, holder still up
        # OUTAGE       → both down, waiting for generator
        # GEN_RUNNING  → NVR back up on generator, holder still down
        # PDB_RESTORED → holder came back up, waiting for NVR ATS blip
        # SWITCHBACK   → NVR briefly down after PDB restored
        self.phase = 'NORMAL'


STATE = MonitorState()


# ── Device helpers ────────────────────────────────────────────────────────────
def get_devices():
    """Get Holder and NVR by name (case-insensitive)"""
    holder = Device.objects.filter(
        is_active=True, name__icontains='holder'
    ).first()
    nvr = Device.objects.filter(
        is_active=True, name__icontains='nvr'
    ).first()
    return holder, nvr


def save_status(device, is_up, ms):
    DeviceStatus.objects.create(
        device=device,
        status='UP' if is_up else 'DOWN',
        response_ms=ms
    )
    # Keep only latest 1000 records per device
    ids_to_keep = DeviceStatus.objects.filter(
        device=device
    ).order_by('-checked_at').values_list('id', flat=True)[:1000]
    DeviceStatus.objects.filter(
        device=device
    ).exclude(id__in=list(ids_to_keep)).delete()


# ── Cycle helpers ─────────────────────────────────────────────────────────────
def start_cycle(outage_start=None):
    ts = outage_start or now_utc()
    cycle = OutageCycle.objects.create(
        outage_start=ts,
        cycle_type='INCOMPLETE',
        is_complete=False,
    )
    STATE.cycle = cycle
    print(f'[CYCLE] New cycle started at {fmt(ts)}')
    return cycle


def complete_cycle(cycle_type='NORMAL', alarm_reason=''):
    c = STATE.cycle
    if not c:
        return None

    now          = now_utc()
    c.cycle_end  = now
    c.cycle_type = cycle_type
    c.is_complete  = True
    c.alarm_reason = alarm_reason

    # PDB outage: outage_start → pdb_restored
    if c.outage_start and c.pdb_restored:
        c.pdb_duration_sec = int(
            (c.pdb_restored - c.outage_start).total_seconds()
        )
    elif c.outage_start and c.cycle_end:
        c.pdb_duration_sec = int(
            (c.cycle_end - c.outage_start).total_seconds()
        )

    # Generator runtime: gen_start → cycle_end
    if c.gen_start and c.cycle_end:
        c.gen_runtime_sec = int(
            (c.cycle_end - c.gen_start).total_seconds()
        )

    c.save()
    notify_dispatch('COMPLETE', cycle=c)
    print(
        f'[CYCLE] Completed — type={cycle_type} '
        f'PDB={duration_fmt(c.pdb_duration_sec)} '
        f'GEN={duration_fmt(c.gen_runtime_sec)}'
    )
    STATE.cycle = None
    return c


def force_close_current_cycle(reason='NORMAL'):
    """
    Force-close the current active cycle when a new confirmed outage starts
    before the previous one was properly completed.

    ATS blip guard: if the cycle is younger than 90 seconds AND has no
    pdb_restored event (grid never came back), it's almost certainly an
    ATS switching artefact — delete it silently instead of recording it
    as a fake completed outage.
    """
    c = STATE.cycle
    if not c:
        return
    from datetime import timedelta
    now        = now_utc()
    close_time = now - timedelta(seconds=1)

    # ATS blip guard — short cycle with no real restoration = delete silently
    ATS_BLIP_MAX_SEC = 90
    age = int((now - c.outage_start).total_seconds()) if c.outage_start else 999
    if age < ATS_BLIP_MAX_SEC and not c.pdb_restored:
        print(f'[CYCLE] Discarding ATS blip cycle ID:{c.id} '
              f'(only {age}s old, no PDB restoration) — not recording as outage')
        c.delete()
        STATE.cycle           = None
        STATE.gen_start_at    = None
        STATE.pdb_restored_at = None
        STATE.avr_warned      = False
        STATE.critical_warned = False
        return

    c.cycle_end   = close_time
    c.is_complete = True
    c.cycle_type  = reason

    if c.outage_start and c.pdb_restored:
        c.pdb_duration_sec = int(
            (c.pdb_restored - c.outage_start).total_seconds()
        )
    elif c.outage_start:
        c.pdb_duration_sec = int(
            (close_time - c.outage_start).total_seconds()
        )
    if c.gen_start:
        c.gen_runtime_sec = int(
            (close_time - c.gen_start).total_seconds()
        )
    c.save()
    notify_dispatch('COMPLETE', cycle=c)
    print(f'[CYCLE] Force-closed cycle ID:{c.id} — '
          f'PDB={duration_fmt(c.pdb_duration_sec)} '
          f'GEN={duration_fmt(c.gen_runtime_sec)}')
    STATE.cycle           = None
    STATE.gen_start_at    = None
    STATE.pdb_restored_at = None
    STATE.avr_warned      = False
    STATE.critical_warned = False


def close_orphan(c, reason='NORMAL'):
    """Close an incomplete cycle that was never properly finished.
    ATS blip guard: discard very short cycles with no PDB restoration.
    """
    now = now_utc()

    # ATS blip guard
    ATS_BLIP_MAX_SEC = 90
    age = int((now - c.outage_start).total_seconds()) if c.outage_start else 999
    if age < ATS_BLIP_MAX_SEC and not c.pdb_restored:
        print(f'[CYCLE] Discarding ATS blip orphan ID:{c.id} '
              f'({age}s, no PDB restoration)')
        c.delete()
        return

    c.cycle_end   = now
    c.is_complete = True
    c.cycle_type  = reason

    if c.outage_start and c.pdb_restored:
        c.pdb_duration_sec = int(
            (c.pdb_restored - c.outage_start).total_seconds()
        )
    elif c.outage_start:
        c.pdb_duration_sec = int(
            (now - c.outage_start).total_seconds()
        )
    if c.gen_start:
        c.gen_runtime_sec = int(
            (now - c.gen_start).total_seconds()
        )
    c.save()
    notify_dispatch('COMPLETE', cycle=c)
    print(f'[CYCLE] Orphan ID:{c.id} closed — '
          f'PDB={duration_fmt(c.pdb_duration_sec)} '
          f'GEN={duration_fmt(c.gen_runtime_sec)}')


# ── Confirmation logic ────────────────────────────────────────────────────────
def apply_confirmation(raw_holder_up, raw_nvr_up):
    """
    Apply 2-consecutive-ping confirmation for DOWN events.
    UP events are instant (1 ping success = restored).

    Returns (confirmed_holder_up, confirmed_nvr_up)
    where confirmed = what we should act on this cycle.
    """
    # ── Holder confirmation ───────────────────────────────────────────────────
    if not raw_holder_up:
        STATE.holder_fail_count += 1
        if STATE.holder_fail_count >= CONFIRM_THRESHOLD:
            confirmed_holder_up = False   # confirmed DOWN
        else:
            # Not yet confirmed — treat as still UP for logic purposes
            confirmed_holder_up = STATE.holder_up if STATE.holder_up is not None else True
            t = datetime.now(BDT).strftime('%I:%M:%S %p')
            print(f'[{t}] [CONFIRM] Holder fail {STATE.holder_fail_count}/{CONFIRM_THRESHOLD} — waiting...')
    else:
        STATE.holder_fail_count = 0       # reset on success
        confirmed_holder_up = True

    # ── NVR confirmation ──────────────────────────────────────────────────────
    if not raw_nvr_up:
        STATE.nvr_fail_count += 1
        if STATE.nvr_fail_count >= CONFIRM_THRESHOLD:
            confirmed_nvr_up = False      # confirmed DOWN
        else:
            confirmed_nvr_up = STATE.nvr_up if STATE.nvr_up is not None else True
            t = datetime.now(BDT).strftime('%I:%M:%S %p')
            print(f'[{t}] [CONFIRM] NVR fail {STATE.nvr_fail_count}/{CONFIRM_THRESHOLD} — waiting...')
    else:
        STATE.nvr_fail_count = 0          # reset on success
        confirmed_nvr_up = True

    return confirmed_holder_up, confirmed_nvr_up


# ── Main transition logic ─────────────────────────────────────────────────────
def handle_changes(holder_dev, nvr_dev, holder_up, nvr_up):
    # If maintenance mode is active — skip cycle creation and notifications
    if is_maintenance_active():
        STATE.holder_up = holder_up
        STATE.nvr_up    = nvr_up
        t = datetime.now(BDT).strftime('%I:%M:%S %p')
        print(f'[{t}] [MAINTENANCE] Ping OK — suppressing events. Holder={holder_up} NVR={nvr_up}')
        return

    prev_holder = STATE.holder_up
    prev_nvr    = STATE.nvr_up
    now         = now_utc()
    phase       = STATE.phase

    holder_changed = (prev_holder is not None) and (holder_up != prev_holder)
    nvr_changed    = (prev_nvr    is not None) and (nvr_up    != prev_nvr)

    # ── Holder went DOWN ──────────────────────────────────────────────────────
    if holder_changed and not holder_up:
        STATE.both_down_at    = now
        STATE.critical_warned = False

        if nvr_up:
            log_event(holder_dev, 'NOTICE',
                'Holder DOWN but NVR still UP — '
                'possible WiFi lag. Monitoring...')
        else:
            if STATE.cycle:
                force_close_current_cycle('NORMAL')
            STATE.phase = 'OUTAGE'
            start_cycle(outage_start=now)
            log_event(holder_dev, 'OUTAGE',
                f'{holder_dev} is DOWN — PDB power lost')
            set_system_status('OUTAGE',
                'Both Holder and NVR are DOWN — power outage detected')
            # notify_dispatch() already prevents duplicate sends for THIS
            # specific cycle (keyed by cycle.id in already_sent()) — no need
            # for a separate flag here. A process-wide flag was used before,
            # but since it was never reset per-cycle, it silently blocked
            # every outage notification after the first one each time the
            # service ran.
            notify_dispatch('OUTAGE_START', cycle=STATE.cycle)

    # ── Holder came UP ────────────────────────────────────────────────────────
    if holder_changed and holder_up:
        if phase == 'GEN_RUNNING':
            STATE.pdb_restored_at = now
            STATE.avr_warned      = False

            if STATE.cycle:
                STATE.cycle.pdb_restored = now
                STATE.cycle.save()

            pdb_dur = 0
            if STATE.cycle and STATE.cycle.outage_start:
                pdb_dur = int(
                    (now - STATE.cycle.outage_start).total_seconds()
                )

            log_event(holder_dev, 'NORMAL',
                f'PDB power restored — Holder UP. '
                f'PDB was out for {duration_fmt(pdb_dur)}. '
                f'Watching for ATS switchback...')

            if nvr_up:
                if STATE.cycle:
                    # Guard: if outage was very short (<60s) AND generator never
                    # properly started (gen_start=None), this is almost certainly
                    # an ATS switching blip — both devices briefly appeared UP
                    # during the ATS transfer. Don't close as a completed outage;
                    # instead keep the cycle open and flip back to OUTAGE phase
                    # so we track the real outage duration.
                    outage_dur = 0
                    if STATE.cycle.outage_start:
                        outage_dur = int((now - STATE.cycle.outage_start).total_seconds())
                    is_ats_blip = (outage_dur < 60 and STATE.cycle.gen_start is None)

                    if is_ats_blip:
                        print(f'[{fmt(now)}] ATS blip detected — Holder+NVR briefly UP '
                              f'({outage_dur}s) but gen never started. '
                              f'Staying in cycle ID:{STATE.cycle.id}')
                        STATE.phase = 'OUTAGE'
                        # Don't complete the cycle — wait for real restoration
                    else:
                        STATE.cycle.cycle_end = now
                        c = complete_cycle('NORMAL')
                        if c:
                            log_event(holder_dev, 'NORMAL',
                                f'{nvr_dev} is back UP')
                            log_event(holder_dev, 'NORMAL',
                                f'✅ Full power cycle complete — '
                                f'PDB out: {c.pdb_duration_fmt()} | '
                                f'Generator runtime: {c.gen_runtime_fmt()}')
                        STATE.phase = 'NORMAL'
                        set_system_status('NORMAL',
                            'Full power cycle complete — all systems normal')
                else:
                    STATE.phase = 'NORMAL'
                    set_system_status('NORMAL',
                        'Full power cycle complete — all systems normal')
            else:
                STATE.phase = 'PDB_RESTORED'
                set_system_status('ATS',
                    'PDB restored. Waiting for ATS switchback to grid power.')

        elif phase == 'OUTAGE':
            log_event(holder_dev, 'NORMAL',
                f'{holder_dev} is back UP')

        else:
            log_event(holder_dev, 'NORMAL',
                f'{holder_dev} is back UP')
            if nvr_up:
                STATE.phase = 'NORMAL'
                set_system_status('NORMAL',
                    'All devices UP — normal operation')

    # ── NVR went DOWN ─────────────────────────────────────────────────────────
    if nvr_changed and not nvr_up:
        STATE.nvr_down_at = now

        if holder_up:
            if phase in ('PDB_RESTORED', 'GEN_RUNNING'):
                STATE.phase = 'SWITCHBACK'
                log_event(nvr_dev, 'NOTICE',
                    'NVR DOWN but Holder UP — '
                    'ATS switchover in progress. Monitoring...')
            else:
                STATE.phase = 'NVR_BLIP'
                log_event(nvr_dev, 'NOTICE',
                    'NVR DOWN but Holder UP — '
                    'ATS switchover or AVR issue. Monitoring...')
        else:
            # Both Holder and NVR are DOWN.
            # Check if Holder JUST went down this same tick or very recently
            # (within 2x ping interval) — if so this is the SAME outage event,
            # not a new one. Holder going down first set both_down_at and phase
            # was still NORMAL because NVR was briefly still up.
            holder_just_went_down = (
                STATE.both_down_at is not None and
                int((now - STATE.both_down_at).total_seconds()) <= (PING_INTERVAL * 2 + 2)
            )

            if phase == 'GEN_RUNNING':
                # Generator already running, Holder still down — NVR dipping
                # down again is just ATS switching activity, not a new
                # outage. Stay in the SAME cycle, just flip back to OUTAGE
                # phase so future NVR-up events are handled correctly again.
                STATE.phase = 'OUTAGE'
                STATE.critical_warned = False
                print(f'[{fmt(now)}] NVR blip during GEN_RUNNING — '
                      f'staying in cycle ID:{STATE.cycle.id if STATE.cycle else "?"}')
            elif phase != 'OUTAGE':
                if holder_just_went_down and STATE.cycle is None:
                    # Holder went down moments ago, NVR now also down — same outage.
                    # Start cycle from when Holder actually went down (both_down_at)
                    STATE.phase = 'OUTAGE'
                    STATE.critical_warned = False
                    start_cycle(outage_start=STATE.both_down_at)
                    notify_dispatch('OUTAGE_START', cycle=STATE.cycle)
                else:
                    # Genuinely separate NVR-triggered outage
                    if STATE.cycle:
                        force_close_current_cycle('NORMAL')
                    STATE.phase        = 'OUTAGE'
                    STATE.both_down_at = now
                    STATE.critical_warned = False
                    start_cycle(outage_start=now)
                    notify_dispatch('OUTAGE_START', cycle=STATE.cycle)
            log_event(nvr_dev, 'OUTAGE',
                f'{nvr_dev} is DOWN — '
                'Power outage in progress')
            set_system_status('OUTAGE',
                'Both Holder and NVR are DOWN — power outage detected')

    # ── NVR came UP ───────────────────────────────────────────────────────────
    if nvr_changed and nvr_up:

        if phase == 'NVR_BLIP':
            dur = 0
            if STATE.nvr_down_at:
                dur = int((now - STATE.nvr_down_at).total_seconds())
            if dur < ATS_BLIP_THRESHOLD:
                log_event(nvr_dev, 'ATS',
                    f'ATS switchover complete. '
                    f'NVR offline for {duration_fmt(dur)}. '
                    f'PDB active throughout.')
            else:
                log_event(nvr_dev, 'NOTICE',
                    f'NVR back UP after {duration_fmt(dur)}.')
            STATE.phase = 'NORMAL'
            set_system_status('NORMAL',
                'All devices UP — normal operation')

        elif phase == 'OUTAGE':
            STATE.phase        = 'GEN_RUNNING'
            STATE.gen_start_at = now

            if STATE.cycle:
                STATE.cycle.gen_start = now
                STATE.cycle.save()

            delay = 0
            if STATE.both_down_at:
                delay = int((now - STATE.both_down_at).total_seconds())

            log_event(nvr_dev, 'GEN-UP',
                f'Generator running — NVR UP after {duration_fmt(delay)}. '
                f'Holder still DOWN.')
            set_system_status('GENERATOR',
                f'Generator running — PDB still out. NVR UP at {fmt(now)}')

        elif phase == 'SWITCHBACK':
            if STATE.cycle:
                c = complete_cycle('NORMAL')
                if c:
                    log_event(nvr_dev, 'NORMAL',
                        f'{nvr_dev} is back UP')
                    log_event(nvr_dev, 'NORMAL',
                        f'✅ Full power cycle complete — '
                        f'PDB out: {c.pdb_duration_fmt()} | '
                        f'Generator runtime: {c.gen_runtime_fmt()}')
            else:
                log_event(nvr_dev, 'NORMAL',
                    f'{nvr_dev} is back UP')

            STATE.phase           = 'NORMAL'
            STATE.avr_warned      = False
            STATE.critical_warned = False
            set_system_status('NORMAL',
                'Full power cycle complete — all systems normal')

        elif phase == 'PDB_RESTORED':
            if STATE.cycle:
                c = complete_cycle('NORMAL')
                if c:
                    log_event(nvr_dev, 'NORMAL',
                        f'{nvr_dev} is back UP')
                    log_event(nvr_dev, 'NORMAL',
                        f'✅ Full power cycle complete — '
                        f'PDB out: {c.pdb_duration_fmt()} | '
                        f'Generator runtime: {c.gen_runtime_fmt()}')
            else:
                log_event(nvr_dev, 'NORMAL',
                    f'{nvr_dev} is back UP')

            STATE.phase           = 'NORMAL'
            STATE.avr_warned      = False
            STATE.critical_warned = False
            set_system_status('NORMAL',
                'Full power cycle complete — all systems normal')

        else:
            log_event(nvr_dev, 'NORMAL',
                f'{nvr_dev} is back UP')
            if holder_up:
                STATE.phase = 'NORMAL'
                set_system_status('NORMAL',
                    'All devices UP — normal operation')

    # Save current state
    STATE.holder_up = holder_up
    STATE.nvr_up    = nvr_up


# ── Periodic checks ───────────────────────────────────────────────────────────
def periodic_checks(holder_dev, nvr_dev):
    now = now_utc()

    if (STATE.phase == 'OUTAGE'
            and STATE.both_down_at
            and not STATE.critical_warned):
        secs = int((now - STATE.both_down_at).total_seconds())
        if secs >= CRITICAL_THRESHOLD:
            log_event(None, 'CRITICAL',
                f'TOTAL POWER FAILURE — '
                f'Both devices DOWN for {duration_fmt(secs)}!')
            set_system_status('OUTAGE',
                f'CRITICAL: Total power failure — '
                f'{duration_fmt(secs)} and counting!')
            STATE.critical_warned = True
            if STATE.cycle:
                STATE.cycle.cycle_type   = 'CRITICAL'
                STATE.cycle.alarm_reason = (
                    f'Total power failure — '
                    f'both devices down {duration_fmt(secs)}'
                )
                STATE.cycle.save()
                notify_dispatch('CRITICAL', cycle=STATE.cycle, extra=duration_fmt(secs))

    if (STATE.phase == 'PDB_RESTORED'
            and STATE.pdb_restored_at
            and not STATE.avr_warned):
        secs = int((now - STATE.pdb_restored_at).total_seconds())
        if secs >= AVR_STUCK_THRESHOLD:
            log_event(nvr_dev, 'CRITICAL',
                f'AVR/ATS STUCK — PDB restored {duration_fmt(secs)} ago '
                f'but NVR still on generator!')
            STATE.avr_warned = True
            if STATE.cycle:
                STATE.cycle.cycle_type   = 'ALARM'
                STATE.cycle.alarm_reason = (
                    f'AVR stuck on generator '
                    f'{duration_fmt(secs)} after PDB restored'
                )
                STATE.cycle.save()
                notify_dispatch('ALARM', cycle=STATE.cycle)
            set_system_status('GENERATOR',
                'ALARM: AVR stuck — '
                'PDB restored but generator still running!')


# ── Startup state check ───────────────────────────────────────────────────────
def ping_with_confirmation(ip, attempts=3, required_down=2):
    """
    Ping multiple times to confirm a device is genuinely DOWN before acting.
    Returns (is_up, last_ms). Device is considered DOWN only if it fails
    `required_down` out of `attempts` pings. Prevents false outage detection
    on server startup when the network stack may not be fully ready.
    """
    results = []
    last_ms = None
    for i in range(attempts):
        up, ms = ping(ip)
        results.append(up)
        if up:
            last_ms = ms
        if i < attempts - 1:
            time.sleep(1)  # small gap between startup confirmation pings
    downs = results.count(False)
    is_up = downs < required_down  # need required_down failures to call it DOWN
    return is_up, last_ms


def check_startup_state(holder_dev, nvr_dev):
    print('Checking device states on startup...')
    print('  Running startup confirmation pings (3x each, 1s apart)...')

    holder_up, holder_ms = ping_with_confirmation(holder_dev.ip_address)
    nvr_up,    nvr_ms    = ping_with_confirmation(nvr_dev.ip_address)

    save_status(holder_dev, holder_up, holder_ms)
    save_status(nvr_dev,    nvr_up,    nvr_ms)

    t   = datetime.now(BDT).strftime('%I:%M:%S %p')
    now = now_utc()

    orphans = OutageCycle.objects.filter(is_complete=False)

    if holder_up and nvr_up:
        print(f'[{t}] Startup: Both UP → NORMAL')
        for c in orphans:
            if c.outage_start:
                close_orphan(c, 'NORMAL')
            else:
                c.delete()
                print(f'[{t}] Deleted junk cycle ID:{c.id}')
        STATE.phase     = 'NORMAL'
        STATE.holder_up = True
        STATE.nvr_up    = True
        set_system_status('NORMAL', 'All devices UP — normal operation')

    elif not holder_up and nvr_up:
        print(f'[{t}] Startup: Holder DOWN + NVR UP → GEN_RUNNING')
        # Sort oldest first so we resume the EARLIEST (real) outage start
        orphan_list   = list(orphans.order_by('outage_start'))
        valid_orphans = [c for c in orphan_list if c.outage_start]
        junk_orphans  = [c for c in orphan_list if not c.outage_start]

        for c in junk_orphans:
            c.delete()
            print(f'[{t}] Deleted junk cycle ID:{c.id} (no outage_start)')

        if valid_orphans:
            # Resume the earliest one — it has the real outage_start time
            earliest = valid_orphans[0]
            for old in valid_orphans[1:]:
                close_orphan(old, 'NORMAL')
                print(f'[{t}] Closed extra orphan ID:{old.id}')

            STATE.cycle = earliest

            if earliest.outage_start:
                STATE.both_down_at = earliest.outage_start

            if earliest.gen_start:
                STATE.gen_start_at = earliest.gen_start
            else:
                # Generator running but gen_start not saved — best guess is now
                earliest.gen_start = now
                earliest.save()
                STATE.gen_start_at = now
                print(f'[{t}] Warning: gen_start missing on cycle ID:{earliest.id} — set to now')

            if earliest.pdb_restored:
                STATE.pdb_restored_at = earliest.pdb_restored
                STATE.phase = 'PDB_RESTORED'
            else:
                STATE.phase = 'GEN_RUNNING'

            elapsed = duration_fmt(int((now - earliest.outage_start).total_seconds()))
            print(f'[{t}] Resuming cycle ID:{earliest.id} '
                  f'from {fmt(earliest.outage_start)} '
                  f'(outage started {elapsed} ago)')
        else:
            # No prior record at all — create a fresh cycle
            c = start_cycle(outage_start=now)
            c.gen_start = now
            c.save()
            STATE.gen_start_at = now
            STATE.phase = 'GEN_RUNNING'
            print(f'[{t}] No prior cycle found — started new cycle')

        STATE.holder_up = False
        STATE.nvr_up    = True
        set_system_status('GENERATOR',
            'Startup: Holder DOWN + NVR UP — generator running')

    elif not holder_up and not nvr_up:
        print(f'[{t}] Startup: Both DOWN → OUTAGE')
        # Use OLDEST orphan — it has the real original outage_start time
        orphan_list   = list(orphans.order_by('outage_start'))
        valid_orphans = [c for c in orphan_list if c.outage_start]
        junk_orphans  = [c for c in orphan_list if not c.outage_start]

        for c in junk_orphans:
            c.delete()
            print(f'[{t}] Deleted junk cycle ID:{c.id} (no outage_start)')

        if valid_orphans:
            earliest = valid_orphans[0]
            for old in valid_orphans[1:]:
                close_orphan(old, 'NORMAL')
                print(f'[{t}] Closed extra orphan ID:{old.id}')
            STATE.cycle        = earliest
            STATE.both_down_at = earliest.outage_start or now
        else:
            start_cycle(outage_start=now)
            STATE.both_down_at = now

        STATE.phase     = 'OUTAGE'
        STATE.holder_up = False
        STATE.nvr_up    = False
        set_system_status('OUTAGE',
            'Startup: Both DOWN — active power outage!')

    else:
        print(f'[{t}] Startup: NVR DOWN + Holder UP → NVR_BLIP')
        for c in orphans:
            if c.outage_start:
                close_orphan(c, 'NORMAL')
            else:
                c.delete()
        STATE.phase       = 'NVR_BLIP'
        STATE.holder_up   = True
        STATE.nvr_up      = False
        STATE.nvr_down_at = now
        set_system_status('DEVICE_DOWN',
            'Startup: NVR DOWN + Holder UP — monitoring...')


# ── Main loop ─────────────────────────────────────────────────────────────────
def run():
    print('=' * 55)
    print('  SysMonitor — Ping & Outage Analyzer')
    print(f'  Ping interval  : {PING_INTERVAL}s')
    print(f'  Confirm threshold: {CONFIRM_THRESHOLD} consecutive pings')
    print(f'  ATS threshold  : {ATS_BLIP_THRESHOLD}s')
    print(f'  AVR threshold  : {AVR_STUCK_THRESHOLD}s')
    print(f'  Critical       : {CRITICAL_THRESHOLD}s')
    print('=' * 55)

    holder_dev, nvr_dev = get_devices()
    if not holder_dev or not nvr_dev:
        print('ERROR: Holder or NVR device not found in database.')
        print('Please add both devices via http://YOUR_IP:8000/devices/')
        print('  Device name must contain "holder" and "nvr"')
        sys.exit(1)

    print(f'  Holder : {holder_dev.name} ({holder_dev.ip_address})')
    print(f'  NVR    : {nvr_dev.name} ({nvr_dev.ip_address})')
    print()

    check_startup_state(holder_dev, nvr_dev)

    print()
    print('Monitoring started. Press Ctrl+C to stop.')
    print()

    while True:
        holder_dev, nvr_dev = get_devices()
        if not holder_dev or not nvr_dev:
            print('ERROR: Devices removed from DB. Stopping.')
            break

        # Raw ping results
        raw_holder_up, holder_ms = ping(holder_dev.ip_address)
        raw_nvr_up,    nvr_ms    = ping(nvr_dev.ip_address)

        # Apply 2-ping confirmation — only act on confirmed state changes
        holder_up, nvr_up = apply_confirmation(raw_holder_up, raw_nvr_up)

        # Always save raw ping result to DB for live status display
        save_status(holder_dev, raw_holder_up, holder_ms)
        save_status(nvr_dev,    raw_nvr_up,    nvr_ms)

        handle_changes(holder_dev, nvr_dev, holder_up, nvr_up)
        periodic_checks(holder_dev, nvr_dev)

        time.sleep(PING_INTERVAL)


if __name__ == '__main__':
    run()
