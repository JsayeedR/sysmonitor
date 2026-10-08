"""
monitor/notifications.py
────────────────────────
Central notification dispatcher for SysMonitor.
Handles WhatsApp (Meta Cloud API), Telegram Bot, and Email (Gmail SMTP).

Anti-spam rules:
- OUTAGE_START  : sent once when outage begins (Holder goes DOWN)
- CRITICAL      : sent once when both devices down > 10 min
- ALARM         : sent once per alarm event per cycle
- COMPLETE      : sent once when cycle closes

No message is ever sent twice for the same cycle + event_type combo.
"""

import smtplib
import urllib.request
import urllib.parse
import urllib.error
import json
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import pytz

from .models import PageViewCounter

logger = logging.getLogger(__name__)
BDT    = pytz.timezone('Asia/Dhaka')

# In-memory guard: prevents duplicate notifications within same process lifetime.
# Solves race condition where DB log hasn't been written when next dispatch fires.
_dispatched = set()


def current_page_view_count():
    """Return the current page-view counter without incrementing it.

    Web page requests increment the counter through the context processor.
    Emails only read the current value and therefore do not count as page views.
    """
    counter, _ = PageViewCounter.objects.get_or_create(
        id=1,
        defaults={'count': 789},
    )
    return counter.count


def fmt_bdt(dt):
    if not dt:
        return '—'
    return dt.astimezone(BDT).strftime('%I:%M:%S %p')


def already_sent(cycle_id, event_type, channel, recipient):
    """
    Return True if this notification was already dispatched.
    Checks in-memory set FIRST (instant, no race) then DB for
    notifications sent in previous process lifetimes.
    """
    key = (cycle_id, event_type, channel, recipient)
    if key in _dispatched:
        return True
    from monitor.models import NotificationLog
    return NotificationLog.objects.filter(
        cycle_id=cycle_id,
        event_type=event_type,
        channel=channel,
        recipient=recipient,
        status='SENT',
    ).exists()


def _mark_dispatched(cycle_id, event_type, channel, recipient):
    """Mark as dispatched in memory BEFORE sending to prevent race condition."""
    _dispatched.add((cycle_id, event_type, channel, recipient))


def log_notification(cycle_id, event_type, channel, recipient, status, error=''):
    from monitor.models import NotificationLog
    NotificationLog.objects.create(
        cycle_id=cycle_id,
        event_type=event_type,
        channel=channel,
        recipient=recipient,
        status=status,
        error=error[:500],
    )


# Shown to the admin on the message-template editor page so they know which
# {placeholders} are available for each event type.
TEMPLATE_PLACEHOLDERS = {
    'OUTAGE_START':       ['time', 'location'],
    'CRITICAL':           ['elapsed', 'start'],
    'ALARM':              ['start', 'reason'],
    'COMPLETE':           ['date', 'start', 'end', 'duration', 'type', 'note'],
    'PAC_STATUS_CHANGE':  ['unit', 'old_state', 'new_state', 'time'],
    'SENSOR_ALERT':       ['device', 'temperature', 'humidity', 'threshold', 'time'],
    'TEST':               ['time'],
}


# ── Message builders ───────────────────────────────────────────────────────────

