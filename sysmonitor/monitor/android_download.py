"""Authenticated Android download landing page; no APK storage or proxy."""
from django.shortcuts import render
from django.http import HttpResponseNotAllowed
from django.contrib.auth.decorators import login_required
from .access_policy import is_full_viewer
from .page_access import user_can_access_page
from .views import get_role

APK_URL = 'https://bscplc.com.bd/uploads/file-manager/b80e5308-8b3b-4453-bbe7-5a2bfcdbc2ed.apk'


@login_required(login_url='login')
def android_application_view(request):
    if (not request.user.is_active
            or get_role(request.user) not in ('user', 'admin')
            or is_full_viewer(request.user)
            or not user_can_access_page(request.user, 'android_application')):
        return render(request, 'monitor/denied.html', status=403)
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    response = render(request, 'monitor/android_application.html', {
        'role': get_role(request.user),
        'apk_url': APK_URL,
    })
    response['Cache-Control'] = 'private, no-store, max-age=0'
    return response
