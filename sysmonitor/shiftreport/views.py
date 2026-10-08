from datetime import datetime

import pytz
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from monitor.views import get_ip, get_role, log_activity

from .models import (
    ShiftHandoverContact,
    ShiftImportantIssue,
    ShiftReport,
    ShiftReportConfig,
    MnocPfeReport,
    ShiftReportActivity,
)
from .services import (
    BDT,
    build_previous_day_summary,
    build_shift_snapshot,
    current_shift,
    latest_completed_shift,
    load_shedding_text,
    parse_email_list,
    report_composer_defaults,
    shift_window,
)


def _role_allowed(user):
    return get_role(user) in ('admin', 'user')


def _admin_required(request):
    return get_role(request.user) == 'admin'


def _can_manage_report(user, report):
    if get_role(user) == 'admin':
        return True

    return report.prepared_by_id == user.id


def _config():
    # Reading Shift Report pages must never create DB rows.
    # This is especially important on REMOTE, whose mirrored database
    # should remain read-only for ordinary GET requests.
    row = ShiftReportConfig.objects.filter(pk=1).first()

    if row is not None:
        return row

    # Unsaved default object. A configuration POST will explicitly save it.
    return ShiftReportConfig(pk=1)


def _parse_date(value):
    return datetime.strptime(value, '%Y-%m-%d').date()


def _activity_rows_from_post(request):
    """
    Read Section C repeating rows from the report composer.
    Empty rows are ignored.
    """
    types = request.POST.getlist('activity_type')
    clients = request.POST.getlist('client_vendor')
    details = request.POST.getlist('activity_details')
    statuses = request.POST.getlist('activity_status')
    remarks = request.POST.getlist('activity_remarks')

    length = max(
        len(types),
        len(clients),
        len(details),
        len(statuses),
        len(remarks),
        0,
    )

    rows = []

    for index in range(length):
        row = {
            'activity_type': (
                types[index].strip()
                if index < len(types)
                else ''
            ),
            'client_vendor': (
                clients[index].strip()
                if index < len(clients)
                else ''
            ),
            'details': (
                details[index].strip()
                if index < len(details)
                else ''
            ),
            'status': (
                statuses[index].strip()
                if index < len(statuses)
                else ''
            ),
            'remarks': (
                remarks[index].strip()
                if index < len(remarks)
                else ''
            ),
        }

        if any(row.values()):
            rows.append(row)

    return rows


def _replace_activity_rows(report, rows):
    report.activity_rows.all().delete()

    objects = []

    for index, row in enumerate(rows, start=1):
        objects.append(
            ShiftReportActivity(
                report=report,
                activity_type=row['activity_type'],
                client_vendor=row['client_vendor'],
                details=row['details'],
                status=row['status'],
                remarks=row['remarks'],
                display_order=index * 10,
            )
        )

    if objects:
        ShiftReportActivity.objects.bulk_create(objects)


def _apply_structured_report_fields(report, request):
    report.ac_shifting = request.POST.get(
        'ac_shifting',
        '',
    ).strip()

    report.network_status = (
        request.POST.get('network_status', '').strip()
        or 'Normal'
    )

    report.cable_status = (
        request.POST.get('cable_status', '').strip()
        or 'Normal'
    )

    report.pfe_status = request.POST.get(
        'pfe_status',
        '',
    ).strip()

    report.dwdm_status = (
        request.POST.get('dwdm_status', '').strip()
        or 'Normal'
    )

    report.maintenance_activity = (
        request.POST.get(
            'maintenance_activity',
            '',
        ).strip()
        or 'None'
    )

    report.generator_status_text = request.POST.get(
        'generator_status_text',
        '',
    ).strip()

    report.rain_water_leakage = (
        request.POST.get(
            'rain_water_leakage',
            '',
        ).strip()
        or 'No significant rain was observed'
    )

    report.bandwidth_status = request.POST.get(
        'bandwidth_status',
        '',
    ).strip()