def _template_context(event_type, cycle=None, extra=None):
    """Builds the {placeholder} dict available to an admin-edited template
    for this event_type. Kept in sync with build_message()'s own wording."""
    now_str = datetime.now(BDT).strftime('%d/%m/%Y %I:%M:%S %p')
    ctx = {'time': now_str, 'now': now_str, 'location': 'NanoLab'}

    if cycle:
        ctx['start'] = fmt_bdt(cycle.outage_start)
        ctx['end']   = fmt_bdt(cycle.pdb_restored)
        ctx['date']  = cycle.outage_start.astimezone(BDT).strftime('%d/%m/%Y') if cycle.outage_start else '—'
        dur_min = (cycle.pdb_duration_sec or 0) // 60
        dur_sec = (cycle.pdb_duration_sec or 0) % 60
        ctx['duration'] = f"{dur_min}min {dur_sec}s" if dur_sec else f"{dur_min}min"
        ctx['type']   = cycle.cycle_type
        ctx['reason'] = getattr(cycle, 'alarm_reason', '') or '—'
        ctx['note']   = ctx['reason']

    if event_type == 'CRITICAL':
        ctx['elapsed'] = extra or '10+ min'
    elif event_type == 'ALARM':
        ctx['reason'] = extra or ctx.get('reason', 'Unknown')
    elif event_type == 'PAC_STATUS_CHANGE' and extra:
        ctx['unit'] = extra.get('unit', 'PAC unit')
        ctx['old_state'] = extra.get('old', '—')
        ctx['new_state'] = extra.get('new', '—')
    elif event_type == 'SENSOR_ALERT' and extra:
        ctx.update({k: extra.get(k, '—') for k in
                    ('device', 'temperature', 'humidity', 'threshold')})
    return ctx


class _SafeDict(dict):
    """dict subclass so {unused_placeholder} in a hand-edited template
    doesn't raise KeyError — it just renders literally instead of crashing
    the whole notification pipeline over a typo."""
    def __missing__(self, key):
        return '{' + key + '}'


def _rendered_override(event_type, cycle=None, extra=None):
    """Returns the admin's custom template text for this event_type,
    rendered with the current context — or None if there's no override
    (falls back to the hardcoded default in build_message)."""
    try:
        from monitor.models import MessageTemplate
        tpl = MessageTemplate.objects.filter(event_type=event_type).first()
        if not tpl or not tpl.template_text.strip():
            return None
        ctx = _template_context(event_type, cycle=cycle, extra=extra)
        return tpl.template_text.format_map(_SafeDict(ctx))
    except Exception:
        # DB not migrated yet, or a bad format string — fail safe to default
        # wording rather than losing the notification entirely.
        logger.exception(f"Template override failed for {event_type}, using default")
        return None


