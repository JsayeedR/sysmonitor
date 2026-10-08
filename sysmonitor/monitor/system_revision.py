import subprocess
import time
from pathlib import Path

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import SystemRevision


# These are audit events, but they are not meaningful system modifications.
REVISION_ACTIONS = {
    # User / account administration
    'USER_CREATED',
    'USER_EDITED',
    'USER_DELETED',
    'PROFILE_UPDATE',
    'PROFILE_CHANGE_APPROVE',
    'PROFILE_CHANGE_REJECT',
    'PASSWORD_CHANGE',

    # Device administration
    'DEVICE_ADDED',
    'DEVICE_EDITED',
    'DEVICE_DELETED',

    # Notification configuration
    'NOTIF_REQUEST_APPROVE',
    'NOTIF_GATEWAY_EDIT',
    'NOTIF_RECIPIENT_ADD',
    'NOTIF_RECIPIENT_EDIT',
    'NOTIF_RECIPIENT_DELETE',
    'NOTIF_RECIPIENT_TOGGLE',

    # Generator / operational records
    'GEN_SHIFT_ADD',
    'GEN_SHIFT_EDIT',
    'GEN_SHIFT_DELETE',
    'GENERATOR_FUEL_ADD',

    # Outage-cycle administration
    'CYCLE_CLOSE',
    'CYCLE_DELETE',
    'CYCLE_MANUAL_ADD',
    'CYCLE_MANUAL_EDIT',
    'CYCLE_MANUAL_DELETE',

    # System configuration / administration
    'COLO_SETPOINTS',
    'MAINT_START',
    'MAINT_STOP',
    'SERVICE_RESTART',
    'CCTV_CONFIG',
    'MESSAGE_TEMPLATE_EDIT',
}


_last_git_check = 0.0
_git_check_interval = 30.0


def get_system_revision():
    """
    Return the singleton revision row.

    MASTER may create it if missing.
    REMOTE never writes its read-only mirror database.
    """
    row = SystemRevision.objects.filter(pk=1).first()

    if row is not None:
        return row

    if settings.IS_MIRROR:
        return None

    return SystemRevision.objects.create(
        pk=1,
        major=1,
        minor=1,
        revision=1234,
        modified_by='system',
        last_action='System revision tracking initialized',
    )


def bump_system_revision(user=None, action='', git_commit=None):
    """
    Atomically increase the monotonically increasing revision on MASTER.
    """
    if settings.IS_MIRROR:
        return None

    row = get_system_revision()
    if row is None:
        return None

    username = ''
    if user is not None:
        username = getattr(user, 'username', '') or ''

    updates = {
        'revision': F('revision') + 1,
        'modified_at': timezone.now(),
        'modified_by': username or 'system',
        'last_action': (action or 'System modification')[:200],
    }

    if git_commit is not None:
        updates['git_commit'] = git_commit[:40]

    SystemRevision.objects.filter(pk=1).update(**updates)

    return SystemRevision.objects.get(pk=1)


def bump_for_activity(user, action, detail=''):
    """
    Increase revision for meaningful persisted SysMonitor changes.

    Login/logout, test sends and ordinary report delivery are intentionally
    excluded so the revision remains a useful modification counter.
    """
    if action not in REVISION_ACTIONS:
        return None

    return bump_system_revision(
        user=user,
        action=f'{action}: {detail}' if detail else action,
    )


def _current_git_commit():
    """
    Read the repository HEAD. The Django project is inside the repository
    root, so BASE_DIR.parent is used.
    """
    repo_root = Path(settings.BASE_DIR).parent

    try:
        result = subprocess.run(
            ['git', '-C', str(repo_root), 'rev-parse', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except Exception:
        return ''

    return result.stdout.strip()[:40]


def sync_git_revision():
    """
    Detect a new deployed Git commit.

    The first detected commit is recorded without increasing 1234.
    A later different commit increases the revision exactly once.
    """
    global _last_git_check

    if settings.IS_MIRROR:
        return get_system_revision()

    now_mono = time.monotonic()

    if now_mono - _last_git_check < _git_check_interval:
        return get_system_revision()

    _last_git_check = now_mono

    commit = _current_git_commit()
    row = get_system_revision()

    if row is None or not commit:
        return row

    if not row.git_commit:
        SystemRevision.objects.filter(pk=1).update(
            git_commit=commit,
        )
        row.git_commit = commit
        return row

    if row.git_commit != commit:
        return bump_system_revision(
            user=None,
            action='SysMonitor code deployment',
            git_commit=commit,
        )

    return row