@login_required
def shift_report_home(request):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to access Shift Report.'
        )

    current_date, current_code, current_start, current_end = (
        current_shift()
    )

    due_date, due_code, due_start, due_end = (
        latest_completed_shift()
    )

    contacts = ShiftHandoverContact.objects.filter(
        is_active=True
    ).order_by('display_order', 'name')

    reports_qs = (
        ShiftReport.objects
        .select_related('prepared_by', 'handover_to')
    )

    if get_role(request.user) != 'admin':
        reports_qs = reports_qs.filter(
            prepared_by=request.user
        )

    reports = reports_qs[:40]

    due_existing = (
        ShiftReport.objects
        .select_related('prepared_by')
        .filter(
            report_date=due_date,
            shift=due_code,
        )
        .first()
    )

    composer_defaults = report_composer_defaults(
        due_date,
        due_code,
    )

    issues = (
        ShiftImportantIssue.objects
        .exclude(status='RESOLVED')
        .order_by('-opened_at', '-id')[:20]
    )

    return render(
        request,
        'shiftreport/home.html',
        {
            'role': get_role(request.user),
            'user': request.user,

            'current_report_date': current_date,
            'current_shift': current_code,
            'current_shift_start': current_start,
            'current_shift_end': current_end,

            'due_report_date': due_date,
            'due_shift': due_code,
            'due_shift_start': due_start,
            'due_shift_end': due_end,
            'due_existing': due_existing,

            'composer_defaults': composer_defaults,
            'load_shedding_text': load_shedding_text(
                composer_defaults['snapshot']['outage']
            ),

            'contacts': contacts,
            'reports': reports,
            'important_issues': issues,
            'config': ShiftReportConfig.objects.filter(pk=1).first(),
        },
    )


@login_required
@require_POST
def shift_report_create(request):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to create Shift Reports.'
        )

    report_date, shift, _, _ = latest_completed_shift()

    handover_id = request.POST.get(
        'handover_to',
        '',
    ).strip()

    handover = ShiftHandoverContact.objects.filter(
        id=handover_id,
        is_active=True,
    ).first()

    if handover is None:
        messages.error(
            request,
            'Please select an active handover recipient.'
        )
        return redirect('shiftreport:home')

    existing = (
        ShiftReport.objects
        .select_related('prepared_by')
        .filter(
            report_date=report_date,
            shift=shift,
        )
        .first()
    )

    if existing:
        owner_name = (
            existing.prepared_by.get_full_name().strip()
            or existing.prepared_by.username
        )

        if existing.prepared_by_id == request.user.id:
            messages.info(
                request,
                'You already created this Shift Report.'
            )

            return redirect(
                'shiftreport:edit',
                report_id=existing.id,
            )

        if get_role(request.user) == 'admin':
            messages.info(
                request,
                (
                    'This Shift Report already exists and was '
                    f'created by {owner_name}.'
                )
            )

            return redirect(
                'shiftreport:edit',
                report_id=existing.id,
            )

        messages.error(
            request,
            (
                'This Shift Report has already been created by '
                f'{owner_name}. No second report can be generated.'
            )
        )

        return redirect('shiftreport:home')

    snapshot = build_shift_snapshot(
        report_date,
        shift,
    )

    previous_day = {}

    if shift == 'NIGHT':
        previous_day = build_previous_day_summary(
            report_date
        )

    defaults = report_composer_defaults(
        report_date,
        shift,
    )

    report = ShiftReport.objects.create(
        report_date=report_date,
        shift=shift,
        shift_start=snapshot['start'],
        shift_end=snapshot['end'],
        prepared_by=request.user,
        handover_to=handover,

        ac_shifting=request.POST.get(
            'ac_shifting',
            defaults['ac_shifting'],
        ).strip(),

        network_status=(
            request.POST.get(
                'network_status',
                defaults['network_status'],
            ).strip()
            or 'Normal'
        ),

        cable_status=(
            request.POST.get(
                'cable_status',
                defaults['cable_status'],
            ).strip()
            or 'Normal'
        ),

        pfe_status=request.POST.get(
            'pfe_status',
            defaults['pfe_status'],
        ).strip(),

        dwdm_status=(
            request.POST.get(
                'dwdm_status',
                defaults['dwdm_status'],
            ).strip()
            or 'Normal'
        ),

        maintenance_activity=request.POST.get(
            'maintenance_activity',
            defaults['maintenance_activity'],
        ).strip(),

        generator_status_text=request.POST.get(
            'generator_status_text',
            defaults['generator_status_text'],
        ).strip(),

        rain_water_leakage=request.POST.get(
            'rain_water_leakage',
            defaults['rain_water_leakage'],
        ).strip(),

        bandwidth_status=request.POST.get(
            'bandwidth_status',
            defaults['bandwidth_status'],
        ).strip(),

        regular_activities=request.POST.get(
            'regular_activities',
            '',
        ).strip(),

        issues_observed=request.POST.get(
            'issues_observed',
            '',
        ).strip(),

        pending_handover=request.POST.get(
            'pending_handover',
            '',
        ).strip(),

        important_notes=request.POST.get(
            'important_notes',
            '',
        ).strip(),

        additional_cc=request.POST.get(
            'additional_cc',
            '',
        ).strip(),

        outage_summary=snapshot['outage'],
        generator_summary=snapshot['generator'],
        sensor_summary=snapshot['sensor'],
        previous_day_summary=previous_day,
    )

    _replace_activity_rows(
        report,
        _activity_rows_from_post(request),
    )

    log_activity(
        request.user,
        'SHIFT_REPORT_DRAFT',
        (
            f'Shift Report draft created: '
            f'{report.report_date} {report.shift}, '
            f'handover={handover.name}.'
        )[:300],
        get_ip(request),
    )

    messages.success(
        request,
        'Shift Report draft created successfully.'
    )

    return redirect(
        'shiftreport:edit',
        report_id=report.id,
    )


