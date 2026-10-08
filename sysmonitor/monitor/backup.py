import os
import sys
import shutil
import tarfile
import subprocess
import django
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
django.setup()

import pytz
BDT = pytz.timezone('Asia/Dhaka')

from monitor.models import Event

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH         = os.path.join(BASE_DIR, 'db.sqlite3')
BACKUP_DIR      = os.path.join(BASE_DIR, 'backups')
DB_BACKUP_DIR   = os.path.join(BACKUP_DIR, 'database')
PROJ_BACKUP_DIR = os.path.join(BACKUP_DIR, 'project')
KEEP_DAYS       = 30  # keep last 30 daily backups (applies to both database and project)

# Folders/files to exclude from the project archive — venv is the big one,
# plus other things that don't belong in a code/config backup
EXCLUDE_NAMES = {
    'venv', '.venv', 'env',           # virtual environments
    '__pycache__', '.git', '.idea',   # caches / IDE / vcs metadata
    'backups',                        # don't recursively back up the backups folder itself
    'db.sqlite3',                     # database is backed up separately above
}

# ── Remote (offsite) sync ────────────────────────────────────────────────────
# Reads config from environment (.env) — all optional. If REMOTE_BACKUP_HOST
# is unset, remote sync is skipped entirely and only local backups run (same
# behavior as before this was added).
REMOTE_BACKUP_HOST = os.environ.get('REMOTE_BACKUP_HOST', '').strip()
REMOTE_BACKUP_USER = os.environ.get('REMOTE_BACKUP_USER', '').strip()
REMOTE_BACKUP_PORT = os.environ.get('REMOTE_BACKUP_PORT', '22').strip()
REMOTE_BACKUP_PATH = os.environ.get('REMOTE_BACKUP_PATH', '/home/backup/sysmonitor').strip()
REMOTE_BACKUP_SSH_KEY = os.environ.get('REMOTE_BACKUP_SSH_KEY', '').strip()


def remote_sync_enabled():
    return bool(REMOTE_BACKUP_HOST and REMOTE_BACKUP_USER)


def rsync_to_remote(local_path, date_str, label):
    """
    Pushes local_path (file or directory) to the remote server via rsync
    over SSH. Returns (ok, message). Requires the `rsync` binary and an
    SSH key that's already authorized on the remote host (ssh-copy-id) —
    this deliberately never handles a password, only key-based auth.
    """
    if not remote_sync_enabled():
        return True, 'skipped (REMOTE_BACKUP_HOST not configured)'

    ssh_cmd = f'ssh -p {REMOTE_BACKUP_PORT} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10'
    if REMOTE_BACKUP_SSH_KEY:
        ssh_cmd += f' -i {REMOTE_BACKUP_SSH_KEY}'

    remote_target = f'{REMOTE_BACKUP_USER}@{REMOTE_BACKUP_HOST}:{REMOTE_BACKUP_PATH}/'

    cmd = ['rsync', '-az', '--mkpath', '-e', ssh_cmd, local_path, remote_target]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            print(f'[{date_str}] Remote sync OK — {label} → {REMOTE_BACKUP_HOST}')
            return True, ''
        else:
            print(f'[{date_str}] Remote sync FAILED — {label}: {result.stderr.strip()}')
            return False, result.stderr.strip()
    except FileNotFoundError:
        return False, "rsync not installed (sudo apt install rsync)"
    except subprocess.TimeoutExpired:
        return False, 'rsync timed out after 5 minutes'
    except Exception as e:
        return False, str(e)


def should_exclude(path):
    """Returns True if this path (file or dir) should be skipped in the tarball."""
    name = os.path.basename(path)
    return name in EXCLUDE_NAMES or name.endswith('.pyc')


def backup_database(now_bdt, date_str, timestamp):
    """Backs up db.sqlite3 into backups/database/."""
    os.makedirs(DB_BACKUP_DIR, exist_ok=True)
    backup_filename = f'db_backup_{timestamp}.sqlite3'
    backup_path     = os.path.join(DB_BACKUP_DIR, backup_filename)

    shutil.copy2(DB_PATH, backup_path)
    size_kb = os.path.getsize(backup_path) // 1024
    print(f'[{date_str}] Database backup saved: {backup_filename} ({size_kb} KB)')
    return backup_filename, size_kb


