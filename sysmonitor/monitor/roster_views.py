from django.contrib import messages
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect, render

from .models import DutyRoster
from .roster_service import RosterImportError, import_roster, resolve_current_and_next
from .views import get_ip, log_activity, role_required


MAX_ROSTER_BYTES = 5 * 1024 * 1024


@role_required('admin')
def duty_roster_manage(request):
    rosters = (
        DutyRoster.objects
        .annotate(assignment_count=Count('assignments'))
        .select_related('imported_by')
        .order_by('-month')[:18]
    )
    return render(request, 'monitor/duty_roster.html', {
        'role': 'admin',
        'rosters': rosters,
        'duty_state': resolve_current_and_next(),
    })


@role_required('admin')
def duty_roster_upload(request):
    if request.method != 'POST':
        return redirect('duty_roster_manage')

    upload = request.FILES.get('roster_file')
    if upload is None:
        messages.error(request, 'Choose an .xlsx roster file first.')
        return redirect('duty_roster_manage')

    if not upload.name.lower().endswith('.xlsx'):
        messages.error(request, 'Only .xlsx roster files are accepted.')
        return redirect('duty_roster_manage')

    if upload.size > MAX_ROSTER_BYTES:
        messages.error(request, 'Roster file is too large (maximum 5 MB).')
        return redirect('duty_roster_manage')

    try:
        roster, created, parsed = import_roster(
            upload.read(), upload.name, request.user,
        )
    except RosterImportError as exc:
        messages.error(request, f'Roster was not imported: {exc}')
        return redirect('duty_roster_manage')
    except Exception:
        messages.error(
            request,
            'Roster import failed safely. Existing roster data was not changed.',
        )
        return redirect('duty_roster_manage')

    verb = 'uploaded' if created else 'replaced'
    messages.success(
        request,
        f'{roster.month:%B %Y} roster {verb}: '
        f'{parsed["engineer_count"]} engineers, '
        f'{len(parsed["assignments"])} duty entries.',
    )

    if parsed['warnings']:
        messages.warning(
            request,
            f'Imported with {len(parsed["warnings"])} warning(s). '
            'Review the roster summary below.',
        )

    log_activity(
        request.user,
        'ROSTER_UPLOAD',
        (
            f'Duty roster {verb} for {roster.month:%B %Y}; '
            f'{parsed["engineer_count"]} engineers, '
            f'{len(parsed["assignments"])} entries.'
        )[:300],
        get_ip(request),
    )
    return redirect('duty_roster_manage')


@role_required('viewer', 'user', 'admin')
def duty_roster_current(request):
    state = resolve_current_and_next()
    return JsonResponse({'ok': True, **state})