def build_message(event_type, cycle=None, extra=None, ignore_override=False):
    """Build a human-friendly message for each event type."""
    override = None if ignore_override else _rendered_override(event_type, cycle=cycle, extra=extra)
    if override is not None:
        return override

    now_str = datetime.now(BDT).strftime('%d/%m/%Y %I:%M:%S %p')

    if event_type == 'OUTAGE_START':
        return (
            f"⚡ *POWER OUTAGE STARTED*\n"
            f"🕐 Time: {fmt_bdt(cycle.outage_start) if cycle else now_str}\n"
            f"📍 Location: NanoLab\n"
            f"🔴 PDB (Grid) power lost — Generator starting..."
        )

    elif event_type == 'CRITICAL':
        elapsed = extra or '10+ min'
        return (
            f"🚨 *CRITICAL ALERT*\n"
            f"Both devices DOWN for {elapsed}\n"
            f"🕐 Outage started: {fmt_bdt(cycle.outage_start) if cycle else '—'}\n"
            f"⚠️ Generator may not have started. Immediate attention required!"
        )

    elif event_type == 'ALARM':
        reason = extra or cycle.alarm_reason if cycle else 'Unknown'
        return (
            f"🟠 *ALARM — ABNORMAL CONDITION*\n"
            f"🕐 Outage started: {fmt_bdt(cycle.outage_start) if cycle else '—'}\n"
            f"⚠️ Reason: {reason}\n"
            f"Please check the generator / AVR immediately."
        )

    elif event_type == 'COMPLETE':
        start   = fmt_bdt(cycle.outage_start) if cycle else '—'
        end     = fmt_bdt(cycle.pdb_restored)  if cycle else '—'
        dur_min = cycle.pdb_duration_sec // 60 if cycle else 0
        dur_sec = cycle.pdb_duration_sec % 60  if cycle else 0
        dur_str = f"{dur_min}min {dur_sec}s" if dur_sec else f"{dur_min}min"
        ctype   = cycle.cycle_type if cycle else '—'
        icon    = '✅' if ctype == 'NORMAL' else ('🟠' if ctype == 'ALARM' else '🔴')
        note_line = f"\n📝 Note: {cycle.alarm_reason}" if cycle and getattr(cycle, 'alarm_reason', '') else ""
        return (
            f"{icon} *OUTAGE CYCLE COMPLETE*\n"
            f"📅 Date: {cycle.outage_start.astimezone(BDT).strftime('%d/%m/%Y') if cycle else '—'}\n"
            f"🕐 Start:    {start}\n"
            f"🕑 Restored: {end}\n"
            f"⏱️ Duration: {dur_str}\n"
            f"📋 Type: {ctype}"
            f"{note_line}"
        )

    elif event_type == 'PAC_STATUS_CHANGE':
        # extra is a dict: {'unit': 'Unit 1', 'old': 'ON', 'new': 'STANDBY'}
        icons = {'ON': '🟢', 'STANDBY': '🟡', 'OFF': '🔴'}
        unit = extra.get('unit', 'PAC unit') if extra else 'PAC unit'
        old_state = extra.get('old', '—') if extra else '—'
        new_state = extra.get('new', '—') if extra else '—'
        icon = icons.get(new_state, 'ℹ️')
        return (
            f"{icon} *SMW6PAC STATUS CHANGE*\n"
            f"❄️ {unit}\n"
            f"🔄 {old_state} → {new_state}\n"
            f"🕐 Time: {now_str}"
        )
    elif event_type == 'DAILY_SUMMARY':
        # extra is the pre-formatted report text from format_summary_text()
        return extra or 'Daily summary unavailable.'

    elif event_type == 'COLOCATION_DATA':
        # extra is the pre-formatted current colocation sensor report.
        return extra or 'Colocation data unavailable.'

    elif event_type == 'TEST':
        return (
            f"✅ *SysMonitor Test Message*\n"
            f"🕐 Sent at: {now_str}\n"
            f"This is a test notification from SysMonitor.\n"
            f"Your notification setup is working correctly!"
        )

    return f"SysMonitor notification — {event_type}"


# ── WhatsApp Gateway (Meta Cloud API) ─────────────────────────────────────────

def translate_wa_error(err_str):
    """
    Converts raw Meta API error strings into plain-English, actionable
    messages so the admin doesn't need to look up error codes manually.
    Covers every error we've encountered so far, plus common related ones.
    """
    e = err_str

    # [190] Invalid / expired access token
    if '[190]' in e or 'OAuthException' in e:
        if 'expired' in e.lower() or 'session has expired' in e.lower():
            return ('🔴 Access token has EXPIRED. Generate a new token in Meta '
                     'dashboard (or better — set up a permanent System User token '
                     'so this never happens again). Go to Notifications → WhatsApp '
                     'to update it.')
        return ('🔴 Access token is invalid. It may have been revoked, regenerated '
                 'elsewhere, or never saved correctly. Re-check the token in '
                 'Notifications → WhatsApp.')

    # [131047] Outside 24-hour customer service window
    if '131047' in e:
        return ('🟠 Message blocked — outside the 24-hour reply window. Free-text '
                 'alerts only work if the recipient messaged your WhatsApp number '
                 'within the last 24 hours. Ask them to send "hi" to your WhatsApp '
                 'number to re-open the window, or set up an approved message '
                 'template to bypass this limit entirely.')

    # [131030] Recipient not in allowed list (test number restriction)
    if '131030' in e:
        return ('🟠 This number is not on your test number\'s approved recipient '
                 'list. Go to Meta Dashboard → WhatsApp → API Setup → "To" field → '
                 'add this number and verify it with the code Meta sends. This '
                 'restriction disappears once you switch to a verified production '
                 'WhatsApp Business number.')

    # [100] Invalid parameter (often bad phone number format)
    if '[100]' in e:
        return ('🟠 Invalid request — usually means the phone number format is '
                 'wrong. Use the full international format with country code, '
                 'no spaces or dashes, e.g. 8801711234567 (no leading +).')

    # [131026] Message undeliverable (recipient has no WhatsApp / blocked number)
    if '131026' in e:
        return ('🟠 Message undeliverable. The recipient may not have WhatsApp '
                 'installed, has blocked your business number, or the number is '
                 'incorrect.')

    # [131056] Rate limit / pair rate limit hit
    if '131056' in e or '80007' in e:
        return ('🟠 Rate limit reached — too many messages sent too quickly. '
                 'Wait a few minutes before sending more.')

    # [10] Permission error (missing scopes on token)
    if '[10]' in e and 'Permission' in e:
        return ('🔴 Token is missing required permissions. It needs both '
                 '"whatsapp_business_messaging" and "whatsapp_business_management" '
                 'scopes. Regenerate the System User token with both checked.')

    # [33] Phone number ID not found / not accessible by this token
    if '[33]' in e:
        return ('🔴 Phone Number ID not found or not accessible with this token. '
                 'Double check the Phone Number ID in Notifications → WhatsApp '
                 'matches the one shown in Meta Dashboard → API Setup.')

    # Fallback — show the raw error so nothing is hidden
    return f'⚠️ {e}'


