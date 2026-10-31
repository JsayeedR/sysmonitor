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

    'generator_shifting_guideline': {
        'label': '📘 Generator Shifting Guideline',
        'group': 'Generator Data',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': ('/generator-shifting-guideline/',),
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

    'android_application': {
        'label': '📱 Android Application', 'group': 'Others',
        'roles': ('user', 'admin'),
        'prefixes': ('/android-application/',),
    },
    'duty_roster_view': {
        'label': '📅 Duty Roster (read-only)', 'group': 'Others',
        'roles': ('user', 'admin'),
        'prefixes': ('/duty-schedule/',),
    },
    'code_reference': {
        'label': '🧭 Code Reference', 'group': 'Others',
        'roles': ('user', 'admin'),
        'prefixes': ('/code-reference/',),
    },
    'administrator_cli': {
        'label': '💻 Administrator CLI Commands', 'group': 'Others',
        'roles': ('user', 'admin'),
        # In-manual section: no independent URL; view and PDF enforce this key.
        'prefixes': (),
    },
    'manual': {
        'label': '📘 Manual',
        'group': 'Others',
        'roles': ('viewer', 'user', 'admin'),
        'prefixes': ('/manual/',),
    },
    'about': {
        'label': 'ℹ️ About Us', 'group': 'Others',
        'roles': ('guest', 'viewer', 'user', 'admin', 'fullviewer'),
        'prefixes': ('/about/',), 'locked': True,
    },
    'account_recovery': {
        'label': '🔑 Login, Logout & Password Recovery',
        'group': 'Account & Recovery',
        'roles': ('guest', 'viewer', 'user', 'admin', 'fullviewer'),
        'prefixes': ('/login/', '/logout/', '/password-reset/',
                     '/profile/password/'), 'locked': True,
    },
    'page_access': {
        'label': '🔐 Page Access (recovery)', 'group': 'Administrative',
        'roles': ('admin',), 'prefixes': ('/page-access/',),
        'locked': True,
    },
    'maintenance': {
        'label': '🛠️ Maintenance Controls', 'group': 'Administrative',
        'roles': ('admin',), 'prefixes': ('/maintenance/',),
    },
    'security_patch_updates': {
        'label': '🛡️ Security Patch Updates',
        'group': 'Others',
        'roles': ('viewer', 'user', 'admin', 'fullviewer'),
        'prefixes': ('/security-patch-updates/',),
    },

    'shift_report': {
        'label': '📝 Shift Report (Main + Historical)',
        'group': 'Main',
        'roles': ('user', 'admin'),
        'prefixes': ('/shift-report/',),
        'exclude_prefixes': ('/shift-report/mnoc-pfe/', '/shift-report/config/',
                             '/shift-report/historical/'),
    },

    'historical_report': {
        'label': '📊 Historical Shift Report', 'group': 'Main',
        'roles': ('user', 'admin'),
        'prefixes': ('/shift-report/historical/',),
    },
    'mnoc_pfe': {
        'label': '⚡ MNOC PFE Report',
        'group': 'Main',
        'roles': ('user', 'admin'),
        'prefixes': ('/shift-report/mnoc-pfe/',),
    },
    'shift_report_config': {
        'label': '⚙️ Shift Report Configuration (Admin only)',
        'group': 'Administrative',
        'roles': ('admin',),
        'prefixes': ('/shift-report/config/',),
    },

    'outage_cycle_remarks': {
        'label': '⚡ Outage Cycle Remarks', 'group': 'Generator Data',
        'roles': ('user','admin'), 'prefixes': ('/outage-cycle-remarks/',),
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
    # The reserved Full Viewer receives the complete read-only menu; the
    # independent FullViewerReadOnlyMiddleware still governs each endpoint.
    from .access_policy import is_full_viewer
    if is_full_viewer(user):
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
        if key in PAGE_DEFINITIONS and (
            not PAGE_DEFINITIONS[key].get('locked')
            or (key == 'page_access' and not user.is_superuser)
        )
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
    # Both the reserved account and accounts explicitly assigned the
    # Full Viewer role receive the Admin READ ceiling. The independent
    # FullViewerReadOnlyMiddleware still denies every mutation/export.
    from .access_policy import is_full_viewer
    if is_full_viewer(user):
        return 'admin'
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

    # Full Viewer has a compatibility read ceiling of Admin for older pages;
    # never use that ceiling to grant these restricted documentation/roster pages.
    from .access_policy import is_full_viewer
    if key in ('android_application', 'duty_roster_view',
               'code_reference', 'administrator_cli',
               'generator_shifting_guideline') and is_full_viewer(user):
        return False

    role = user_role(user)

    if role not in cfg['roles']:
        return False

    # Only the Superadmin retains guaranteed recovery access. Normal Admin
    # access to the Page Access screen is controlled by a Superadmin.
    if key == 'page_access':
        return bool(user.is_superuser) or key not in hidden_pages_for_user(user)
    return cfg.get('locked', False) or key not in hidden_pages_for_user(user)
