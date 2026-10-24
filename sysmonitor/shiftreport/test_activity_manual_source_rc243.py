"""Manual Section C source choice. Isolated Django test DB only."""
from datetime import date, timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from monitor.models import UserProfile
from .models import ShiftHandoverContact, ShiftReport, ShiftReportActivity
from .services import shift_window
from .activity_carryforward import manual_source_rows, find_manual_source
from .views import _validated_activity_rows

class ManualSectionCSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user=User.objects.create_user('rc243engineer', password='test-only-password')
        cls.other=User.objects.create_user('rc243other', password='test-only-password')
        UserProfile.objects.create(user=cls.user,role='user')
        UserProfile.objects.create(user=cls.other,role='user')
        cls.contact=ShiftHandoverContact.objects.create(name='Next engineer',email='test@example.org')

    def report(self, date_value, shift, user=None, status='SENT', activity='Filled'):
        start,end=shift_window(date_value,shift)
        obj=ShiftReport.objects.create(report_date=date_value,shift=shift,
            shift_start=start,shift_end=end,prepared_by=user or self.user,
            handover_to=self.contact,status=status)
        if activity:
            ShiftReportActivity.objects.create(report=obj,activity_type=activity,details='Task',status='Tracking')
        return obj

    def test_last_own_sent_different_from_latest_other(self):
        target=date(2026,10,24)
        own=self.report(target-timedelta(days=4),'NIGHT',activity='My sent')
        recent=self.report(target-timedelta(days=1),'NIGHT',user=self.other,activity='Their submitted')
        self.assertEqual(find_manual_source(target,'MORNING',self.user,'mine'),own)
        self.assertEqual(find_manual_source(target,'MORNING',self.user,'latest'),recent)
        self.assertEqual(manual_source_rows(target,'MORNING',self.user,'mine')[0]['activity_type'],'My sent')

    def test_draft_and_future_never_used(self):
        target=date(2026,10,24)
        self.report(target-timedelta(days=1),'NIGHT',status='DRAFT',activity='Not sent')
        self.report(target,'EVENING',activity='Future')
        self.assertEqual(manual_source_rows(target,'MORNING',self.user,'latest'),[])

    def test_manual_provenance_round_trip_validated(self):
        target=date(2026,10,24)
        self.report(target-timedelta(days=2),'MORNING',activity='Archived row')
        current=self.report(target,'EVENING',status='DRAFT',activity=None)
        rows=manual_source_rows(target,'EVENING',self.user,'mine')
        self.assertTrue(rows and rows[0]['source_reference'])
        self.assertEqual(_validated_activity_rows(current,rows,self.user)[0]['source_reference'],rows[0]['source_reference'])

    def test_preview_rejects_unauthorized_and_bad_mode(self):
        target=date(2026,10,24)
        self.report(target-timedelta(days=1),'NIGHT',activity='Recorded')
        url=reverse('shiftreport:activity_source')
        self.client.force_login(self.user)
        invalid=self.client.get(url,{'date':str(target),'shift':'MORNING','mode':'garbage'})
        self.assertEqual(invalid.status_code,400)
        allowed=self.client.get(url,{'date':str(target),'shift':'MORNING','mode':'mine'})
        self.assertEqual(allowed.status_code,200)
        self.assertEqual(allowed.json()['rows'][0]['activity_type'],'Recorded')
        profile=self.user.userprofile;profile.role='guest';profile.save()
        self.assertEqual(self.client.get(url,{'date':str(target),'shift':'MORNING','mode':'mine'}).status_code,403)

    def test_preview_rejects_sent_report_edit_exclusion(self):
        target=date(2026,10,24)
        sent=self.report(target-timedelta(days=1),'NIGHT',activity='Recorded')
        self.client.force_login(self.user)
        response=self.client.get(reverse('shiftreport:activity_source'),
            {'date':str(target),'shift':'MORNING','mode':'mine','exclude':str(sent.pk)})
        self.assertEqual(response.status_code,403)