def _wa_post(gateway, payload):
    """Low-level POST to Meta Graph API. Returns (ok, response_dict_or_error_str)."""
    url = f"https://graph.facebook.com/v19.0/{gateway.wa_phone_number_id}/messages"
    headers = {
        'Authorization': f'Bearer {gateway.wa_access_token}',
        'Content-Type': 'application/json',
    }
    data = json.dumps(payload).encode('utf-8')
    req  = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body   = resp.read().decode('utf-8')
            result = json.loads(body)
            if 'messages' in result:
                return True, result
            return False, str(result)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8')
        try:
            err_json = json.loads(err_body)
            err_msg  = err_json.get('error', {}).get('message', err_body)
            err_code = err_json.get('error', {}).get('code', '')
            return False, f"[{err_code}] {err_msg}"
        except Exception:
            return False, f"HTTP {e.code}: {err_body}"
    except Exception as e:
        return False, str(e)


def send_whatsapp(gateway, recipient_contact, message):
    """
    Send a WhatsApp message via Meta Cloud API.

    Free-form text messages ONLY work if the recipient messaged your
    number within the last 24 hours (Meta's "customer service window").
    Outside that window, free text is REJECTED — only pre-approved
    template messages can be sent (e.g. 'hello_world').

    This function tries free text first. If Meta rejects it for being
    outside the 24h window, it automatically falls back to the
    hello_world template so the recipient still gets notified that
    something happened. All errors are translated to plain English.
    """
    to = recipient_contact.replace('+', '').replace(' ', '').replace('-', '')

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    ok, result = _wa_post(gateway, payload)
    if ok:
        return True, ''

    err_str = str(result)

    # Detect "outside 24h window" — fall back to template
    if '131047' in err_str:
        template_payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": "hello_world",
                "language": {"code": "en_US"}
            }
        }
        t_ok, t_result = _wa_post(gateway, template_payload)
        if t_ok:
            return False, (
                translate_wa_error(err_str) +
                " (Sent a fallback 'hello_world' template instead, so the "
                "recipient was at least pinged.)"
            )
        else:
            return False, (
                translate_wa_error(err_str) +
                f" Template fallback also failed: {translate_wa_error(t_result)}"
            )

    return False, translate_wa_error(err_str)


# ── WhatsApp Token Health Check ───────────────────────────────────────────────

