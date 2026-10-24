"""Safe, read-only security release notes for signed-in non-guest roles.

No database introspection or operational configuration is exposed. This is a
release bulletin, not an assessment of the system's current security score.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .page_access import user_role


@require_GET
@login_required(login_url='login')
def security_patch_updates_view(request):
    # PageAccessMiddleware may further restrict individual access. Never rely
    # on the navbar alone to protect the endpoint.
    if user_role(request.user) not in ('viewer', 'user', 'admin', 'fullviewer'):
        return render(request, 'monitor/denied.html', status=403)
    from .views import get_role
    response = render(request, 'monitor/security_updates.html', {'role': get_role(request.user)})
    response['Cache-Control'] = 'private, no-store'
    return response
