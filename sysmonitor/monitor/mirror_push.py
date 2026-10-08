"""
monitor/mirror_push.py — LOCAL server → REMOTE mirror (app.bsccl.com/sysmonitor)

Two modes (both run by systemd timers, see systemd/sysmonitor-mirror-*.timer):

  python monitor/mirror_push.py          every ~30 s  → "live" push
        1. takes a consistent snapshot of db.sqlite3 (SQLite online-backup API,
           safe while the ping service is writing)
        2. blanks the secrets in the snapshot (gateway tokens/passwords) and
           removes login sessions — the remote copy never needs them
        3. if the snapshot changed since last time, sends it as a NEW file
           (mirror-<id>.sqlite3), then flips the small `mirror_current` pointer
           to it and deletes snapshots older than 10 min. A file is never
           overwritten while the remote web page may be using it.
        4. always sends a tiny mirror_meta.json (the "data is N seconds old"
           banner on the remote reads it)
        5. sends media/ (profile pictures) only when something in it changed

  python monitor/mirror_push.py --full   nightly → whole project (code,
        templates, systemd files…) so the remote always runs the same version.
        Never sends .env, databases, backups or the virtualenv.

If the remote is unreachable nothing breaks: the next 30-second run simply tries
again. After ~5 minutes of failures ONE alarm Event is added to the event log,
and one "recovered" Event when it works again.

Configuration (.env on the LOCAL server) — if MIRROR_HOST is not set this
script does nothing:
    MIRROR_HOST=app.bsccl.com        MIRROR_USER=app-admin
    MIRROR_PORT=3048                  MIRROR_PATH=/home/app-admin/sysmonitor/repo/sysmonitor
    MIRROR_SSH_KEY=/home/nanolab/.ssh/sysmonitor_ed25519
"""
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')

if __name__ == '__main__':          # run by systemd; inside the web server Django is already set up
    import django  # noqa: E402
    django.setup()

DB_PATH = BASE_DIR / 'db.sqlite3'
MEDIA_DIR = BASE_DIR / 'media'
STATE_PATH = BASE_DIR / '.mirror_state.json'

HOST = os.environ.get('MIRROR_HOST', '').strip()
USER = os.environ.get('MIRROR_USER', '').strip()
PORT = os.environ.get('MIRROR_PORT', '3048').strip()
RPATH = os.environ.get('MIRROR_PATH', '/home/app-admin/sysmonitor/repo/sysmonitor').strip().rstrip('/')
KEY = os.environ.get('MIRROR_SSH_KEY', '').strip()
# 'git'   → code reaches the remote through GitHub (deploy/remote-update.sh);
#           the nightly job then only re-sends media.
# 'rsync' → (default) the nightly job copies the whole project by rsync.
CODE_SYNC = os.environ.get('MIRROR_CODE_SYNC', 'rsync').strip().lower()

ALARM_AFTER_FAILURES = 10          # × 30 s ≈ 5 minutes

# Never copied by the nightly full push.
FULL_EXCLUDES = [
    '.env', '.env.*', 'venv/', '.venv/', '.git/', '__pycache__/', '*.pyc',
    'db.sqlite3*', 'mirror.sqlite3*', 'mirror-*', '.mirror-*', 'mirror_current',
    'mirror_meta.json', '.mirror_state.json', '.mirror_push.lock', '.mirror_nonces/',
    'backups/', 'media/', 'staticfiles/',
    '*.backup_*', '*.pre-patch*', '*.bak', 'x',
]

# Secrets blanked in the snapshot the remote receives.
SCRUB_SQL = [
    "DELETE FROM django_session",
    "UPDATE monitor_notificationgateway SET wa_access_token='', tg_bot_token='', email_password=''",
]


def configured():
    return bool(HOST and USER)


def ssh_cmd():
    cmd = f'ssh -p {PORT} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes'
    if KEY:
        cmd += f' -i {KEY}'
    return cmd


def rsync(args, timeout=300):
    cmd = ['rsync', '-az', '--mkpath', '-e', ssh_cmd()] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, 'rsync is not installed (sudo apt install rsync)'
    except subprocess.TimeoutExpired:
        return False, f'rsync timed out after {timeout}s'
    return (r.returncode == 0), r.stderr.strip()


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(st):
    tmp = STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(st))
    tmp.replace(STATE_PATH)


def make_snapshot(dest):
    """Consistent, scrubbed copy of the live DB at `dest`."""
    src = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=30)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
        for sql in SCRUB_SQL:
            try:
                dst.execute(sql)
            except sqlite3.OperationalError:
                pass  # table not present — ignore
        dst.commit()
        dst.execute('PRAGMA journal_mode=DELETE')   # single self-contained file
    finally:
        dst.close()
        src.close()


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def media_signature():
    if not MEDIA_DIR.exists():
        return ''
    parts = []
    for p in sorted(MEDIA_DIR.rglob('*')):
        if p.is_file():
            st = p.stat()
            parts.append(f'{p.relative_to(MEDIA_DIR)}:{st.st_size}:{int(st.st_mtime)}')
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()