def check_whatsapp_token_health(gateway):
    """
    Checks the WhatsApp access token's validity and expiration.
    Returns a dict: {
        'ok': bool,
        'status': 'PERMANENT' | 'TEMPORARY' | 'EXPIRING_SOON' | 'EXPIRED' | 'INVALID' | 'NOT_CONFIGURED' | 'ERROR',
        'message': str,
        'expires_at': datetime or None,
    }
    """
    from datetime import datetime

    if not gateway or not gateway.wa_access_token:
        return {'ok': False, 'status': 'NOT_CONFIGURED',
                'message': 'WhatsApp access token not set.', 'expires_at': None}

    url = (f"https://graph.facebook.com/v19.0/debug_token"
           f"?input_token={gateway.wa_access_token}"
           f"&access_token={gateway.wa_access_token}")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))['data']

        if not data.get('is_valid'):
            return {'ok': False, 'status': 'INVALID',
                    'message': 'Access token is invalid or has been revoked.',
                    'expires_at': None}

        exp = data.get('expires_at', 0)
        if exp == 0:
            return {'ok': True, 'status': 'PERMANENT',
                    'message': 'Token is permanent (System User token).',
                    'expires_at': None}

        expires_dt = datetime.fromtimestamp(exp)
        now = datetime.now()
        if expires_dt <= now:
            return {'ok': False, 'status': 'EXPIRED',
                    'message': f'Token expired on {expires_dt.strftime("%d %b %Y %I:%M:%S %p")}.',
                    'expires_at': expires_dt}

        hours_left = (expires_dt - now).total_seconds() / 3600
        if hours_left <= 6:
            return {'ok': True, 'status': 'EXPIRING_SOON',
                    'message': f'Token expires in {hours_left:.1f} hours '
                               f'({expires_dt.strftime("%d %b %Y %I:%M:%S %p")}). '
                               f'Generate a permanent System User token to avoid alert failures.',
                    'expires_at': expires_dt}

        return {'ok': True, 'status': 'TEMPORARY',
                'message': f'Token valid until {expires_dt.strftime("%d %b %Y %I:%M:%S %p")} '
                           f'({hours_left:.0f}h left). Consider switching to a permanent token.',
                'expires_at': expires_dt}

    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode('utf-8'))
            msg = err.get('error', {}).get('message', 'Unknown error')
        except Exception:
            msg = str(e)
        return {'ok': False, 'status': 'INVALID',
                'message': f'Token check failed: {msg}', 'expires_at': None}
    except Exception as e:
        return {'ok': False, 'status': 'ERROR',
                'message': f'Could not verify token: {e}', 'expires_at': None}


# ── Telegram Gateway ───────────────────────────────────────────────────────────

def send_telegram(gateway, chat_id, message, button_text=None, button_url=None):
    """Send a Telegram message via Bot API. Optionally attach a single
    inline URL button (button_text + button_url)."""
    url  = f"https://api.telegram.org/bot{gateway.tg_bot_token}/sendMessage"
    payload = {
        'chat_id':    chat_id,
        'text':       message,
        'parse_mode': 'Markdown',
    }
    if button_text and button_url:
        payload['reply_markup'] = {
            'inline_keyboard': [[{'text': button_text, 'url': button_url}]]
        }
    data = json.dumps(payload).encode('utf-8')
    req  = urllib.request.Request(url, data=data,
                                   headers={'Content-Type': 'application/json'},
                                   method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body   = resp.read().decode('utf-8')
            result = json.loads(body)
            if result.get('ok'):
                return True, ''
            return False, result.get('description', 'Unknown error')
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8')}"
    except Exception as e:
        return False, str(e)


def get_telegram_chat_id(bot_token):
    """Helper: fetch recent updates to find chat IDs."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            chats = []
            for update in result.get('result', []):
                msg = update.get('message', {})
                chat = msg.get('chat', {})
                if chat:
                    chats.append({
                        'chat_id': str(chat.get('id', '')),
                        'name':    chat.get('first_name', '') + ' ' + chat.get('last_name', ''),
                        'type':    chat.get('type', ''),
                    })
            # deduplicate
            seen = set()
            unique = []
            for c in chats:
                if c['chat_id'] not in seen:
                    seen.add(c['chat_id'])
                    unique.append(c)
            return True, unique
    except Exception as e:
        return False, str(e)




def get_telegram_bot_identity(bot_token):
    """Return the configured bot's public username without sending anything."""
    if not bot_token:
        return False, 'Telegram bot is not configured.'

    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))

        if not result.get('ok'):
            return False, result.get('description', 'Telegram getMe failed')

        bot = result.get('result') or {}
        return True, {
            'username': bot.get('username', ''),
            'name': bot.get('first_name', 'SysMonitor Bot'),
        }

    except Exception as exc:
        return False, str(exc)


