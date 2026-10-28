"""RC26 NOC SENT Historical source selector — isolated synthetic test DB."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from monitor.models import UserProfile
from .models import HistoricalReportRevision, ShiftHandoverContact


class HistoricalSentSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user('history_me', email='me@example.test',password='synthetic')
        cls.fuad = User.objects.create_user('history_fuad', email='fuad@example.test',password='synthetic')
        cls.nafis = User.objects.create_user('history_nafis', email='nafis@example.test',password='synthetic')
        cls.guest = User.objects.create_user('history_guest', email='guest@example.test',password='synthetic')
        for user,role in ((cls.me,'user'),(cls.fuad,'user'),(cls.nafis,'user'),(cls.guest,'guest')):
            UserProfile.objects.create(user=user,role=role)
        cls.fuad_contact = ShiftHandoverContact.objects.create(
            name='Fuad NOC',email='fuad@example.test',is_active=True)
        cls.nafis_contact = ShiftHandoverContact.objects.create(
            name='Nafis NOC',email='different@example.test',user=cls.nafis,is_active=True)
        cls.fuad_sent = HistoricalReportRevision.objects.create(
            owner=cls.fuad,created_by=cls.fuad,revision_number=1,status='SENT',
            rows=[dict(category_1='NOC',category_2='Power',details='Fuad latest',remarks='Reviewed',event_type='Followup')],
            sent_at=timezone.now()-timedelta(hours=1))
        cls.fuad_draft = HistoricalReportRevision.objects.create(
            owner=cls.fuad,created_by=cls.fuad,revision_number=2,status='DRAFT',
            rows=[dict(details='SECRET DRAFT')])
        cls.nafis_sent = HistoricalReportRevision.objects.create(
            owner=cls.nafis,created_by=cls.nafis,revision_number=1,status='SENT',
            rows=[dict(category_1='NOC',details='Nafis history')],sent_at=timezone.now())

    def test_default_own_source_and_visible_configured_names(self):
        self.client.force_login(self.me)
        response=self.client.get(reverse('shiftreport:historical_home'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'value="mine"')
        self.assertContains(response,'Fuad NOC')
        self.assertContains(response,'Nafis NOC')
        self.assertContains(response,'Last updated (BDT)')
        self.assertContains(response,'Save Draft')

    def test_email_based_source_fetches_only_sent_not_draft(self):
        self.client.force_login(self.me)
        response=self.client.get(reverse('shiftreport:historical_source_preview'),
                                 {'source':f'contact:{self.fuad_contact.pk}'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['rows'][0]['details'],'Fuad latest')
        self.assertNotIn('SECRET DRAFT',str(response.json()))
        self.assertEqual(response.json()['name'],'Fuad NOC')
        self.assertTrue(response.json()['updated_at'])
        self.assertIn('no-store',response['Cache-Control'])
        self.fuad_sent.refresh_from_db()
        self.assertEqual(self.fuad_sent.status,'SENT')
        self.assertEqual(self.fuad_sent.rows[0]['details'],'Fuad latest')

    def test_explicit_link_wins_over_mismatched_email(self):
        self.client.force_login(self.me)
        r=self.client.get(reverse('shiftreport:historical_source_preview'),
                          {'source':f'contact:{self.nafis_contact.pk}'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['rows'][0]['details'],'Nafis history')

    def test_missing_own_sent_returns_404_without_changes(self):
        self.client.force_login(self.me)
        before=HistoricalReportRevision.objects.count()
        r=self.client.get(reverse('shiftreport:historical_source_preview'),{'source':'mine'})
        self.assertEqual(r.status_code,404)
        self.assertEqual(HistoricalReportRevision.objects.count(),before)

    def test_invalid_inactive_ambiguous_and_guest_denied(self):
        self.client.force_login(self.me)
        url=reverse('shiftreport:historical_source_preview')
        self.assertEqual(self.client.get(url,{'source':'contact:999999'}).status_code,400)
        self.assertEqual(self.client.get(url,{'source':'anything'}).status_code,400)
        self.fuad_contact.is_active=False;self.fuad_contact.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(url,{'source':f'contact:{self.fuad_contact.pk}'}).status_code,400)
        self.fuad_contact.is_active=True;self.fuad_contact.save(update_fields=['is_active'])
        User.objects.create_user('duplicate',email='fuad@example.test',password='synthetic')
        self.assertEqual(self.client.get(url,{'source':f'contact:{self.fuad_contact.pk}'}).status_code,404)
        self.client.force_login(self.guest)
        self.assertEqual(self.client.get(url,{'source':'mine'}).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(url,{'source':'mine'}).status_code,302)

    def test_get_uses_no_draft_writes(self):
        self.client.force_login(self.me)
        count=HistoricalReportRevision.objects.count()
        self.client.get(reverse('shiftreport:historical_source_preview'),
                        {'source':f'contact:{self.nafis_contact.pk}'})
        self.assertEqual(HistoricalReportRevision.objects.count(),count)
