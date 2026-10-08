"""
monitor/middleware.py
──────────────────────
UsageTrackingMiddleware accumulates a running total of "active" time per
user, purely from normal page/API requests they already make — no extra
JS or heartbeat needed.

How it works: on each authenticated request, we look at how long it's
been since that user's last request. If the gap is under USAGE_IDLE_TIMEOUT,
we add that gap to their cumulative total (they were presumably actively
using the app the whole time). If the gap is longer, we assume they were
away/closed the tab and don't count that idle time.

To avoid a DB write on every single request, updates are throttled to at
most once per USAGE_MIN_UPDATE_INTERVAL — the accumulated gap is still
exact, we just batch several requests into one write.
"""

from datetime import timedelta
from django.conf import settings
from django.utils import timezone

USAGE_IDLE_TIMEOUT = timedelta(minutes=15)
USAGE_MIN_UPDATE_INTERVAL = timedelta(seconds=60)


class UsageTrackingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if getattr(settings, "IS_MIRROR", False):
            return response          # the remote never writes (usage is tracked on the master)

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            try:
                profile = user.userprofile
            except Exception:
                profile = None

            if profile is not None:
                now = timezone.now()
                last = profile.last_activity_at

                if last is None or (now - last) >= USAGE_MIN_UPDATE_INTERVAL:
                    if last is not None and (now - last) < USAGE_IDLE_TIMEOUT:
                        gap_seconds = int((now - last).total_seconds())
                        profile.total_usage_seconds = (profile.total_usage_seconds or 0) + gap_seconds
                    profile.last_activity_at = now
                    profile.save(update_fields=["total_usage_seconds", "last_activity_at"])

        return response


# Paths a user with must_change_password=True is still allowed to hit —
# otherwise they'd be stuck unable to even load the change-password page,
# its save endpoint, static assets, or log out.
_PASSWORD_CHANGE_ALLOWED_PREFIXES = (
    '/profile/password',
    '/logout',
    '/static/',
    '/media/',
)


class ForcePasswordChangeMiddleware:
    """
    After a "Forgot password" reset, the account is logged in with a
    temporary password (see views.password_reset_request). This middleware
    redirects every request straight to the change-password page until the
    user sets a real one, so a temporary password can't linger in use.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            if not any(request.path_info.startswith(p) for p in _PASSWORD_CHANGE_ALLOWED_PREFIXES):
                try:
                    profile = user.userprofile
                except Exception:
                    profile = None
                if profile is not None and profile.must_change_password:
                    from django.shortcuts import redirect
                    return redirect('profile_password')
        return self.get_response(request)


class PageAccessMiddleware:
    """
    Enforces individual page restrictions after normal role permissions.

    This middleware never grants access. It only blocks a page that the user's
    role would otherwise be allowed to use.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)

        if user is not None and user.is_authenticated:
            # The Page Access administration screen is intentionally
            # non-hideable so administrators always have a recovery route.
            if not request.path_info.startswith('/page-access/'):
                from django.shortcuts import render
                from .page_access import (
                    page_key_for_path,
                    user_can_access_page,
                )

                key = page_key_for_path(request.path_info)

                if key and not user_can_access_page(user, key):
                    return render(
                        request,
                        'monitor/denied.html',
                        status=403,
                    )

        return self.get_response(request)
