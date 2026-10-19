from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import transaction
from django.views.decorators.http import require_http_methods
from monitor.views import get_role, get_ip, log_activity
from .models import WetCircuitConfig

FIELDS = (('gbe100', '100GBE'), ('ge10', '10GE (LAN PHY)'), ('stm16', 'STM-16'), ('stm1', 'STM-1'))

@login_required
@require_http_methods(['GET', 'POST'])
def wet_circuit_admin(request):
    if get_role(request.user) != 'admin':
        return HttpResponseForbidden('Administrator access required.')
    config = WetCircuitConfig.objects.filter(pk=1).first()
    if request.method == 'POST':
        values = {}
        for field, label in FIELDS:
            raw = request.POST.get(field, '').strip()
            if not raw.isascii() or not raw.isdecimal() or len(raw) > 8:
                messages.error(request, f'{label}: enter a nonnegative whole number.')
                return redirect('shiftreport:wet_circuits')
            values[field] = int(raw)
        with transaction.atomic():
            config, _ = WetCircuitConfig.objects.select_for_update().get_or_create(pk=1)
            previous = {key: getattr(config, key) for key, _ in FIELDS}
            for key, value in values.items():
                setattr(config, key, value)
            config.updated_by = request.user.get_username()
            config.full_clean()
            config.save()
        log_activity(request.user, 'WET_CIRCUIT_CONFIG',
                     f'WET active circuits changed: {previous} -> {values}', get_ip(request))
        messages.success(request, 'WET active circuit limits saved.')
        return redirect('shiftreport:wet_circuits')
    if config is None:
        config = WetCircuitConfig(pk=1)
    entries = [(label, key, getattr(config, key)) for key, label in FIELDS]
    return render(request, 'shiftreport/wet_circuits.html', {
        'role': get_role(request.user), 'section_active': 'wet', 'entries': entries,
        'total': sum(value for _, _, value in entries),
        'updated_at': config.updated_at, 'updated_by': config.updated_by,
    })