@login_required
def shift_report_edit(request, report_id):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to access Shift Reports.'
        )

    report = get_object_or_404(
        ShiftReport.objects.select_related(
            'prepared_by',
            'handover_to',
        ),
        id=report_id,
    )

    if not _can_manage_report(request.user, report):
        return HttpResponseForbidden(
            "You cannot access another engineer's Shift Report."
        )

    contacts = ShiftHandoverContact.objects.filter(
        is_active=True
    ).order_by('display_order', 'name')

    cfg = _config()

    mandatory_to = parse_email_list(cfg.mandatory_to)
    mandatory_cc = parse_email_list(cfg.mandatory_cc)
    additional_cc = parse_email_list(report.additional_cc)

    preview_to = []

    for address in (
        [report.handover_to.email]
        + mandatory_to
        + ([cfg.manager_email] if cfg.manager_email else [])
    ):
        address = (address or '').strip()

        if (
            address
            and address.casefold()
            not in {x.casefold() for x in preview_to}
        ):
            preview_to.append(address)

    preview_cc = []

    to_keys = {
        address.casefold()
        for address in preview_to
    }

    for address in mandatory_cc + additional_cc:
        address = (address or '').strip()

        if not address:
            continue

        if address.casefold() in to_keys:
            continue

        if (
            address.casefold()
            not in {x.casefold() for x in preview_cc}
        ):
            preview_cc.append(address)

    return render(
        request,
        'shiftreport/edit.html',
        {
            'role': get_role(request.user),
            'user': request.user,
            'report': report,
            'contacts': contacts,
            'config': cfg,
            'mandatory_cc': mandatory_cc,
            'preview_to': preview_to,
            'preview_cc': preview_cc,
            'gen01_runtime': (
                report.generator_summary or {}
            ).get('Gen-01', '0m'),
            'gen02_runtime': (
                report.generator_summary or {}
            ).get('Gen-02', '0m'),
            'unknown_generator_runtime': (
                report.generator_summary or {}
            ).get('Unknown', '0m'),

            'load_shedding_text': load_shedding_text(
                report.outage_summary
            ),

            'temperature_text': (
                report_composer_defaults(
                    report.report_date,
                    report.shift,
                )['temperature_text']
            ),

            'activity_rows': report.activity_rows.all(),
        },
    )


