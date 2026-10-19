from django.conf import settings
from django.http import HttpResponseForbidden

class LocalDjangoAdminMiddleware:
    """Block remote Django admin HTTP access; trust only socket peer, not headers."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if path == '/admin' or path.startswith('/admin/'):
            peer = request.META.get('REMOTE_ADDR', '')
            # Mirror web users must never obtain Django Admin via the public proxy.
            is_mirror = getattr(settings, 'IS_MIRROR', False)
            # Forwarded requests are not equivalent to an actual local browser.
            forwarded = any(request.META.get(x) for x in (
                'HTTP_X_FORWARDED_FOR', 'HTTP_FORWARDED', 'HTTP_X_REAL_IP'))
            if is_mirror or peer not in ('127.0.0.1', '::1') or forwarded:
                return HttpResponseForbidden('Django Admin is restricted to local access.')
        return self.get_response(request)
