from django.db.models import F
from .models import PageViewCounter


def page_counter(request):
    """Atomically increments the page view counter on every request and
    makes the current value available to all templates as {{ page_view_count }}."""
    PageViewCounter.objects.get_or_create(id=1, defaults={'count': 789})
    PageViewCounter.objects.filter(id=1).update(count=F('count') + 1)
    current = PageViewCounter.objects.get(id=1).count
    return {'page_view_count': current}
