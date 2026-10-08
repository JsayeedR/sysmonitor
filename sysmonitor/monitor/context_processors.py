from django.conf import settings
from django.db.models import F
from .models import PageViewCounter


def page_counter(request):
    """Atomically increments the page view counter on every request and
    makes the current value available to all templates as {{ page_view_count }}."""
    if getattr(settings, 'IS_MIRROR', False):
        # The remote only shows the master's number; it never writes.
        row = PageViewCounter.objects.filter(id=1).first()
        return {'page_view_count': row.count if row else 0}
    PageViewCounter.objects.get_or_create(id=1, defaults={'count': 789})
    PageViewCounter.objects.filter(id=1).update(count=F('count') + 1)
    current = PageViewCounter.objects.get(id=1).count
    return {'page_view_count': current}


def system_revision(request):
    """
    Expose the compact SysMonitor version to templates.

    MASTER also checks periodically whether the deployed Git commit changed.
    REMOTE only reads the mirrored singleton row.
    """
    from .system_revision import sync_git_revision

    row = sync_git_revision()

    if row is None:
        return {
            'sys_version': '1.1.1234',
            'sys_modified_at': None,
        }

    return {
        'sys_version': row.version,
        'sys_modified_at': row.modified_at,
    }


def page_access(request):
    """
    Makes individual restrictions available to the shared navbar.
    """
    from .page_access import hidden_pages_for_user

    hidden = hidden_pages_for_user(
        getattr(request, 'user', None)
    )

    generator_keys = {
        'generator_shifting',
        'generator_fuel_entry',
        'generator_manual_cycle',
        'generator_fuel_report',
        'generator_runtime',
    }

    others_keys = {
        'smw6pac',
        'uptime',
        'events',
        'cctv',
        'notification_request',
        'profile',
    }

    admin_keys = {
        'devices',
        'users',
        'notifications_admin',
        'activity',
        'system',
        'cctv_setup',
        'colocation_setpoints',
    }

    return {
        'page_hidden': hidden,
        'page_show_generator': bool(generator_keys - hidden),
        'page_show_others': bool(others_keys - hidden),
        'page_show_admin': bool(admin_keys - hidden),
    }
