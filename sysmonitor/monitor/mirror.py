"""
monitor/mirror.py — master ⇄ remote glue.

Two roles (settings.SYSMONITOR_ROLE):

  MASTER ("local", where the services run and SQLite is written)
      MirrorInboundMiddleware   accepts requests forwarded by the remote server.
      They are signed with MIRROR_SHARED_SECRET and may only arrive from
      127.0.0.1 (the SSH tunnel). The master then behaves as if the user had
      clicked on the master site: same views, same database, same logs.
      After a saved change it pushes the fresh data to the remote at once.

  REMOTE (app.bsccl.com/sysmonitor — a copy of the master's data)
      MirrorForwardMiddleware   every change (POST/PUT/DELETE…) and the few
      pages that need the master machine (system tools, Uptime Kuma, PAC) are
      passed to the master; its answer is handed back to the user. Reading
      pages, login and logout are handled on the remote itself.
      If the master cannot be reached the user gets a clear message.

Also here: mirror_context (template variables: base_path, banner).
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages as dj_messages
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse, JsonResponse

log = logging.getLogger('monitor.mirror')

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')
MAX_SKEW_SECONDS = 120          # signed request must be at most 2 minutes old
NONCE_TTL_SECONDS = 600         # a used request ID is remembered this long (> 2 × skew)

# Handled on the remote itself (login uses the copied password hashes).
LOCAL_POST_PATHS = ('/login/', '/logout/')
# GET pages that need the master machine (live services / local hardware).
PASS_THROUGH_GET_PREFIXES = (
    '/system/', '/uptime/', '/smw6pac/',
    '/generator-cycle-audit/data/',
    '/notifications/whatsapp-health/', '/notifications/gateway/telegram-chats/',
)
# Not available on the remote at all (Django admin → use the master).
BLOCKED_PREFIXES = ('/admin/',)

ACTIVITY_PATH = '/mirror/activity/'
TRACK_PATH = '/mirror/track/'
PASSWORD_SAVE_PATH = '/profile/password/save/'


# ─── signing ──────────────────────────────────────────────────────────────────

def _secret():
    return (getattr(settings, 'MIRROR_SHARED_SECRET', '') or '').encode()


def sign(ts, nonce, method, path, query, username, body, prefix):
    msg = '\n'.join([str(ts), nonce, method.upper(), path, query, username,
                     hashlib.sha256(body).hexdigest(), prefix])
    return hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()


def _wants_json(request):
    return ('application/json' in request.headers.get('Accept', '')
            or bool(request.headers.get('X-Requested-With'))
            or request.headers.get('Content-Type', '').startswith('application/json'))


def _error(request, status, title, text):
    if _wants_json(request):
        return JsonResponse({'ok': False, 'success': False, 'error': text, 'message': text},
                            status=status)
    return HttpResponse(
        '<!doctype html><meta charset="utf-8"><title>%s</title>'
        '<body style="font-family:sans-serif;background:#0f172a;color:#e2e8f0;'
        'display:grid;place-items:center;height:100vh;margin:0">'
        '<div style="text-align:center;max-width:32rem;padding:1rem"><h2>%s</h2><p>%s</p>'
        '<p><a style="color:#60a5fa" href="javascript:history.back()">← Go back</a></p></div>'
        % (title, title, text), status=status)


# ═════════════════════════════════════════════════════════════════════════════
# MASTER side
# ═════════════════════════════════════════════════════════════════════════════

def _nonce_dir():
    d = Path(settings.BASE_DIR) / '.mirror_nonces'
    d.mkdir(exist_ok=True)
    return d


def _claim_nonce(nonce):
    """Remember a request ID. Returns False if it was already used (= replay).
    One empty file per ID, created atomically (O_EXCL) — safe across several
    web processes. Old IDs are deleted."""
    if not nonce or len(nonce) > 64 or not nonce.isalnum():
        return False
    d = _nonce_dir()
    now = time.time()
    try:
        for e in os.scandir(d):                     # purge expired IDs
            if now - e.stat().st_mtime > NONCE_TTL_SECONDS:
                try:
                    os.unlink(e.path)
                except OSError:
                    pass
        os.close(os.open(d / nonce, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except FileExistsError:
        return False


def _push_now():
    try:
        from monitor import mirror_push
        mirror_push.live_push()
    except Exception:
        log.exception('push after forwarded write failed')


class MirrorInboundMiddleware:
    """Accept signed requests forwarded from the remote (see module doc)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if 'HTTP_X_MIRROR_SIG' not in request.META:
            return self.get_response(request)

        if request.META.get('REMOTE_ADDR') not in ('127.0.0.1', '::1'):
            return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
        try:
            ts = int(request.META.get('HTTP_X_MIRROR_TS', '0'))
        except ValueError:
            ts = 0
        username = request.META.get('HTTP_X_MIRROR_USER', '')
        prefix = request.META.get('HTTP_X_MIRROR_PREFIX', '')
        nonce = request.META.get('HTTP_X_MIRROR_NONCE', '')
        try:
            body = request.body
        except Exception:
            return JsonResponse({'ok': False, 'error': 'request too large'}, status=413)
        expected = sign(ts, nonce, request.method, request.path_info,
                        request.META.get('QUERY_STRING', ''), username, body, prefix)
        if (not _secret() or abs(time.time() - ts) > MAX_SKEW_SECONDS
                or not hmac.compare_digest(expected, request.META['HTTP_X_MIRROR_SIG'])):
            log.warning('rejected forwarded request: bad signature (%s)', request.path_info)
            return JsonResponse({'ok': False, 'error': 'bad signature'}, status=403)
        # Signature is valid → now make sure this exact request was not used before.
        if not _claim_nonce(nonce):
            log.warning('rejected forwarded request: repeated request ID (%s)', request.path_info)
            return JsonResponse({'ok': False, 'error': 'repeated request'}, status=409)

        # Trusted: act as the user who clicked on the remote site.
        user = User.objects.filter(username=username, is_active=True).first() if username else None
        request.user = user or AnonymousUser()
        request._dont_enforce_csrf_checks = True      # the remote already checked CSRF
        request._mirror_forwarded = True
        request._mirror_prefix = prefix

        response = self.get_response(request)

        # Hand flash messages ("Device added") to the remote in a header;
        # drop cookies (the master's session/CSRF cookies mean nothing there).
        queued = getattr(getattr(request, '_messages', None), '_queued_messages', [])
        if queued:
            payload = [[m.level, str(m.message), m.extra_tags or ''] for m in queued]
            response['X-Mirror-Messages'] = base64.b64encode(
                json.dumps(payload).encode()).decode()
        response.cookies.clear()

        # Make the change visible on the remote immediately, not in 30 s.
        if (request.method not in SAFE_METHODS and request.path_info not in (ACTIVITY_PATH, TRACK_PATH)
                and response.status_code < 500):
            # In the background, so the user's Save returns at once.
            threading.Thread(target=_push_now, daemon=True).start()
        return response