def backup_project(now_bdt, date_str, timestamp):
    """Backs up the full project folder (code + templates + systemd configs)
    as a compressed tar.gz into backups/project/, excluding venv and other
    non-essential folders."""
    os.makedirs(PROJ_BACKUP_DIR, exist_ok=True)
    archive_filename = f'project_backup_{timestamp}.tar.gz'
    archive_path     = os.path.join(PROJ_BACKUP_DIR, archive_filename)

    def tar_filter(tarinfo):
        # tarinfo.name is relative to the arcname root we set below
        parts = tarinfo.name.split('/')
        for part in parts:
            if part in EXCLUDE_NAMES or part.endswith('.pyc'):
                return None  # returning None excludes this entry
        return tarinfo

    with tarfile.open(archive_path, 'w:gz') as tar:
        tar.add(BASE_DIR, arcname='sysmonitor', filter=tar_filter)

    size_mb = os.path.getsize(archive_path) / (1024 * 1024)
    print(f'[{date_str}] Project backup saved: {archive_filename} ({size_mb:.1f} MB)')
    return archive_filename, size_mb


def cleanup_old_backups(folder, prefix, suffix, date_str, label):
    """Keeps only the last KEEP_DAYS backups in the given folder."""
    all_backups = sorted([
        f for f in os.listdir(folder)
        if f.startswith(prefix) and f.endswith(suffix)
    ])
    if len(all_backups) > KEEP_DAYS:
        to_delete = all_backups[:len(all_backups) - KEEP_DAYS]
        for old_file in to_delete:
            os.remove(os.path.join(folder, old_file))
            print(f'[{date_str}] Deleted old {label} backup: {old_file}')
        return len(to_delete)
    return 0


def run():
    now_bdt   = datetime.now(BDT)
    timestamp = now_bdt.strftime('%Y-%m-%d_%H-%M')
    date_str  = now_bdt.strftime('%d/%m/%Y %I:%M %p')

    os.makedirs(BACKUP_DIR, exist_ok=True)

    try:
        # ── 1. Database backup ───────────────────────────────────────────────
        db_filename, db_size_kb = backup_database(now_bdt, date_str, timestamp)

        # ── 2. Project (code) backup ─────────────────────────────────────────
        proj_filename, proj_size_mb = backup_project(now_bdt, date_str, timestamp)

        # ── 2b. Push both to the offsite remote server (if configured) ───────
        # This is the once-daily "large data" sync — it rides on the same
        # 00:01 BDT timer as the local backup above. Small, frequent
        # real-time sync of just the live database is a separate, lighter
        # job (see realtime_sync.py + its own systemd timer).
        remote_ok = True
        remote_note = ''
        if remote_sync_enabled():
            ok1, msg1 = rsync_to_remote(
                os.path.join(DB_BACKUP_DIR, db_filename), date_str, 'database backup')
            ok2, msg2 = rsync_to_remote(
                os.path.join(PROJ_BACKUP_DIR, proj_filename), date_str, 'project backup')
            remote_ok = ok1 and ok2
            if not remote_ok:
                remote_note = f' | Remote sync issue: {msg1 or msg2}'

        # ── 3. Auto-cleanup 0s cycles before logging ─────────────────────────
        from monitor.models import OutageCycle
        deleted = OutageCycle.objects.filter(is_complete=True, pdb_duration_sec=0).delete()
        cleaned = deleted[0] if deleted else 0
        if cleaned:
            print(f'[{date_str}] Auto-cleaned {cleaned} zero-duration cycle(s)')
            Event.objects.create(
                device=None,
                level='INFO',
                message=f'Auto-cleanup: removed {cleaned} zero-duration cycle(s).'
            )

        # ── 4. Log success event to dashboard ────────────────────────────────
        remote_status = ''
        if remote_sync_enabled():
            remote_status = (
                ' | Remote synced'
                if remote_ok
                else remote_note
            )

        Event.objects.create(
            device=None,
            level='INFO' if remote_ok else 'ALARM',
            message=(
                f'Backup completed — DB {db_size_kb / 1024:.1f} MB'
                f' | Project {proj_size_mb:.1f} MB'
                f'{remote_status}'
            ),
        )

        # ── 5. Cleanup old backups — keep only last KEEP_DAYS in each folder ─
        db_deleted   = cleanup_old_backups(DB_BACKUP_DIR, 'db_backup_', '.sqlite3', date_str, 'database')
        proj_deleted = cleanup_old_backups(PROJ_BACKUP_DIR, 'project_backup_', '.tar.gz', date_str, 'project')

        if db_deleted or proj_deleted:
            Event.objects.create(
                device=None,
                level='INFO',
                message=(
                    f'Backup cleanup — DB {db_deleted} removed'
                    f' | Project {proj_deleted} removed'
                    f' | Retention {KEEP_DAYS} days'
                ),
            )

    except Exception as e:
        print(f'[{date_str}] Backup FAILED: {e}')
        Event.objects.create(
            device=None,
            level='CRITICAL',
            message=f'Backup FAILED — {str(e)}'
        )
        sys.exit(1)


if __name__ == '__main__':
    run()
