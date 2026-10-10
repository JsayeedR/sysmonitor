"""
Per-user page restrictions.

Important rule:
    ROLE permission is always the maximum.
    Page Access may only REMOVE pages from that role.

Nothing in this module can grant a page that the existing role system denies.
"""

PAGE_DEFINITIONS = {
    'dashboard': {
        'label': '📊 Dashboard',
        'group': 'Main',
        'roles': ('guest', 'viewer', 'user', 'admin'),
        'prefixes': ('/',),
        'exact_only': True,
    },
    'colocation': {
        'label': '🌡️ Colocation',
        'group': 'Main',
        'roles': ('guest', 'viewer', 'user', 'admin'),
        'prefixes': (
            '/sensor/',
            '/api/sensor-status/',
            '/api/sensor-history/',
            '/sensor/export/',
        ),
    },
    'load_shedding': {
        'label': '⚡ Load Shedding',
        'group': 'Main',
        'roles': ('guest', 'viewer', 'user', 'admin'),
        'prefixes': (
            '/report/',
            '/api/report/',
        ),
    },

    'generator_shifting': {
        'label': '🔄 Generator Shifting Entry',
        'group': 'Generator Data',
        'roles': ('user', 'admin'),
        'prefixes': ('/generator-log/',),
    },
    'generator_fuel_entry': {
        'label': '⛽ Generator Fuel Entry',
        'group': 'Generator Data',
        'roles': ('user', 'admin'),
        'prefixes': ('/generator-fuel/',),
        'exclude_prefixes': ('/generator-fuel-report/',),
    },
    'generator_manual_cycle': {
        'label': '🕵️ Generator Manual Cycle Entry',
        'group': 'Generator Data',
        'roles': ('user', 'admin'),
        'prefixes': ('/generator-cycle-audit/',),
    },
    'generator_fuel_report': {
        'label': '📊 Generator Fuel Report',
        'group': 'Generator Data',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': ('/generator-fuel-report/',),
    },
    'generator_runtime': {
        'label': '⏱️ Generator Runtime',
        'group': 'Generator Data',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': ('/genruntime/',),
    },

    'smw6pac': {
        'label': '❄️ SMW6PAC',
        'group': 'Others',
        'roles': ('viewer', 'admin'),
        'prefixes': ('/smw6pac/',),
    },
    'uptime': {
        'label': '🌐 Uptime Status',
        'group': 'Others',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': ('/uptime/',),
    },
    'events': {
        'label': '📋 All Events',
        'group': 'Others',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': ('/events/',),
    },
    'cctv': {
        'label': '🎥 NOC CCTV',
        'group': 'Others',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': ('/cctv/',),
    },
    'notification_request': {
        'label': '🔔 Notification Request',
        'group': 'Others',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': ('/notification-request/',),
    },
    'profile': {
        'label': '👤 My Profile',
        'group': 'Others',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': (
            '/profile/',
        ),
        # Password-change remains reachable even if My Profile is hidden.
        'exclude_prefixes': (
            '/profile/password/',
            '/profile/password/save/',
        ),
    },

    'manual': {
        'label': '📘 Manual',
        'group': 'Others',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': ('/manual/',),
    },

    'shift_report': {
        'label': '📝 Shift Report',
        'group': 'Main',
        'roles': ('user', 'admin'),
        'prefixes': ('/shift-report/',),
    },

    'duty_roster': {
        'label': '📅 Duty Roster',
        'group': 'Administrative',
        'roles': ('admin',),
        'prefixes': ('/duty-roster/',),
    },

    'devices': {
        'label': '🖥️ Devices',
        'group': 'Administrative',
        'roles': ('admin',),
        'prefixes': ('/devices/',),
    },
    'users': {
        'label': '👥 Users',
        'group': 'Administrative',
        'roles': ('admin',),
        'prefixes': ('/users/',),
    },
    'notifications_admin': {
        'label': '🔔 Notifications Administration',
        'group': 'Administrative',
        'roles': ('admin',),
        'prefixes': ('/notifications/',),
    },
    'activity': {
        'label': '🕵️ Activity',
        'group': 'Administrative',
        'roles': ('admin',),
        'prefixes': ('/activity/',),
    },
    'system': {
        'label': '🛠️ System',
        'group': 'Administrative',
        'roles': ('admin',),
        'prefixes': ('/system/',),
    },
    'cctv_setup': {
        'label': '🎥 CCTV Setup',
        'group': 'Administrative',
        'roles': ('admin',),
        'prefixes': ('/cctv-setup/',),
    },
    'colocation_setpoints': {
        'label': '🌡️ Colocation Setpoints',
        'group': 'Administrative',
        'roles': ('admin',),
        'prefixes': ('/colocation-setpoints/',),
    },
}


def pages_for_role(role):
    """Pages the role already owns. This is the maximum access ceiling."""
    return {
        key: cfg
        for key, cfg in PAGE_DEFINITIONS.items()
        if role in cfg['roles']
    }


def hidden_pages_for_user(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()

    try:
        profile = user.userprofile
    except Exception:
        return set()

    raw = profile.hidden_pages or []
    if not isinstance(raw, list):
        return set()

    # Ignore stale/invalid keys.
    return {
        key
        for key in raw
        if key in PAGE_DEFINITIONS
    }


def page_key_for_path(path):
    """
    Return the managed page key for this path, if any.

    Most-specific prefixes win so /generator-fuel-report/ never gets
    mistaken for /generator-fuel/.
    """
    candidates = []

    for key, cfg in PAGE_DEFINITIONS.items():
        for prefix in cfg.get('prefixes', ()):
            if cfg.get('exact_only'):
                if path == prefix:
                    candidates.append((len(prefix), key))
                continue

            if not path.startswith(prefix):
                continue

            excluded = cfg.get('exclude_prefixes', ())
            if any(path.startswith(x) for x in excluded):
                continue

            candidates.append((len(prefix), key))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def user_role(user):
    if getattr(user, 'is_superuser', False):
        return 'admin'

    try:
        return user.userprofile.role
    except Exception:
        return 'guest'


def user_can_access_page(user, key):
    """
    Existing role is checked first. A hidden-page override can only reduce it.
    """
    cfg = PAGE_DEFINITIONS.get(key)
    if cfg is None:
        return True

    role = user_role(user)

    if role not in cfg['roles']:
        return False

    return key not in hidden_pages_for_user(user)