@login_required
@require_POST
def shift_report_save(request, report_id):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to edit Shift Reports.'
        )

    report = get_object_or_404(
        ShiftReport,
        id=report_id,
    )

    if not _can_manage_report(request.user, report):
        return HttpResponseForbidden(
            "You cannot edit another engineer's Shift Report."
        )

    if report.status == 'SENT':
        messages.error(
            request,
            'A sent Shift Report cannot be edited.'
        )
        return redirect(
            'shiftreport:edit',
            report_id=report.id,
        )

    handover_id = request.POST.get('handover_to', '').strip()

    handover = ShiftHandoverContact.objects.filter(
        id=handover_id,
        is_active=True,
    ).first()

    if handover is None:
        messages.error(
            request,
            'Please select an active handover recipient.'
        )
        return redirect(
            'shiftreport:edit',
            report_id=report.id,
        )

    report.handover_to = handover

    _apply_structured_report_fields(
        report,
        request,
    )

    report.regular_activities = request.POST.get(
        'regular_activities',
        '',
    ).strip()
    report.issues_observed = request.POST.get(
        'issues_observed',
        '',
    ).strip()
    report.pending_handover = request.POST.get(
        'pending_handover',
        '',
    ).strip()
    report.important_notes = request.POST.get(
        'important_notes',
        '',
    ).strip()
    report.additional_cc = request.POST.get(
        'additional_cc',
        '',
    ).strip()
    report.mnoc_notes = request.POST.get(
        'mnoc_notes',
        '',
    ).strip()

    report.save()

    _replace_activity_rows(
        report,
        _activity_rows_from_post(request),
    )

    log_activity(
        request.user,
        'SHIFT_REPORT_DRAFT',
        (
            f'Shift Report draft updated: '
            f'{report.report_date} {report.shift}.'
        )[:300],
        get_ip(request),
    )

    messages.success(
        request,
        'Shift Report draft saved.'
    )

    return redirect(
        'shiftreport:edit',
        report_id=report.id,
    )


@login_required
@require_POST
def shift_report_refresh_summary(request, report_id):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to update Shift Reports.'
        )

    report = get_object_or_404(
        ShiftReport,
        id=report_id,
    )

    if not _can_manage_report(request.user, report):
        return HttpResponseForbidden(
            "You cannot refresh another engineer's Shift Report."
        )

    if report.status == 'SENT':
        messages.error(
            request,
            'A sent Shift Report cannot be refreshed.'
        )
        return redirect(
            'shiftreport:edit',
            report_id=report.id,
        )

    snapshot = build_shift_snapshot(
        report.report_date,
        report.shift,
    )

    report.shift_start = snapshot['start']
    report.shift_end = snapshot['end']
    report.outage_summary = snapshot['outage']
    report.generator_summary = snapshot['generator']
    report.sensor_summary = snapshot['sensor']

    if report.shift == 'NIGHT':
        report.previous_day_summary = (
            build_previous_day_summary(report.report_date)
        )
    else:
        report.previous_day_summary = {}

    report.save()

    messages.success(
        request,
        'Automatic operational summary refreshed.'
    )

    return redirect(
        'shiftreport:edit',
        report_id=report.id,
    )


@login_required
def shift_report_config(request):
    if not _admin_required(request):
        return HttpResponseForbidden(
            'Administrator access required.'
        )

    cfg = _config()

    contacts = ShiftHandoverContact.objects.all()

    return render(
        request,
        'shiftreport/config.html',
        {
            'role': get_role(request.user),
            'user': request.user,
            'config': cfg,
            'contacts': contacts,
        },
    )


@login_required
@require_POST
def shift_report_config_save(request):
    if not _admin_required(request):
        return HttpResponseForbidden(
            'Administrator access required.'
        )

    cfg = _config()

    cfg.noc_from_name = request.POST.get(
        'noc_from_name',
        '',
    ).strip()

    cfg.noc_from_email = request.POST.get(
        'noc_from_email',
        '',
    ).strip()

    cfg.smtp_host = request.POST.get(
        'smtp_host',
        '',
    ).strip()

    try:
        cfg.smtp_port = int(
            request.POST.get('smtp_port', '587')
            or 587
        )
    except ValueError:
        cfg.smtp_port = 587

    cfg.smtp_username = request.POST.get(
        'smtp_username',
        '',
    ).strip()

    new_password = request.POST.get(
        'smtp_password',
        '',
    )

    if new_password:
        cfg.smtp_password = new_password

    cfg.smtp_use_tls = (
        request.POST.get('smtp_use_tls') == 'on'
    )

    cfg.mandatory_to = request.POST.get(
        'mandatory_to',
        '',
    ).strip()

    cfg.manager_email = request.POST.get(
        'manager_email',
        '',
    ).strip()

    cfg.mandatory_cc = request.POST.get(
        'mandatory_cc',
        '',
    ).strip()

    cfg.mnoc_to = request.POST.get(
        'mnoc_to',
        '',
    ).strip()

    cfg.mnoc_cc = request.POST.get(
        'mnoc_cc',
        '',
    ).strip()

    cfg.noc_mobile = request.POST.get(
        'noc_mobile',
        '',
    ).strip()

    cfg.company_name = request.POST.get(
        'company_name',
        '',
    ).strip()

    cfg.website = request.POST.get(
        'website',
        '',
    ).strip()

    cfg.updated_by = request.user.username
    cfg.save()

    log_activity(
        request.user,
        'SHIFT_REPORT_CONFIG',
        'Shift Report email configuration updated.',
        get_ip(request),
    )

    messages.success(
        request,
        'Shift Report configuration saved.'
    )

    return redirect('shiftreport:config')


