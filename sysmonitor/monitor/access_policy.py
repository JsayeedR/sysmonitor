"""Centralized security constraints for account management and mirror forwarding.
No Django model writes or migrations are performed by this module.
"""
FULL_VIEWER_USERNAME = 'fullviewer@sysmonitor'
ASSIGNABLE_ROLES = frozenset({'admin', 'user', 'viewer', 'fullviewer', 'guest'})
REMOTE_LOCAL_ONLY_PREFIXES = (
    '/admin/', '/users/', '/page-access/', '/system/',
    '/notifications/gateway/', '/notifications/recipient/',
    '/cctv-setup/', '/shift-report/config/',
    '/profile/pending-changes/',
)


def is_full_viewer(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'username', '').casefold() == FULL_VIEWER_USERNAME:
        return True  # Compatibility until the existing identity is assigned the role.
    try:
        return user.userprofile.role == 'fullviewer'
    except Exception:
        return False


def role_assignment_error(actor, desired, *, username=''):
    if desired not in ASSIGNABLE_ROLES:
        return 'Invalid account role.'
    if not actor.is_superuser and desired in {'admin', 'fullviewer'}:
        return 'Only a Superadmin can assign Admin or Full Viewer roles.'
    if username.casefold() == FULL_VIEWER_USERNAME:
        return 'The reserved Full Viewer username cannot be created through user management.'
    return None


def deny_remote_forward(user, path, method):
    """Fail closed for operations requiring local administrative authority.

    Remote-originating HMAC statements of identity never confer authority for
    master account administration/service control. This intentionally restricts
    previously-remote-managed sensitive tasks to the local console.
    """
    if any(path.startswith(prefix) for prefix in REMOTE_LOCAL_ONLY_PREFIXES):
        return True
    if getattr(user, 'is_superuser', False) and method not in ('GET', 'HEAD', 'OPTIONS'):
        return True
    return False
