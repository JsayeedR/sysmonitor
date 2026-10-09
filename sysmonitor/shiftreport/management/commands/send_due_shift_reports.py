"""Opt-in, cron-compatible scheduled sending of completed shift drafts."""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.test import RequestFactory
from shiftreport.models import ShiftReport
from shiftreport.views import shift_report_send

class Command(BaseCommand):
    help = 'Inspect completed drafts; --send dispatches via existing validated sender (opt in only).'
    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true', help='Actually deliver eligible reports.')
        parser.add_argument('--report-id', type=int, help='Only this selected shift report (required for --send).')
    def handle(self, *args, **opts):
        now = timezone.now()
        qs = ShiftReport.objects.filter(auto_send_after_shift=True,status='DRAFT',shift_end__lte=now,main_email_sent_at__isnull=True).select_related('prepared_by').order_by('shift_end')
        if opts['report_id']:
            qs = qs.filter(pk=opts['report_id'])
        self.stdout.write(f'Eligible shift drafts: {qs.count()}')
        if not opts['send']:
            self.stdout.write('Dry run only. --send delivers ONLY explicitly opted-in drafts after shift end.')
            return
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.contrib.messages.storage.fallback import FallbackStorage
        factory=RequestFactory()
        for report in qs:
            request=factory.post('/shift-report/%s/send/' % report.pk)
            request.user=report.prepared_by
            SessionMiddleware(lambda r: None).process_request(request)
            request.session.save()
            setattr(request,'_messages',FallbackStorage(request))
            try:
                response=shift_report_send(request,report.pk)
                report.refresh_from_db()
                self.stdout.write(f'Report {report.pk}: status={report.status}, HTTP={response.status_code}')
            except Exception as exc:
                self.stderr.write(f'Report {report.pk}: send error: {exc}')
