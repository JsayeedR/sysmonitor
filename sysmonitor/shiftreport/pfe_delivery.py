"""MNOC PFE shared manual/scheduled mailer. Never auto-retry uncertain SMTP deliveries."""
from datetime import datetime, time
from django.db import transaction
from django.utils import timezone
from .models import MnocPfeReport, ShiftReportConfig
from .services import BDT
from .reporting import build_mnoc_recipient_lists, build_pfe_html_body, build_pfe_subject, send_smtp_email


def schedule_pfe(report, send_at):
    if timezone.is_naive(send_at):
        raise ValueError('Please select a valid Bangladesh date and time.')
    now = timezone.now()
    if send_at <= now:
        raise ValueError('Scheduled sending must be in the future.')
    if timezone.localtime(send_at, BDT).date() < report.report_date:
        raise ValueError('Cannot schedule sending before the PFE reading date.')
    updated = MnocPfeReport.objects.filter(pk=report.pk, status__in=['DRAFT', 'FAILED', 'SCHEDULED']).update(
        status='SCHEDULED', scheduled_send_at=send_at, email_error='',
    )
    if not updated:
        raise ValueError('Report is already sent or currently being sent.')


def deliver_pfe(report_id, actor=None, ip='', scheduled_only=False):
    now = timezone.now()
    # Atomic conditional claim prevents two workers or manual+scheduler from sending together.
    eligible = MnocPfeReport.objects.filter(pk=report_id)
    if scheduled_only:
        eligible = eligible.filter(status='SCHEDULED', scheduled_send_at__lte=now)
    else:
        eligible = eligible.filter(status__in=['DRAFT', 'FAILED', 'SCHEDULED'])
    claimed = eligible.update(status='SENDING', scheduled_send_at=None)
    if not claimed:
        raise ValueError('PFE email already sent, sending, or no longer scheduled.')

    report = MnocPfeReport.objects.select_related('prepared_by').get(pk=report_id)
    # Protect against future reading date even if scheduled earlier and the date was edited.
    if timezone.localtime(now, BDT).date() < report.report_date:
        MnocPfeReport.objects.filter(pk=report_id, status='SENDING').update(status='DRAFT')
        raise ValueError('A future-dated MNOC PFE Report cannot be sent.')

    # Import lazily to avoid circular dependencies with views.
    from .views import _config, _report_event
    from monitor.views import log_activity
    cfg = _config()
    to_list, cc_list = build_mnoc_recipient_lists(cfg)
    if not cfg.noc_from_email or not to_list:
        MnocPfeReport.objects.filter(pk=report_id, status='SENDING').update(status='FAILED', email_error='MNOC sender or TO recipients not configured.')
        raise ValueError('MNOC sender or TO recipients not configured.')
    subject = build_pfe_subject()
    MnocPfeReport.objects.filter(pk=report_id, status='SENDING').update(
        email_subject=subject, email_to=to_list, email_cc=cc_list,
    )
    # Persist the signature before SMTP; a profile change cannot rewrite it
    # after delivery. A failed attempt is NOT evidence of successful sending.
    from .reporting import capture_delivery_signature
    if not report.sent_signature_html:
        report.sent_signature_html = capture_delivery_signature(report, cfg)
        MnocPfeReport.objects.filter(pk=report_id, status='SENDING').update(
            sent_signature_html=report.sent_signature_html,
        )
    try:
        send_smtp_email(
            subject=subject, html_body=build_pfe_html_body(report, cfg),
            to_list=to_list, cc_list=cc_list, shift_config=cfg,
            attachments=__import__('shiftreport.optional_attachments', fromlist=['outgoing_attachments']).outgoing_attachments(report),
        )
    except Exception as exc:
        MnocPfeReport.objects.filter(pk=report_id, status='SENDING').update(
            status='FAILED', email_error=str(exc)[:2000],
        )
        _report_event('NOTICE', f'MNOC PFE email failed for {report.report_date} (see report status for details)')
        raise
    MnocPfeReport.objects.filter(pk=report_id, status='SENDING').update(
        status='SENT', sent_at=timezone.now(), email_error='',
    )
    engineer = actor or report.prepared_by
    _report_event('INFO', f'MNOC PFE Report sent successfully: {report.report_date}; engineer={engineer.username}')
    log_activity(engineer, 'SHIFT_REPORT_SEND',
                 f'MNOC PFE Report sent: {report.report_date}; to={", ".join(to_list)}.'[:300], ip)
    return report_id
