"""Synthetic signature/archived author regression tests; no outgoing SMTP."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from monitor.models import UserProfile
from monitor.historical_identity import link_all_historical_relations
from shiftreport.models import ShiftHandoverContact, ShiftReport, ShiftReportConfig, MnocPfeReport
from shiftreport.reporting import (capture_delivery_signature, report_signature_html,
                                   build_pfe_html_body)


class ReportSignatureHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('archived-engineer', password='synthetic-only',
                                             first_name='Original', last_name='Engineer')
        UserProfile.objects.create(user=self.user, designation='NOC Engineer', mobile_number='01700000000')
        self.config = ShiftReportConfig.objects.create(pk=1, company_name='Original Company',
                                                        noc_mobile='01234')
        self.contact = ShiftHandoverContact.objects.create(name='Other engineer',
                                                            email='handover@example.test')
        now = timezone.now()
        self.shift = ShiftReport.objects.create(report_date=date(2026,10,19), shift='MORNING',
            shift_start=now-timedelta(hours=12), shift_end=now-timedelta(hours=5),
            prepared_by=self.user, handover_to=self.contact, status='SENT')
        self.pfe = MnocPfeReport.objects.create(report_date=date(2026,10,19),
            prepared_by=self.user, voltage_v=Decimal('12.00'), current_ma=Decimal('13.00'),status='SENT')

    def test_shift_sent_html_survives_profile_change_and_detach(self):
        original = capture_delivery_signature(self.shift, self.config)
        self.assertIn('Original Engineer',original)
        self.shift.sent_signature_html=original
        self.shift.save(update_fields=['sent_signature_html'])
        self.user.first_name='Changed';self.user.save(update_fields=['first_name'])
        self.config.company_name='New Company';self.config.save(update_fields=['company_name'])
        actor, _ = link_all_historical_relations(self.user)
        self.shift.refresh_from_db()
        self.shift.prepared_by = None
        self.assertEqual(report_signature_html(self.shift, self.config),original)
        self.assertNotIn('New Company',report_signature_html(self.shift, self.config))

    def test_pfe_sent_html_used_in_email_body_after_login_detached(self):
        original=capture_delivery_signature(self.pfe, self.config)
        self.pfe.sent_signature_html=original
        self.pfe.save(update_fields=['sent_signature_html'])
        link_all_historical_relations(self.user)
        self.pfe.refresh_from_db()
        self.pfe.prepared_by=None
        html=build_pfe_html_body(self.pfe, self.config)
        self.assertIn(original,html)
        self.assertIn('Original Engineer',html)

    def test_unsnapshotted_legacy_report_reconstructs_but_is_not_claimed_exact(self):
        actor,_=link_all_historical_relations(self.user)
        self.shift.refresh_from_db()
        self.shift.prepared_by=None
        self.assertEqual(self.shift.sent_signature_html,'')
        rendered=report_signature_html(self.shift,self.config)
        self.assertIn(actor.display_name,rendered)
        self.assertIn('NOC Engineer',rendered)
        self.assertIn('01700000000',rendered)
        self.assertEqual(self.shift.sent_signature_html,'')

    def test_html_escape_applies_to_historical_identity(self):
        self.user.first_name='<danger>';self.user.save(update_fields=['first_name'])
        actor,_=link_all_historical_relations(self.user)
        self.pfe.refresh_from_db();self.pfe.prepared_by=None
        html=report_signature_html(self.pfe,self.config)
        self.assertIn('&lt;danger&gt;',html)
        self.assertNotIn('<danger>',html)