@login_required
@require_POST
def shift_handover_add(request):
    if not _admin_required(request):
        return HttpResponseForbidden(
            'Administrator access required.'
        )

    name = request.POST.get('name', '').strip()
    designation = request.POST.get('designation', '').strip()
    email = request.POST.get('email', '').strip()

    try:
        display_order = int(
            request.POST.get('display_order', '100').strip()
            or 100
        )
    except ValueError:
        display_order = 100

    if not name or not email:
        messages.error(
            request,
            'Handover contact name and email are required.'
        )
        return redirect('shiftreport:config')

    ShiftHandoverContact.objects.create(
        name=name,
        designation=designation,
        email=email,
        is_active=True,
        display_order=max(display_order, 0),
    )

    log_activity(
        request.user,
        'SHIFT_REPORT_CONFIG',
        f'Shift Report handover contact added: {name} <{email}>.'[:300],
        get_ip(request),
    )

    messages.success(
        request,
        f'Handover contact "{name}" added.'
    )

    return redirect('shiftreport:config')


@login_required
@require_POST
def shift_handover_toggle(request, contact_id):
    if not _admin_required(request):
        return HttpResponseForbidden(
            'Administrator access required.'
        )

    contact = get_object_or_404(
        ShiftHandoverContact,
        id=contact_id,
    )

    contact.is_active = not contact.is_active
    contact.save(update_fields=['is_active'])

    state = 'enabled' if contact.is_active else 'disabled'

    log_activity(
        request.user,
        'SHIFT_REPORT_CONFIG',
        (
            f'Shift Report handover contact '
            f'{contact.name} {state}.'
        )[:300],
        get_ip(request),
    )

    messages.success(
        request,
        f'Handover contact "{contact.name}" {state}.'
    )

    return redirect('shiftreport:config')


@login_required
@require_POST
def shift_issue_add(request):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to add Shift Report issues.'
        )

    from django.utils import timezone

    category_1 = request.POST.get(
        'category_1',
        '',
    ).strip()

    category_2 = request.POST.get(
        'category_2',
        '',
    ).strip()

    details = request.POST.get(
        'details',
        '',
    ).strip()

    remarks = request.POST.get(
        'remarks',
        '',
    ).strip()

    event_type = request.POST.get(
        'event_type',
        '',
    ).strip()

    if not category_1 and not category_2 and not details:
        messages.error(
            request,
            'Historical issue details are required.'
        )
        return redirect('shiftreport:home')

    issue = ShiftImportantIssue.objects.create(
        category_1=category_1,
        category_2=category_2,

        # Retain compatibility with existing views/admin.
        title=(
            category_2
            or category_1
            or 'Historical Issue'
        ),

        description=details,
        remarks=remarks,
        event_type=event_type,

        status=(
            'MONITORING'
            if event_type.casefold() == 'followup'
            else 'OPEN'
        ),

        opened_at=timezone.now(),
        added_by=request.user,
    )

    log_activity(
        request.user,
        'SHIFT_ISSUE_ADD',
        (
            f'Historical Shift issue added: '
            f'{issue.category_1} / {issue.category_2}.'
        )[:300],
        get_ip(request),
    )

    messages.success(
        request,
        'Historical important issue added.'
    )

    return redirect('shiftreport:home')


