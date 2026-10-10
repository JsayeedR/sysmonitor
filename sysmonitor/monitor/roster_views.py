from datetime import datetime

from django.contrib import messages
from django.db.models import Count
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header

from .models import DutyRoster
from .roster_service import (
    RosterImportError,
    build_roster_template,
    import_roster,
    resolve_current_and_next,
)
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
        'template_month': timezone.localdate().strftime('%Y-%m'),
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



@role_required('admin')
def duty_roster_download(request, roster_id):
    roster = get_object_or_404(DutyRoster, pk=roster_id)

    if not roster.original_file:
        raise Http404(
            'The original workbook was not retained for this older import. '
            'Re-upload the roster once to enable downloading.'
        )

    filename = roster.source_filename or (
        f'Roster_{roster.month:%Y_%m}.xlsx'
    )

    response = HttpResponse(
        bytes(roster.original_file),
        content_type=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        ),
    )
    response['Content-Disposition'] = content_disposition_header(
        True,
        filename,
    )
    response['Content-Length'] = str(roster.original_size)
    return response


@role_required('admin')
def duty_roster_template_download(request):
    raw_month = (request.GET.get('month') or '').strip()

    if raw_month:
        try:
            month = datetime.strptime(raw_month, '%Y-%m').date().replace(day=1)
        except ValueError:
            messages.error(
                request,
                'Invalid template month. Use YYYY-MM.',
            )
            return redirect('duty_roster_manage')
    else:
        month = timezone.localdate().replace(day=1)

    file_bytes = build_roster_template(month)
    filename = f'NOC_Duty_Roster_Template_{month:%Y_%m}.xlsx'

    response = HttpResponse(
        file_bytes,
        content_type=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        ),
    )
    response['Content-Disposition'] = content_disposition_header(
        True,
        filename,
    )
    response['Content-Length'] = str(len(file_bytes))
    return response



@role_required('viewer', 'user', 'admin')
def duty_roster_current(request):
    state = resolve_current_and_next()
    return JsonResponse({'ok': True, **state})
