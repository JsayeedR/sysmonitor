"""Dedicated fullviewer read-only guard. Non-superuser identity; no secrets exposure."""
from django.http import HttpResponseForbidden

IDENTITY='fullviewer@sysmonitor'

# Admin configuration pages displaying tokens/passwords must not be exposed.
SENSITIVE_PREFIXES=(
    '/admin/', '/notifications/', '/shift-report/config/', '/system/',
    '/cctv-setup/', '/mirror/', '/backup/', '/page-access/',
)
# Strictly reject download/export endpoints; response header provides defense in depth.
DOWNLOAD_PATH_MARKERS=(
    '/download/', '/export/', '/attachment/', '/attachments/', '/media/',
    '/pdf/', '/print/', '/template.xlsx', '.csv', '.xlsx', '.pdf', '.zip',
)
# GET endpoints that can send notifications or cause mutations without POST.
ACTION_PATH_MARKERS=(
    '/send-now/', '/send-test/', '/test-send/', '/restart/', '/stop/',
    '/start/', '/reset/', '/trigger/', '/activate/', '/execute/',
    '/delete/', '/approve/', '/reject/', '/toggle/', '/import/',
    '/add/', '/save/', '/upload/', '/clear/', '/change/', '/force/',
)


def is_fullviewer(user):
    return bool(user and user.is_authenticated and user.username.lower()==IDENTITY)


def forbidden_request(request):
    path=request.path_info.lower()
    if request.method not in ('GET','HEAD','OPTIONS'):
        return True
    if any(path.startswith(p) for p in SENSITIVE_PREFIXES):
        return True
    if any(x in path for x in DOWNLOAD_PATH_MARKERS+ACTION_PATH_MARKERS):
        return True
    query=request.GET
    if any(key.lower() in ('export','download','attachment','file','format','print','send','action') for key in query):
        return True
    return False


class FullViewerReadOnlyMiddleware:
    def __init__(self,get_response):
        self.get_response=get_response
    def __call__(self,request):
        user=getattr(request,'user',None)
        if not is_fullviewer(user):
            return self.get_response(request)
        if forbidden_request(request):
            return HttpResponseForbidden('Full Viewer: read-only access; downloads and sensitive configuration are restricted.')
        response=self.get_response(request)
        disposition=response.get('Content-Disposition','').lower()
        if 'attachment' in disposition:
            return HttpResponseForbidden('Full Viewer: downloading is disabled.')
        return response
