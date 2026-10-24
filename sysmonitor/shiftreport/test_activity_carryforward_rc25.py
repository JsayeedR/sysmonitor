"""RC24.2 Section C carry-forward tests; no SMTP, no production DB."""
from datetime import date, timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from .activity_carryforward import carry_forward_rows, find_source, previous_slots
from .models import ShiftHandoverContact, ShiftReport, ShiftReportActivity
from .services import shift_window
from .views import _validated_activity_rows
from .reporting import _activity_rows_html


class ShiftActivitySourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.engineer = User.objects.create_user(username='engineer_rc25', password='TestPass!1')
        cls.other = User.objects.create_user(username='other_rc25', password='TestPass!1')
        cls.contact = ShiftHandoverContact.objects.create(name='Next engineer', email='test@example.org')

    def report(self, when, shift, author=None, status='SENT', activity=None):
        start, end = shift_window(when, shift)
        obj = ShiftReport.objects.create(
            report_date=when, shift=shift, shift_start=start, shift_end=end,
            prepared_by=author or self.engineer, handover_to=self.contact,
            status=status,
        )
        if activity:
            ShiftReportActivity.objects.create(report=obj, activity_type=activity,
                                              details='Escalated incident', status='Monitoring')
        return obj

    def test_previous_two_slots_order(self):
        self.assertEqual(list(previous_slots(date(2026, 10, 24), 'MORNING')),
            [(date(2026, 10, 23), 'NIGHT'),
             (date(2026, 10, 23), 'EVENING')])

    def test_closest_submitted_shift_wins_even_for_other_engineer(self):
        target=date(2026,10,24)
        self.report(date(2026,10,23), 'EVENING', activity='Older')
        recent=self.report(date(2026,10,23), 'NIGHT', self.other,
                           activity='More recent')
        rows=carry_forward_rows(target,'MORNING',self.engineer)
        self.assertEqual(rows[0]['activity_type'],'More recent')
        self.assertIn('Night Shift',rows[0]['source_reference'])
        self.assertIn('other_rc25',rows[0]['source_reference'])
        self.assertEqual(find_source(target,'MORNING',self.engineer),recent)

    def test_empty_and_draft_previous_slots_skipped(self):
        target=date(2026,10,24)
        self.report(date(2026,10,23),'NIGHT',status='DRAFT',activity='DraftOnly')
        second=self.report(date(2026,10,23),'EVENING',activity='Second')
        self.assertEqual(find_source(target,'MORNING',self.engineer),second)

    def test_fallback_to_old_engineer_sent_report_beyond_three_days(self):
        target=date(2026,10,24)
        old=self.report(target-timedelta(days=12),'MORNING',activity='Old own row')
        self.report(target-timedelta(days=3),'NIGHT',activity=None)
        self.assertEqual(find_source(target,'MORNING',self.engineer),old)

    def test_no_future_report_used(self):
        target=date(2026,10,24)
        self.report(target,'EVENING',activity='Future')
        self.assertIsNone(find_source(target,'MORNING',self.engineer))

    def test_report_email_does_not_include_source_metadata(self):
        report=self.report(date(2026,10,24),'MORNING',status='DRAFT')
        ShiftReportActivity.objects.create(
            report=report,activity_type='Incident',details='Data preserved',
            source_reference='DO-NOT-EXPOSE-THIS-SOURCE')
        html=_activity_rows_html(report)
        self.assertIn('Data preserved',html)
        self.assertNotIn('DO-NOT-EXPOSE-THIS-SOURCE',html)

    def test_untrusted_provenance_is_discarded(self):
        report=self.report(date(2026,10,24),'MORNING',status='DRAFT')
        data=[dict(activity_type='Manual',client_vendor='',details='',
                   status='',remarks='',source_reference='Fabricated provenance')]
        checked=_validated_activity_rows(report,data,self.engineer)
        self.assertEqual(checked[0]['source_reference'],'')

    def test_real_copied_provenance_is_accepted(self):
        target=date(2026,10,24)
        self.report(date(2026,10,23),'NIGHT',activity='Carry')
        report=self.report(target,'MORNING',status='DRAFT')
        rows=carry_forward_rows(target,'MORNING',self.engineer)
        checked=_validated_activity_rows(report,rows,self.engineer)
        self.assertEqual(checked[0]['source_reference'],rows[0]['source_reference'])