@login_required
@require_POST
def shift_issue_update(request, issue_id):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to update Shift Report issues.'
        )

    from django.utils import timezone

    issue = get_object_or_404(
        ShiftImportantIssue,
        id=issue_id,
    )

    status = request.POST.get('status', '').strip().upper()

    if status not in ('OPEN', 'MONITORING', 'RESOLVED'):
        messages.error(
            request,
            'Invalid issue status.'
        )
        return redirect('shiftreport:home')

    issue.status = status

    if status == 'RESOLVED':
        issue.resolved_at = timezone.now()
    else:
        issue.resolved_at = None

    issue.save(
        update_fields=[
            'status',
            'resolved_at',
            'updated_at',
        ]
    )

    log_activity(
        request.user,
        'SHIFT_ISSUE_EDIT',
        (
            f'Shift important issue updated: '
            f'{issue.title} -> {status}.'
        )[:300],
        get_ip(request),
    )

    messages.success(
        request,
        'Important issue status updated.'
    )

    return redirect('shiftreport:home')


@login_required
@require_POST
def shift_report_send(request, report_id):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to send Shift Reports.'
        )

    from django.utils import timezone

    from .reporting import (
        XLSX_MIME,
        build_html_body,
        build_mnoc_recipient_lists,
        build_recipient_lists,
        build_shift_report_xlsx,
        build_subject,
        send_smtp_email,
    )

    report = get_object_or_404(
        ShiftReport.objects.select_related(
            'prepared_by',
            'handover_to',
        ),
        id=report_id,
    )

    if not _can_manage_report(request.user, report):
        return HttpResponseForbidden(
            "You cannot send another engineer's Shift Report."
        )

    # Fully successful reports are immutable and cannot be re-sent.
    if report.status == 'SENT':
        messages.info(
            request,
            'This Shift Report has already been sent.'
        )
        return redirect(
            'shiftreport:edit',
            report_id=report.id,
        )

    now = timezone.now()

    if now < report.shift_end:
        messages.error(
            request,
            (
                'Shift Report can only be sent after the shift has ended '
                f'({report.shift_end.astimezone(BDT):%d %b %Y %H:%M}).'
            )
        )
        return redirect(
            'shiftreport:edit',
            report_id=report.id,
        )

    cfg = _config()

    to_list, cc_list = build_recipient_lists(
        report,
        cfg,
    )

    if not report.handover_to.email:
        messages.error(
            request,
            'The selected handover officer has no email address.'
        )
        return redirect(
            'shiftreport:edit',
            report_id=report.id,
        )

    if not cfg.mandatory_to and not cfg.manager_email:
        messages.error(
            request,
            'Mandatory TO recipient is not configured.'
        )
        return redirect(
            'shiftreport:edit',
            report_id=report.id,
        )

    if not cfg.noc_from_email:
        messages.error(
            request,
            'NOC From email is not configured.'
        )
        return redirect(
            'shiftreport:edit',
            report_id=report.id,
        )

    filename, workbook = build_shift_report_xlsx(report)
    subject = build_subject(report)

    report.email_subject = subject
    report.email_to = to_list
    report.email_cc = cc_list
    report.submitted_at = report.submitted_at or now
    report.save(
        update_fields=[
            'email_subject',
            'email_to',
            'email_cc',
            'submitted_at',
            'updated_at',
        ]
    )

    # ------------------------------------------------------
    # NORMAL HANDOVER EMAIL
    # ------------------------------------------------------
    if report.main_email_sent_at is None:
        try:
            send_smtp_email(
                subject=subject,
                html_body=build_html_body(
                    report,
                    cfg,
                ),
                to_list=to_list,
                cc_list=cc_list,
                attachment=(
                    filename,
                    workbook,
                    XLSX_MIME,
                ),
                shift_config=cfg,
            )

        except Exception as exc:
            report.status = 'FAILED'
            report.email_error = (
                f'Normal Shift Report email failed: {exc}'
            )[:2000]
            report.save(
                update_fields=[
                    'status',
                    'email_error',
                    'updated_at',
                ]
            )

            messages.error(
                request,
                f'Shift Report email failed: {exc}'
            )

            return redirect(
                'shiftreport:edit',
                report_id=report.id,
            )

        report.main_email_sent_at = timezone.now()
        report.save(
            update_fields=[
                'main_email_sent_at',
                'updated_at',
            ]
        )

    # ------------------------------------------------------
    # COMPLETE
    # ------------------------------------------------------
    final_time = timezone.now()

    report.status = 'SENT'
    report.sent_at = final_time
    report.email_error = ''

    report.save(
        update_fields=[
            'status',
            'sent_at',
            'email_error',
            'updated_at',
        ]
    )

    log_activity(
        request.user,
        'SHIFT_REPORT_SEND',
        (
            f'Shift Report sent: '
            f'{report.report_date} {report.shift}; '
            f'to={", ".join(to_list)}.'
        )[:300],
        get_ip(request),
    )

    messages.success(
        request,
        'Shift Report sent successfully.'
    )

    return redirect(
        'shiftreport:edit',
        report_id=report.id,
    )


