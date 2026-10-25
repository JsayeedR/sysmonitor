"""Conservative server-side Full Viewer guard.

Routes not explicitly audited for safe GET behavior are inaccessible.
Use a non-superuser account; never put its password in source control.
"""
from django.http import HttpResponseForbidden
from django.urls import Resolver404, resolve

IDENTITY = 'fullviewer@sysmonitor'

# Explicit safe read-only route names, checked against Django's URL resolver.
# Unsafe/unknown admin, mutation, attachment and export routes fail closed.
SAFE_ROUTES = frozenset({
    'dashboard', 'about', 'manual', 'security_patch_updates', 'api_status', 'api_daily_summary',
    'api_report', 'user_list', 'duty_roster_manage', 'duty_roster_current',
    'device_list', 'event_log', 'activity_log', 'report', 'uptime_status',
    'uptime_status_log', 'pac_status', 'sensor_status', 'api_sensor_status',
    'api_sensor_history', 'generator_runtime_report', 'generator_fuel_report',
    'outage_cycle_remarks', 'generator_cycle_audit', 'generator_cycle_audit_data',
    'generator_log_page', 'maintenance_status', 'profile_view',
    'system_recent_cycles', 'cctv_view', 'cctv_live_status',
    'shiftreport:home', 'shiftreport:view',
    'shiftreport:pfe_home', 'shiftreport:slot_check',
    # RC24.8: specifically reviewed display-only GET routes. No mutation,
    # download, export or credential configuration handlers are admitted.
    'page_access_manage', 'colocation_setpoints', 'system_tools',
    'system_live_state', 'generator_fuel', 'generator_cycle_audit',
    'generator_log_page', 'shiftreport:pfe_edit',
    'shiftreport:activity_source',
})

# Some report screens currently show an edit form on GET. These may be added
# only after a route-specific source review proves that GET is side-effect free.

DENIED_QUERY_KEYS = frozenset({
    'export', 'download', 'attachment', 'file', 'format', 'print', 'send',
    'action', 'execute', 'trigger', 'restart', 'delete', 'approve', 'reset',
})
SAFE_CONTENT_TYPES = ('text/html', 'application/json', 'text/plain')


def is_fullviewer(user):
    from .access_policy import is_full_viewer
    return is_full_viewer(user)


def forbidden_request(request):
    # A signed, nonce-checked mirror request has already passed through
    # MirrorInboundMiddleware, which marks it _mirror_forwarded. Permit ONLY
    # the exact internal, read-only identity verification POST; the endpoint
    # itself still compares the identity proof with the master's live account.
    # Browser POSTs, invalid signatures, and all other mutations stay denied.
    if (request.method == 'POST'
            and request.path_info == '/mirror/auth/validate/'
            and getattr(request, '_mirror_forwarded', False) is True):
        return False
    # Permit only the two exact logout URLs.
    # The reverse-proxied public portal retains /sysmonitor/.
    logout_path = request.path_info.rstrip('/').lower()
    if (
        request.method in ('GET', 'POST')
        and logout_path in ('/logout', '/sysmonitor/logout')
    ):
        return False


    from django.urls import resolve, Resolver404

    if request.method in ('GET', 'POST'):
        try:
            match = resolve(request.path_info)
            if match.url_name == 'logout':
                return False
        except Resolver404:
            pass


    if request.method not in ('GET', 'HEAD'):
        return True
    if any(key.casefold() in DENIED_QUERY_KEYS for key in request.GET):
        return True
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return True
    route = match.view_name
    return route not in SAFE_ROUTES


class FullViewerReadOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_fullviewer(getattr(request, 'user', None)):
            return self.get_response(request)
        if forbidden_request(request):
            return HttpResponseForbidden('Full Viewer: this operation or resource is restricted.')
        response = self.get_response(request)
        disposition = response.get('Content-Disposition', '').lower()
        content_type = response.get('Content-Type', '').lower().split(';', 1)[0].strip()
        if disposition or content_type not in SAFE_CONTENT_TYPES:
            if hasattr(response, 'close'):
                response.close()
            return HttpResponseForbidden('Full Viewer: files and downloads are disabled.')
        response['Cache-Control'] = 'private, no-store'
        return response
