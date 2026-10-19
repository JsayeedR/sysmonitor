"""Independent NVR availability critical alarm. OFF unless logs/nvr_critical_mode is enabled.

Does not change the existing outage-cycle / both-devices-down CRITICAL logic.
"""
import json
from html import escape
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent.parent
MODE_FILE = ROOT / 'logs' / 'nvr_critical_mode'
STATE_FILE = ROOT / 'logs' / 'nvr_critical_state.json'
THRESHOLD = timedelta(minutes=5)
REPEAT = timedelta(minutes=2)
PRIVATE_RECIPIENT_IDS = frozenset({33, 34})  # verified user entries; confirm contacts before enable


def _stamp(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value):
    return datetime.fromisoformat(value) if value else None


def read_mode():
    try:
        value = MODE_FILE.read_text(encoding='utf-8').strip().lower()
    except FileNotFoundError:
        return 'off'
    return value if value in ('private', 'production') else 'off'


def _load(path):
    try:
        obj = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(obj, dict):
            raise ValueError('Invalid NVR alarm state')
        return obj
    except FileNotFoundError:
        return {'down_since': None, 'first_alert_at': None, 'next_due': None}


def _save(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _format_duration(seconds):
    m, s = divmod(max(0, int(seconds)), 60)
    return f'{m}m {s:02d}s'


def format_nvr_message(kind, now, down_since, holder_up):
    """Return (Telegram/plain text, email subject, email HTML) without side effects."""
    if kind not in ('critical', 'recovery'):
        raise ValueError('Unknown NVR notification kind')
    elapsed = _format_duration((now - down_since).total_seconds())
    holder = 'UP' if holder_up is True else 'DOWN' if holder_up is False else 'UNKNOWN'
    started = down_since.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Dhaka')).strftime('%d %b %Y %I:%M:%S %p')
    urgent = holder_up is False
    if kind == 'critical':
        subject = f'🚨🚨 CRITICAL — COX CLS NVR DOWN {elapsed} — IMMEDIATE ACTION REQUIRED'
        headline = '🚨🚨 🆘 CRITICAL POWER CONTINUITY WARNING 🆘 🚨🚨'
        advisory = ('⚠️ BOTH NVR AND HOLDER/PDB UNREACHABLE — HIGH POWER CONTINUITY RISK'
                    if urgent else '⚠️ POSSIBLE NVR / POWER SUPPLY / NETWORK FAILURE')
        lines = [headline, '🔴 COX CLS NVR REMAINS OFFLINE', '', advisory,
                 '🔴 NVR: DOWN', f'{"🔴" if urgent else "🟢" if holder_up is True else "⚪"} Holder/PDB: {holder}',
                 f'⏱️ NVR DOWN FOR: {elapsed}', f'🕒 Started (BDT): {started}', '',
                 '🔋 CRITICAL TELECOM EQUIPMENT MAY BE RELYING ON BATTERY BACKUP.',
                 '⚡ Possible causes: power supply, generator, ATS/AVR, NVR or network fault.',
                 '🆘 URGENT: VERIFY THE PHYSICAL POWER SYSTEM IMMEDIATELY!',
                 '🔁 This alarm repeats every 2 minutes until NVR recovery.',
                 'ℹ️ NVR reachability alone does NOT confirm generator/ATS failure or battery-only operation.']
    else:
        subject = f'✅ RECOVERED — COX CLS NVR ONLINE — {elapsed} DOWNTIME'
        lines = ['✅✅ NVR CONNECTIVITY RESTORED ✅✅', '', '🟢 COX CLS NVR: ONLINE',
                 f'{"🔴" if urgent else "🟢" if holder_up is True else "⚪"} Holder/PDB: {holder}',
                 f'⏱️ Total NVR downtime: {elapsed}', f'🕒 Outage began (BDT): {started}',
                 '✅ Repeated NVR critical reminders stopped.',
                 '🔋 Verify mains/generator supply, UPS/battery status and equipment power.',
                 'ℹ️ Connectivity restored; physical power stability must still be verified.']
    text = '\n'.join(lines)
    color = '#b91c1c' if kind == 'critical' else '#15803d'
    html = ('<html><body style="font-family:Arial,sans-serif;background:#f8fafc;padding:22px">'
            '<div style="max-width:650px;margin:auto;background:white;padding:24px;border:2px solid '+color+';border-radius:10px">'
            '<h2 style="color:'+color+';margin-top:0">'+escape(lines[0])+'</h2>'
            '<p style="white-space:pre-line;line-height:1.7;color:#172b45">'+escape('\n'.join(lines[1:]))+'</p>'
            '</div></body></html>')
    return text, subject, html


def _deliver(kind, now, down_since, holder_up, mode):
    """Send independent NVR messages using existing critical preferences."""
    from monitor.models import NotificationGateway, NotificationRecipient
    from monitor.notifications import send_telegram, send_raw_email, log_notification
    text, subject, html = format_nvr_message(kind, now, down_since, holder_up)
    gateways = {g.channel: g for g in NotificationGateway.objects.filter(is_enabled=True)}
    qs = NotificationRecipient.objects.filter(is_active=True, alert_critical=True,
                                               channel__in=['telegram', 'email'])
    if mode == 'private':
        qs = qs.filter(pk__in=PRIVATE_RECIPIENT_IDS)
    elif mode != 'production':
        raise ValueError('Refusing notification delivery for disabled/unknown mode')
    for recipient in qs:
        if mode == 'private':
            expected = {33: ('email', 'jikrulrzs13eeekuet@gmail.com'), 34: ('telegram', '2114390365')}
            if (recipient.channel, recipient.contact) != expected.get(recipient.pk):
                raise RuntimeError('Private recipient destination mismatch; delivery blocked')
        gateway = gateways.get(recipient.channel)
        if not gateway:
            continue
        try:
            if recipient.channel == 'telegram':
                ok, err = send_telegram(gateway, recipient.contact, text)
            else:
                ok, err = send_raw_email(gateway, recipient.contact, subject, html, plain_body=text)
            log_notification(None, 'NVR_CRITICAL' if kind == 'critical' else 'NVR_RECOVERY',
                             recipient.channel, recipient.contact, 'SENT' if ok else 'FAILED', err)
            print(f'[NVR-ALARM] {kind} {recipient.channel} id={recipient.pk} sent={ok}')
        except Exception as exc:
            print(f'[NVR-ALARM] delivery failed ({recipient.channel} id={recipient.pk}): {exc}')


def tick(nvr_up, holder_up, *, now=None, state_path=None, mode=None, deliver=None):
    """One confirmed ping observation; deterministic/testable, including restart persistence."""
    mode = mode if mode is not None else read_mode()
    if mode not in ('private', 'production'):
        return 'disabled'
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('timezone-aware now required')
    path = Path(state_path) if state_path is not None else STATE_FILE
    state = _load(path)
    action = 'none'
    down_since = _parse(state.get('down_since'))
    if down_since is not None and down_since > now:
        raise ValueError('NVR alarm state timestamp is in the future; refusing to notify')
    send = deliver or _deliver
    if nvr_up is False:
        if down_since is None:
            down_since = now
            state = {'down_since': _stamp(now), 'first_alert_at': None, 'next_due': None}
            action = 'started'
        elif now - down_since >= THRESHOLD:
            due = _parse(state.get('next_due'))
            if due is None or now >= due:
                action = 'critical'
                if state.get('first_alert_at') is None:
                    state['first_alert_at'] = _stamp(now)
                # Persist the next delivery deadline BEFORE networking.
                state['next_due'] = _stamp(now + REPEAT)
                _save(path, state)
                send('critical', now, down_since, holder_up, mode)
                return action
    elif nvr_up is True:
        if down_since is not None and state.get('first_alert_at') is not None:
            # Clear persistent state BEFORE sending one recovery notification.
            _save(path, {'down_since': None, 'first_alert_at': None, 'next_due': None})
            send('recovery', now, down_since, holder_up, mode)
            return 'recovery'
        state = {'down_since': None, 'first_alert_at': None, 'next_due': None}
        action = 'cleared' if down_since else 'none'
    else:
        return 'unknown'  # Never infer NVR recovery from unknown monitor state.
    _save(path, state)
    return action
