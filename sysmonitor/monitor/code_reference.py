"""Read-only code reference, restricted to approved User/Admin roles."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .access_policy import is_full_viewer
from .code_reference_content import BASE_COMMIT, FILE_COUNT, FLOW, GROUPS, TREE


@require_GET
@login_required(login_url='login')
def code_reference_view(request):
    # The ordinary Admin role is insufficient. This must also reject Full
    # Viewer even though that identity has an Admin *display* role ceiling.
    user = request.user
    from .page_access import user_can_access_page
    from .views import get_role
    if (
        not user.is_active
        or get_role(user) not in ('user', 'admin')
        or is_full_viewer(user)
        or not user_can_access_page(user, 'code_reference')
        or getattr(request, '_mirror_forwarded', False)
    ):
        return render(request, 'monitor/denied.html', status=403)

    response = render(request, 'monitor/code_reference.html', {
        'role': 'admin',
        'code_reference_baseline': BASE_COMMIT,
        'code_reference_file_count': FILE_COUNT,
        'code_reference_tree': TREE,
        'code_reference_groups': GROUPS,
        'code_reference_flow': FLOW,
    })
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response