def find_telegram_pairing_message(bot_token, pairing_code, not_before=None):
    """
    Find an exact private Telegram message:

        hi PAIRINGCODE

    Only messages sent at/after not_before are accepted when supplied.
    This is read-only. It never sends a Telegram message.
    """
    if not bot_token or not pairing_code:
        return False, 'Telegram bot or pairing code is missing.'

    expected = f"hi {pairing_code}".strip().casefold()

    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))

        if not result.get('ok'):
            return False, result.get('description', 'Telegram getUpdates failed')

        # Newest first, so an old matching message is less likely to win.
        for update in reversed(result.get('result', [])):
            msg = update.get('message') or {}
            text_value = str(msg.get('text') or '').strip()

            if text_value.casefold() != expected:
                continue

            if not_before is not None:
                try:
                    message_ts = int(msg.get('date') or 0)
                    minimum_ts = int(not_before.timestamp())
                except (TypeError, ValueError, AttributeError):
                    continue

                if message_ts < minimum_ts:
                    continue

            chat = msg.get('chat') or {}

            # Personal notification pairing must be a private Telegram chat.
            if chat.get('type') != 'private':
                continue

            chat_id = str(chat.get('id') or '').strip()
            if not chat_id:
                continue

            name = ' '.join(
                part for part in (
                    chat.get('first_name', ''),
                    chat.get('last_name', ''),
                )
                if part
            ).strip()

            username = str(chat.get('username') or '').strip()
            if username:
                display = f"{name} (@{username})" if name else f"@{username}"
            else:
                display = name or 'Telegram User'

            return True, {
                'chat_id': chat_id,
                'display_name': display,
            }

        return False, (
            f'No private Telegram message matching "hi {pairing_code}" '
            f'was found yet.'
        )

    except Exception as exc:
        return False, str(exc)


# ── Email Gateway ──────────────────────────────────────────────────────────────