@login_required
def mnoc_pfe_home(request):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to access MNOC PFE Reports.'
        )

    from django.utils import timezone

    today = timezone.localtime().date()

    reports = (
        MnocPfeReport.objects
        .select_related('prepared_by')
        .all()[:60]
    )

    today_report = (
        MnocPfeReport.objects
        .select_related('prepared_by')
        .filter(report_date=today)
        .first()
    )

    return render(
        request,
        'shiftreport/pfe.html',
        {
            'role': get_role(request.user),
            'user': request.user,
            'today': today,
            'reports': reports,
            'today_report': today_report,
            'config': ShiftReportConfig.objects.filter(pk=1).first(),
        },
    )


@login_required
@require_POST
def mnoc_pfe_create(request):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to create MNOC PFE Reports.'
        )

    from decimal import Decimal, InvalidOperation
    from django.utils import timezone

    report_date = timezone.localtime().date()

    existing = (
        MnocPfeReport.objects
        .select_related('prepared_by')
        .filter(report_date=report_date)
        .first()
    )

    if existing:
        owner = (
            existing.prepared_by.get_full_name().strip()
            or existing.prepared_by.username
        )

        messages.error(
            request,
            (
                f'MNOC PFE Report for {report_date:%d %b %Y} '
                f'already exists and was prepared by {owner}.'
            )
        )

        return redirect('shiftreport:pfe_home')

    try:
        voltage = Decimal(
            request.POST.get('voltage_v', '').strip()
        )

        current = Decimal(
            request.POST.get('current_ma', '').strip()
        )

    except (InvalidOperation, ValueError):
        messages.error(
            request,
            'Voltage and Current must be valid numbers.'
        )

        return redirect('shiftreport:pfe_home')

    mode = request.POST.get(
        'mode',
        'CURRENT',
    ).strip().upper()

    if mode not in ('CURRENT', 'VOLTAGE', 'OTHER'):
        mode = 'CURRENT'

    report = MnocPfeReport.objects.create(
        report_date=report_date,
        prepared_by=request.user,
        voltage_v=voltage,
        current_ma=current,
        mode=mode,
        remark=(
            request.POST.get('remark', 'OK').strip()
            or 'OK'
        ),
        alarm_status=(
            request.POST.get('alarm_status', 'None').strip()
            or 'None'
        ),
    )

    messages.success(
        request,
        'MNOC PFE draft created.'
    )

    return redirect(
        'shiftreport:pfe_edit',
        report_id=report.id,
    )


@login_required
def mnoc_pfe_edit(request, report_id):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to access MNOC PFE Reports.'
        )

    report = get_object_or_404(
        MnocPfeReport.objects.select_related('prepared_by'),
        id=report_id,
    )

    can_edit = (
        get_role(request.user) == 'admin'
        or report.prepared_by_id == request.user.id
    )

    return render(
        request,
        'shiftreport/pfe_edit.html',
        {
            'role': get_role(request.user),
            'user': request.user,
            'report': report,
            'can_edit': can_edit,
            'config': ShiftReportConfig.objects.filter(pk=1).first(),
        },
    )


