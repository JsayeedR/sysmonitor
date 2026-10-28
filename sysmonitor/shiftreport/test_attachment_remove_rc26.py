"""RC26: owner-only draft attachment removal, immutable sent and cross-report files."""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from monitor.models import UserProfile
from .models import OptionalReportAttachment, ShiftHandoverContact, ShiftReport


class AttachmentRemovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner=User.objects.create_user('attachment_owner_rc26',password='synthetic')
        cls.other=User.objects.create_user('attachment_other_rc26',password='synthetic')
        cls.guest=User.objects.create_user('attachment_guest_rc26',password='synthetic')
        for user,role in ((cls.owner,'user'),(cls.other,'user'),(cls.guest,'guest')):
            UserProfile.objects.create(user=user,role=role)
        cls.contact=ShiftHandoverContact.objects.create(name='NOC duty',email='noc@example.test')
        start=timezone.now()-timedelta(hours=6)
        cls.draft=ShiftReport.objects.create(
            prepared_by=cls.owner,handover_to=cls.contact,report_date=date(2026,10,28),
            shift='MORNING',shift_start=start,shift_end=start+timedelta(hours=8),status='DRAFT')
        cls.sent=ShiftReport.objects.create(
            prepared_by=cls.owner,handover_to=cls.contact,report_date=date(2026,10,27),
            shift='MORNING',shift_start=start-timedelta(days=1),
            shift_end=start-timedelta(days=1)+timedelta(hours=8),status='SENT',
            main_email_sent_at=timezone.now())
        cls.file=OptionalReportAttachment.objects.create(
            shift_report=cls.draft,name='record.pdf',mime_type='application/pdf',content=b'%PDF-1.4\n')
        cls.sent_file=OptionalReportAttachment.objects.create(
            shift_report=cls.sent,name='sent.pdf',mime_type='application/pdf',content=b'%PDF-1.4\n')

    def url(self,report,attachment):
        return reverse('shiftreport:attachment_remove',args=[report.pk,attachment.pk])

    def test_owner_can_remove_own_unsent_draft_file(self):
        self.client.force_login(self.owner)
        response=self.client.post(self.url(self.draft,self.file))
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['removed'])
        self.assertFalse(OptionalReportAttachment.objects.filter(pk=self.file.pk).exists())
        self.assertTrue(OptionalReportAttachment.objects.filter(pk=self.sent_file.pk).exists())

    def test_other_engineer_cannot_delete_attachment(self):
        self.client.force_login(self.other)
        response=self.client.post(self.url(self.draft,self.file))
        self.assertEqual(response.status_code,403)
        self.assertTrue(OptionalReportAttachment.objects.filter(pk=self.file.pk).exists())

    def test_sent_is_immutable(self):
        self.client.force_login(self.owner)
        response=self.client.post(self.url(self.sent,self.sent_file))
        self.assertEqual(response.status_code,403)
        self.assertTrue(OptionalReportAttachment.objects.filter(pk=self.sent_file.pk).exists())

    def test_cross_report_file_id_is_not_accepted(self):
        self.client.force_login(self.owner)
        response=self.client.post(self.url(self.draft,self.sent_file))
        self.assertEqual(response.status_code,404)
        self.assertTrue(OptionalReportAttachment.objects.filter(pk=self.sent_file.pk).exists())

    def test_get_guest_anonymous_and_post_csrf(self):
        url=self.url(self.draft,self.file)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code,405)
        self.client.force_login(self.guest)
        self.assertEqual(self.client.post(url).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code,302)
        from django.test import Client
        csrf=Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(url).status_code,403)
        self.assertTrue(OptionalReportAttachment.objects.filter(pk=self.file.pk).exists())

    def test_submission_started_disallows_removal(self):
        ShiftReport.objects.filter(pk=self.draft.pk).update(submitted_at=timezone.now())
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url(self.draft,self.file)).status_code,403)
        self.assertTrue(OptionalReportAttachment.objects.filter(pk=self.file.pk).exists())

    def test_delivered_but_not_yet_sent_status_is_locked(self):
        ShiftReport.objects.filter(pk=self.draft.pk).update(main_email_sent_at=timezone.now())
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url(self.draft,self.file)).status_code,403)
        self.assertTrue(OptionalReportAttachment.objects.filter(pk=self.file.pk).exists())


class AttachmentPanelPlacementTests(TestCase):
    def test_panel_occurs_immediately_before_recipient_panel(self):
        from pathlib import Path
        templates=Path(__file__).parent/'templates'/'shiftreport'
        for name, recipient in (('home.html','Email recipients'),('edit.html','Email Recipient Preview')):
            with self.subTest(name=name):
                text=(templates/name).read_text(encoding='utf-8')
                self.assertLess(text.index('Optional Attachments'),text.index(recipient))
                self.assertNotIn('report_attachments',text[:text.index('B. Regular Shift Activities')])