def send_email(gateway, recipient_email, message, event_type='NOTIFICATION'):
    """Send an email via SMTP."""
    subject_map = {
        'OUTAGE_START':  '⚡ Power Outage Started — NanoLab',
        'CRITICAL':      '🚨 CRITICAL: Both Devices Down — NanoLab',
        'ALARM':         '🟠 Alarm: Abnormal Condition — NanoLab',
        'COMPLETE':      '✅ Outage Cycle Complete — NanoLab',
        'DAILY_SUMMARY': f'📋 Daily Generator Summary — {datetime.now(BDT).strftime("%d/%m/%Y")} — NanoLab',
        'COLOCATION_DATA': '🌡️ Colocation Sensor Data — NanoLab',
        'TEST':          '✅ SysMonitor Test Message',
    }
    subject = subject_map.get(event_type, 'SysMonitor Notification')

    # Plain text version (strip markdown)
    plain = message.replace('*', '')

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = gateway.email_from or gateway.email_username
    msg['To']      = recipient_email

    # HTML version — daily summary uses a monospace <pre> block to keep
    # the table-style alignment intact; other alerts use normal prose
    # Light theme — email clients handle dark backgrounds unreliably
    # (many strip them, some double-apply their own dark mode on top of
    # an already-dark template), so unlike the app's own dark UI, email
    # uses a light background with dark text for predictable readability
    # across Gmail/Outlook/Apple Mail/etc.
    if event_type == 'DAILY_SUMMARY':
        body_html = f'<pre style="font-family:monospace;font-size:13px;line-height:1.5;white-space:pre-wrap;color:#1e293b;">{plain}</pre>'
    else:
        body_html = f'<p style="line-height:1.7;font-size:15px;color:#1e293b;">{plain.replace(chr(10), "<br>")}</p>'

    page_view_count = current_page_view_count()

    html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#f1f5f9;color:#1e293b;padding:20px;">
    <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:24px;border:1px solid #e2e8f0;">
    <h3 style="color:#1d4ed8;margin-bottom:16px;">SysMonitor Alert</h3>
    {body_html}
    <hr style="border-color:#e2e8f0;margin:20px 0;">
    <p style="color:#64748b;font-size:12px;"><a href="http://163.47.80.62:9696/about/" style="color:#1d4ed8;text-decoration:none;">ℹ️ About / Docs</a> · Developed by Jikrul Sayeed · © 2026 COXCLS NOC, BSCPLC. All rights reserved. #{page_view_count:05d}</p>
    </div></body></html>
    """

    msg.attach(MIMEText(plain, 'plain'))
    msg.attach(MIMEText(html,  'html'))

    try:
        server = smtplib.SMTP(gateway.email_host, gateway.email_port, timeout=10)
        server.starttls()
        server.login(gateway.email_username, gateway.email_password)
        server.sendmail(gateway.email_from or gateway.email_username,
                        recipient_email, msg.as_string())
        server.quit()
        return True, ''
    except Exception as e:
        return False, str(e)


def send_raw_email(gateway, recipient_email, subject, html_body, plain_body=None, attachment=None):
    """
    Low-level SMTP send for system emails that don't fit the alert
    event_type flow (password resets, monthly reports). attachment, if
    given, is a tuple of (filename, bytes, mime_subtype) e.g.
    ('loadshedding_2026-09.xlsx', b'...', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet').
    """
    from email.mime.base import MIMEBase
    from email import encoders

    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['From']    = gateway.email_from or gateway.email_username
    msg['To']      = recipient_email

    body = MIMEMultipart('alternative')
    body.attach(MIMEText(plain_body or html_body, 'plain'))
    body.attach(MIMEText(html_body, 'html'))
    msg.attach(body)

    if attachment:
        filename, data, subtype = attachment
        part = MIMEBase('application', subtype)
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
        msg.attach(part)

    try:
        server = smtplib.SMTP(gateway.email_host, gateway.email_port, timeout=15)
        server.starttls()
        server.login(gateway.email_username, gateway.email_password)
        server.sendmail(gateway.email_from or gateway.email_username,
                        recipient_email, msg.as_string())
        server.quit()
        return True, ''
    except Exception as e:
        return False, str(e)


def send_password_reset_email(gateway, recipient_email, username, temp_password, login_url):
    """Sends the temporary password + login link. Valid 30 minutes."""
    subject = '🔑 SysMonitor Password Reset'
    page_view_count = current_page_view_count()
    html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#f1f5f9;color:#1e293b;padding:20px;">
    <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:24px;border:1px solid #e2e8f0;">
    <h3 style="color:#1d4ed8;margin-bottom:16px;">Password Reset — SysMonitor</h3>
    <p style="line-height:1.7;font-size:15px;">Hi {username},</p>
    <p style="line-height:1.7;font-size:15px;">A password reset was requested for your account. Use this temporary password to log in:</p>
    <p style="font-family:monospace;font-size:20px;background:#f1f5f9;border-radius:8px;padding:12px 16px;letter-spacing:1px;">{temp_password}</p>
    <p style="line-height:1.7;font-size:14px;color:#b91c1c;">⏱️ This temporary password is valid for <b>30 minutes</b> only. After logging in you'll be required to set a new password immediately.</p>
    <p style="margin:20px 0;"><a href="{login_url}" style="background:#1d4ed8;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-size:14px;">Go to Login</a></p>
    <p style="line-height:1.6;font-size:12px;color:#64748b;">If you didn't request this, contact your administrator — someone else may have entered your username.</p>
    <hr style="border-color:#e2e8f0;margin:20px 0;">
    <p style="color:#64748b;font-size:12px;"><a href="http://163.47.80.62:9696/about/" style="color:#1d4ed8;text-decoration:none;">ℹ️ About / Docs</a> · Developed by Jikrul Sayeed · © 2026 COXCLS NOC, BSCPLC. All rights reserved. #{page_view_count:05d}</p>
    </div></body></html>
    """
    plain = (f"Hi {username},\n\nA password reset was requested for your SysMonitor account.\n\n"
             f"Temporary password: {temp_password}\n\n"
             f"This is valid for 30 minutes only. Log in at {login_url} and you'll be "
             f"asked to set a new password immediately.\n\n"
             f"If you didn't request this, contact your administrator.")
    return send_raw_email(gateway, recipient_email, subject, html, plain)


# ── Main dispatcher ────────────────────────────────────────────────────────────

def dispatch(event_type, cycle=None, extra=None, force=False):
    """
    Main entry point. Called from ping_monitor.py.
    event_type: OUTAGE_START | CRITICAL | ALARM | COMPLETE
    cycle: OutageCycle instance (or None for non-cycle events)
    extra: optional string (elapsed time for CRITICAL, reason for ALARM)
    force: skip duplicate check (used for TEST only)
    """
    from monitor.models import NotificationGateway, NotificationRecipient

    # Map event_type → recipient alert field
    alert_field = {
        'OUTAGE_START':   'alert_outage',
        'CRITICAL':       'alert_critical',
        'ALARM':          'alert_alarm',
        'COMPLETE':       'alert_complete',
        'DAILY_SUMMARY':  'daily_summary',
        'PAC_STATUS_CHANGE': 'alert_pac_status',
        'COLOCATION_DATA': 'colocation_data',
    }.get(event_type)

    if not alert_field and not force:
        return

    try:
        recipients = NotificationRecipient.objects.filter(is_active=True)
        if alert_field:
            recipients = recipients.filter(**{alert_field: True})

        gateways = {gw.channel: gw for gw in
                    NotificationGateway.objects.filter(is_enabled=True)}

        message = build_message(event_type, cycle=cycle, extra=extra)

        # DAILY_SUMMARY has no cycle — use a synthetic id based on today's
        # date so the anti-duplicate check still works (one summary/day/recipient)
        if cycle:
            cycle_id = cycle.id
        elif event_type == 'DAILY_SUMMARY':
            cycle_id = -int(datetime.now(BDT).strftime('%Y%m%d'))  # negative = synthetic
        else:
            cycle_id = None

        for r in recipients:
            gw = gateways.get(r.channel)
            if not gw:
                continue  # gateway not configured/enabled

            # Anti-duplicate check — in-memory first, then DB
            if not force and cycle_id:
                if already_sent(cycle_id, event_type, r.channel, r.contact):
                    continue
                # Mark IMMEDIATELY before sending — prevents race condition
                # where next dispatch() call passes duplicate check while
                # this one is still sending (network calls take ~1-2s each)
                _mark_dispatched(cycle_id, event_type, r.channel, r.contact)

            # Send
            if r.channel == 'whatsapp':
                ok, err = send_whatsapp(gw, r.contact, message)
            elif r.channel == 'telegram':
                ok, err = send_telegram(gw, r.contact, message)
            elif r.channel == 'email':
                ok, err = send_email(gw, r.contact, message, event_type)
            else:
                continue

            status = 'SENT' if ok else 'FAILED'
            log_notification(cycle_id, event_type, r.channel, r.contact, status, err)

            if not ok:
                logger.error(f"Notification failed [{r.channel}→{r.contact}]: {err}")

    except Exception as e:
        logger.error(f"dispatch() error: {e}")


def send_test(channel, contact, gateway):
    """Send a test message to a single recipient. Returns (ok, error)."""
    message = build_message('TEST')
    if channel == 'whatsapp':
        return send_whatsapp(gateway, contact, message)
    elif channel == 'telegram':
        return send_telegram(gateway, contact, message)
    elif channel == 'email':
        return send_email(gateway, contact, message, 'TEST')
    return False, 'Unknown channel'