@login_required
@require_POST
def mnoc_pfe_save(request, report_id):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to edit MNOC PFE Reports.'
        )

    from decimal import Decimal, InvalidOperation

    report = get_object_or_404(
        MnocPfeReport,
        id=report_id,
    )

    if (
        get_role(request.user) != 'admin'
        and report.prepared_by_id != request.user.id
    ):
        return HttpResponseForbidden(
            "You cannot edit another engineer's MNOC PFE Report."
        )

    if report.status == 'SENT':
        messages.error(
            request,
            'A sent MNOC PFE Report cannot be edited.'
        )

        return redirect(
            'shiftreport:pfe_edit',
            report_id=report.id,
        )

    try:
        report.voltage_v = Decimal(
            request.POST.get('voltage_v', '').strip()
        )

        report.current_ma = Decimal(
            request.POST.get('current_ma', '').strip()
        )

    except (InvalidOperation, ValueError):
        messages.error(
            request,
            'Voltage and Current must be valid numbers.'
        )

        return redirect(
            'shiftreport:pfe_edit',
            report_id=report.id,
        )

    mode = request.POST.get(
        'mode',
        'CURRENT',
    ).strip().upper()

    if mode not in ('CURRENT', 'VOLTAGE', 'OTHER'):
        mode = 'CURRENT'

    report.mode = mode

    report.remark = (
        request.POST.get('remark', 'OK').strip()
        or 'OK'
    )

    report.alarm_status = (
        request.POST.get('alarm_status', 'None').strip()
        or 'None'
    )

    report.save()

    messages.success(
        request,
        'MNOC PFE draft saved.'
    )

    return redirect(
        'shiftreport:pfe_edit',
        report_id=report.id,
    )


@login_required
@require_POST
def mnoc_pfe_send(request, report_id):
    if not _role_allowed(request.user):
        return HttpResponseForbidden(
            'You do not have permission to send MNOC PFE Reports.'
        )

    from django.utils import timezone

    from .reporting import (
        build_mnoc_recipient_lists,
        build_pfe_html_body,
        build_pfe_subject,
        send_smtp_email,
    )

    report = get_object_or_404(
        MnocPfeReport.objects.select_related('prepared_by'),
        id=report_id,
    )

    if (
        get_role(request.user) != 'admin'
        and report.prepared_by_id != request.user.id
    ):
        return HttpResponseForbidden(
            "You cannot send another engineer's MNOC PFE Report."
        )

    if report.status == 'SENT':
        messages.info(
            request,
            'This MNOC PFE Report has already been sent.'
        )

        return redirect(
            'shiftreport:pfe_edit',
            report_id=report.id,
        )

    cfg = _config()

    if not cfg.noc_from_email:
        messages.error(
            request,
            'NOC From email is not configured.'
        )

        return redirect(
            'shiftreport:pfe_edit',
            report_id=report.id,
        )

    to_list, cc_list = build_mnoc_recipient_lists(cfg)

    if not to_list:
        messages.error(
            request,
            'MNOC PFE mandatory TO recipient is not configured.'
        )

        return redirect(
            'shiftreport:pfe_edit',
            report_id=report.id,
        )

    subject = build_pfe_subject()

    report.email_subject = subject
    report.email_to = to_list
    report.email_cc = cc_list

    report.save(
        update_fields=[
            'email_subject',
            'email_to',
            'email_cc',
            'updated_at',
        ]
    )

    try:
        send_smtp_email(
            subject=subject,
            html_body=build_pfe_html_body(
                report,
                cfg,
            ),
            to_list=to_list,
            cc_list=cc_list,
            shift_config=cfg,
        )

    except Exception as exc:
        report.status = 'FAILED'
        report.email_error = str(exc)[:2000]

        report.save(
            update_fields=[
                'status',
                'email_error',
                'updated_at',
            ]
        )

        messages.error(
            request,
            f'MNOC PFE email failed: {exc}'
        )

        return redirect(
            'shiftreport:pfe_edit',
            report_id=report.id,
        )

    report.status = 'SENT'
    report.sent_at = timezone.now()
    report.email_error = ''

    report.save(
        update_fields=[
            'status',
            'sent_at',
            'email_error',
            'updated_at',
        ]
    )

    log_activity(
        request.user,
        'SHIFT_REPORT_SEND',
        (
            f'MNOC PFE Report sent: '
            f'{report.report_date}; '
            f'to={", ".join(to_list)}.'
        )[:300],
        get_ip(request),
    )

    messages.success(
        request,
        'MNOC PFE Report sent successfully.'
    )

    return redirect(
        'shiftreport:pfe_edit',
        report_id=report.id,
    )
