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
from django.utils import timezone

USAGE_IDLE_TIMEOUT = timedelta(minutes=15)
USAGE_MIN_UPDATE_INTERVAL = timedelta(seconds=60)


class UsageTrackingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

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