def record_result(st, ok, msg):
    """Count failures; raise one alarm / one recovery event."""
    from monitor.models import Event
    if ok:
        if st.get('alarmed'):
            Event.objects.create(device=None, level='INFO',
                                 message='Remote mirror sync recovered')
        st['fails'], st['alarmed'] = 0, False
    else:
        st['fails'] = st.get('fails', 0) + 1
        print(f'mirror push failed ({st["fails"]}): {msg}')
        if st['fails'] >= ALARM_AFTER_FAILURES and not st.get('alarmed'):
            Event.objects.create(device=None, level='ALARM',
                                 message=f'Remote mirror sync FAILING for ~5 min — {msg[:200]}')
            st['alarmed'] = True
    save_state(st)


class push_lock:
    """One push at a time (the 30-second timer and a push-after-save never overlap)."""
    def __enter__(self):
        self.f = open(BASE_DIR / '.mirror_push.lock', 'w')
        deadline = time.time() + 60
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.time() > deadline:
                    self.f.close()
                    raise TimeoutError('another push is still running')
                time.sleep(0.2)

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def live_push():
    if not configured():
        return True
    with push_lock():
        return _live_push()


def _live_push():
    st = load_state()
    with tempfile.TemporaryDirectory(prefix='mirror_') as tmp:
        snap = os.path.join(tmp, 'mirror.sqlite3')
        make_snapshot(snap)
        digest = sha256(snap)

        meta = os.path.join(tmp, 'mirror_meta.json')
        with open(meta, 'w') as f:
            json.dump({'pushed_at': int(time.time())}, f)

        files = [meta]
        db_changed = digest != st.get('db_hash')
        snap_name = f'mirror-{digest[:16]}.sqlite3'
        if db_changed:
            named = os.path.join(tmp, snap_name)
            os.replace(snap, named)
            files.append(named)
        ok, msg = rsync(files + [f'{USER}@{HOST}:{RPATH}/'], timeout=120)

        if ok and db_changed:
            # Point the remote web page at the new snapshot (atomic rename),
            # then tidy up old snapshots (kept 10 min so running requests finish).
            remote_cmd = (
                f"cd '{RPATH}' && printf '%s' '{snap_name}' > .mirror_current.tmp "
                f"&& mv -f .mirror_current.tmp mirror_current "
                f"&& find . -maxdepth 1 \\( -name 'mirror-*.sqlite3*' -o -name '.mirror-*' \\) "
                f"! -name 'mirror-{digest[:16]}.sqlite3*' -mmin +10 -delete"
            )
            r = subprocess.run(ssh_cmd().split() + [f'{USER}@{HOST}', remote_cmd],
                               capture_output=True, text=True, timeout=60)
            ok, msg = (r.returncode == 0), r.stderr.strip()
            if ok:
                st['db_hash'] = digest

        if ok:
            sig = media_signature()
            if sig != st.get('media_sig') and MEDIA_DIR.exists():
                ok, msg = rsync([f'{MEDIA_DIR}/', f'{USER}@{HOST}:{RPATH}/media/'])
                if ok:
                    st['media_sig'] = sig
    record_result(st, ok, msg)
    return ok


def full_push():
    started = time.monotonic()

    with push_lock():
        ok = _full_push()

        if ok:
            from monitor.models import Event

            elapsed = time.monotonic() - started

            mode_label = (
                'GitHub code + media'
                if CODE_SYNC == 'git'
                else 'project + media'
            )

            Event.objects.create(
                device=None,
                level='INFO',
                message=(
                    'Remote mirror full sync completed'
                    f' — {elapsed:.1f}s'
                    f' | {mode_label}'
                ),
            )

        return ok


def _full_push():
    st = load_state()
    if CODE_SYNC == 'git':
        ok, msg = True, ''
        if MEDIA_DIR.exists():
            ok, msg = rsync([f'{MEDIA_DIR}/', f'{USER}@{HOST}:{RPATH}/media/'])
            if ok:
                st['media_sig'] = media_signature()
        record_result(st, ok, msg)
        print('media push (code comes from GitHub)', 'OK' if ok else f'FAILED: {msg}')
        return ok
    args = []
    for pat in FULL_EXCLUDES:
        args += ['--exclude', pat]
    # --delete keeps the remote code identical; excluded files are protected.
    ok, msg = rsync(['--delete'] + args + [f'{BASE_DIR}/', f'{USER}@{HOST}:{RPATH}/'], timeout=900)
    if ok and MEDIA_DIR.exists():
        ok, msg = rsync([f'{MEDIA_DIR}/', f'{USER}@{HOST}:{RPATH}/media/'])
    if ok:
        st['media_sig'] = media_signature()
        # Also make the remote web server pick up the new code.
        subprocess.run(['ssh'] + ssh_cmd().split()[1:] + [f'{USER}@{HOST}',
                        'sudo systemctl restart sysmonitor-remote-web'],
                       capture_output=True, timeout=60)
    record_result(st, ok, msg)
    print('full push', 'OK' if ok else f'FAILED: {msg}')
    return ok


if __name__ == '__main__':
    if not configured():
        sys.exit(0)           # nothing configured — quietly do nothing
    try:
        ok = full_push() if '--full' in sys.argv else live_push()
    except Exception as e:    # never let a bad run crash the timer loop
        print(f'mirror push error: {e}')
        ok = False
    sys.exit(0 if ok else 1)