# ═════════════════════════════════════════════════════════════════════════════
# REMOTE side
# ═════════════════════════════════════════════════════════════════════════════

def _master_request(method, path, query, username, body, prefix, headers, timeout):
    import requests
    ts = int(time.time())
    nonce = uuid.uuid4().hex                    # unique ID for this one request
    h = dict(headers)
    h.update({
        'X-Mirror-Ts': str(ts), 'X-Mirror-Nonce': nonce,
        'X-Mirror-User': username, 'X-Mirror-Prefix': prefix,
        'X-Mirror-Sig': sign(ts, nonce, method, path, query, username, body, prefix),
    })
    base = getattr(settings, 'MIRROR_MASTER_URL', 'http://127.0.0.1:18000').rstrip('/')
    url = base + path + (('?' + query) if query else '')
    return requests.request(method, url, data=body, headers=h, timeout=timeout,
                            allow_redirects=False)


class MirrorForwardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    # Cache the body early so CSRF checking and forwarding can both use it.
    def __call__(self, request):
        if request.method not in SAFE_METHODS:
            try:
                request.body
            except Exception:
                return _error(request, 413, 'Too large', 'That upload is too large.')
        path = request.path_info
        if path == '/logout/' and request.method == 'POST' and request.user.is_authenticated:
            self._notify(request, 'LOGOUT', request.user.username,
                         f'User "{request.user.username}" logged out.')
        response = self.get_response(request)
        # Count this page view (and the user's activity time) on the MASTER —
        # the remote's database copy can't keep anything. Pages that were
        # passed through to the master are already counted there.
        if (request.method == 'GET' and response.status_code == 200
                and not getattr(request, '_mirror_was_forwarded', False)
                and 'text/html' in response.get('Content-Type', '')
                and not path.startswith(('/static/', '/media/'))):
            self._track(request)
        if path == '/login/' and request.method == 'POST':
            uname = request.POST.get('username', '')
            if response.status_code == 302 and request.user.is_authenticated:
                self._notify(request, 'LOGIN', uname, f'User "{uname}" logged in.')
            elif response.status_code == 200:
                self._notify(request, 'LOGIN_FAILED', uname, f'Failed login attempt for "{uname}".')
        return response

    def process_view(self, request, view_func, args, kwargs):
        path = request.path_info
        if path.startswith(BLOCKED_PREFIXES):
            return _error(request, 403, '🔒 Not available here',
                          'Django admin is only available on the master server.')
        if request.method not in SAFE_METHODS:
            if path in LOCAL_POST_PATHS:
                return None
        elif not path.startswith(PASS_THROUGH_GET_PREFIXES):
            return None
        return self._forward(request)

    # ── helpers ──
    def _forward(self, request):
        request._mirror_was_forwarded = True
        user = request.user.username if request.user.is_authenticated else ''
        prefix = request.META.get('SCRIPT_NAME', '').rstrip('/')
        headers = {}
        for k in ('Content-Type', 'Accept', 'X-Requested-With', 'Accept-Language'):
            if request.headers.get(k):
                headers[k] = request.headers[k]
        # So the master's activity log shows the user's real address.
        from monitor.views import get_ip
        headers['X-Forwarded-For'] = get_ip(request)
        try:
            r = _master_request(request.method, request.path_info,
                                request.META.get('QUERY_STRING', ''), user,
                                request.body if request.method not in SAFE_METHODS else b'',
                                prefix, headers, timeout=(5, 90))
        except Exception as e:
            log.warning('master unreachable: %s', e)
            return _error(request, 503, '⚠️ Master server unreachable',
                          'The master SysMonitor server cannot be reached right now, so this '
                          'change could not be saved. Nothing was changed. Please try again shortly.')

        resp = HttpResponse(r.content, status=r.status_code)
        for k in ('Content-Type', 'Content-Disposition', 'Cache-Control'):
            if k in r.headers:
                resp[k] = r.headers[k]
        loc = r.headers.get('Location')
        if loc:
            if loc.startswith('/') and not loc.startswith('//') and prefix and not loc.startswith(prefix + '/'):
                loc = prefix + loc
            resp['Location'] = loc
        # Password changed on the remote: the old login is no longer valid, so end
        # it on purpose and ask the user to log in again with the new password.
        if (request.path_info == PASSWORD_SAVE_PATH and request.method == 'POST'
                and r.status_code == 200):
            try:
                changed = bool(json.loads(r.content).get('ok'))
            except ValueError:
                changed = False
            if changed:
                request.session.flush()
                dj_messages.add_message(request, dj_messages.SUCCESS,
                                        'Password changed. Please log in again with your new password.')
        enc = r.headers.get('X-Mirror-Messages')
        if enc:
            try:
                for level, text, tags in json.loads(base64.b64decode(enc)):
                    dj_messages.add_message(request, level, text, extra_tags=tags)
            except Exception:
                log.exception('bad messages header')
        return resp

    def _track(self, request):
        """Tell the master about one page view (fire-and-forget)."""
        username = request.user.username if request.user.is_authenticated else ''
        prefix = request.META.get('SCRIPT_NAME', '').rstrip('/')

        def run():
            try:
                _master_request('POST', TRACK_PATH, '', username, b'', prefix, {}, timeout=(3, 5))
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _notify(self, request, action, username, detail):
        """Tell the master about a login/logout (fire-and-forget)."""
        from monitor.views import get_ip
        body = json.dumps({'action': action, 'username': username, 'detail': detail,
                           'ip': get_ip(request)}).encode()
        prefix = request.META.get('SCRIPT_NAME', '').rstrip('/')

        def run():
            try:
                _master_request('POST', ACTIVITY_PATH, '', username, body, prefix,
                                {'Content-Type': 'application/json'}, timeout=(3, 10))
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()


# ═════════════════════════════════════════════════════════════════════════════
# templates
# ═════════════════════════════════════════════════════════════════════════════

def _read_meta():
    try:
        with open(settings.MIRROR_META_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def mirror_context(request):
    # On the master, a forwarded request carries the remote's URL prefix so
    # pages rendered there get correct links.
    prefix = getattr(request, '_mirror_prefix', None)
    if prefix is None:
        prefix = request.META.get('SCRIPT_NAME', '').rstrip('/')
    ctx = {'base_path': prefix, 'is_mirror': bool(getattr(settings, 'IS_MIRROR', False))}
    if ctx['is_mirror']:
        pushed = _read_meta().get('pushed_at')
        ctx['mirror_pushed_at'] = pushed
        ctx['mirror_lag_seconds'] = int(time.time() - pushed) if pushed else None
    return ctx
