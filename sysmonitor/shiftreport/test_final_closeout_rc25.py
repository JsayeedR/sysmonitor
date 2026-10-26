"""RC25 closeout: synthetic isolated Django DB regressions (never production)."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from monitor.models import UserProfile
from .models import ShiftHandoverContact, ShiftReport
from .services import build_generator_runtime_log, format_circuit_bandwidth, shift_window


class CanonicalGeneratorLogTests(SimpleTestCase):
    @patch('monitor.daily_summary.build_daily_summary')
    def test_night_log_uses_identical_daily_email_rows(self, daily):
        daily.return_value = {
            'date': '25/10/2026', 'has_data': True,
            'rows': [
                {'start':'10:25:20 PM','end':'10:38:02 PM','duration_sec':762,
                 'generator':'Gen-01','is_ongoing':False},
                {'start':'10:38:00 PM','end':'11:01:00 PM','duration_sec':1380,
                 'generator':'Gen-01','is_ongoing':False},
            ],
            'totals': {'Gen-01':2142,'Gen-02':0,'UNASSIGNED':0},
            'grand_total':2142,
        }
        from .services import localize
        from datetime import datetime, time, timedelta
        begin=localize(datetime.combine(date(2026,10,25),time.min))
        end=localize(datetime.combine(date(2026,10,26),time.min))
        result=build_generator_runtime_log(begin,end)
        daily.assert_called_once_with(date(2026,10,25))
        self.assertEqual([r['duration'] for r in result['rows']], ['12m 42s','23m 00s'])
        self.assertEqual(result['Gen-01'], '35m 42s')
        self.assertEqual(result['grand_total'], '35m 42s')
        self.assertEqual(result['total_seconds'], 2142)

    @patch('monitor.daily_summary.build_daily_summary')
    def test_non_calendar_day_window_is_rejected(self, daily):
        from .services import localize
        from datetime import datetime
        begin=localize(datetime(2026,10,25,1))
        end=localize(datetime(2026,10,26,0))
        with self.assertRaises(ValueError):
            build_generator_runtime_log(begin,end)
        daily.assert_not_called()


class WetCapacityCalculationTests(SimpleTestCase):
    @patch('shiftreport.services.wet_circuit_limits', return_value=(20,72,1,16))
    def test_capacity_uses_configured_wet_maximum(self, _limits):
        text=format_circuit_bandwidth((19,51,1,7))
        self.assertIn('Used Capacity        : 2,413.58 Gbps (51.90%)',text)
        self.assertIn('Assigned, No Traffic :',text)
        self.assertIn('Assigned Capacity    : 2,724.98 Gbps',text)
        self.assertIn('Carrying Traffic     : 78',text)

    @patch('shiftreport.services.wet_circuit_limits', return_value=(20,73,1,16))
    def test_one_extra_max_10g_increases_only_assigned_capacity(self, _limits):
        text=format_circuit_bandwidth((19,51,1,7))
        self.assertIn('Assigned Capacity    : 2,734.98 Gbps',text)
        self.assertIn('Used Capacity        : 2,413.58 Gbps (51.90%)',text)
        self.assertIn('Carrying Traffic     : 78',text)


class SentReportReadOnlyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author=User.objects.create_user('closeout_author',password='synthetic-test')
        cls.colleague=User.objects.create_user('closeout_colleague',password='synthetic-test')
        for u in (cls.author,cls.colleague):
            UserProfile.objects.create(user=u,role='user')
        cls.contact=ShiftHandoverContact.objects.create(name='Synthetic Contact',email='test@example.org')
        start,end=shift_window(date(2026,10,25),'NIGHT')
        cls.sent=ShiftReport.objects.create(report_date=date(2026,10,25),shift='NIGHT',
            shift_start=start,shift_end=end,prepared_by=cls.author,
            handover_to=cls.contact,status='SENT')
        cls.draft=ShiftReport.objects.create(report_date=date(2026,10,24),shift='EVENING',
            shift_start=shift_window(date(2026,10,24),'EVENING')[0],
            shift_end=shift_window(date(2026,10,24),'EVENING')[1],
            prepared_by=cls.author,handover_to=cls.contact,status='DRAFT')

    def test_author_and_colleague_both_use_same_sent_view(self):
        edit=reverse('shiftreport:edit',args=[self.sent.pk])
        view=reverse('shiftreport:view',args=[self.sent.pk])
        for user in (self.author,self.colleague):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response=self.client.get(edit)
                self.assertRedirects(response,view,fetch_redirect_response=False)
                self.assertEqual(self.client.get(view).status_code,200)

    def test_sent_save_and_refresh_cannot_modify_snapshot(self):
        self.client.force_login(self.author)
        for route in ('save','refresh_summary'):
            with self.subTest(route=route):
                resp=self.client.post(reverse('shiftreport:'+route,args=[self.sent.pk]),
                                      {'bandwidth_status':'FAKE'})
                self.assertRedirects(resp,reverse('shiftreport:view',args=[self.sent.pk]),
                                     fetch_redirect_response=False)
                self.sent.refresh_from_db()
                self.assertEqual(self.sent.status,'SENT')
                self.assertNotEqual(self.sent.bandwidth_status,'FAKE')

    def test_draft_remains_editable_by_author(self):
        self.client.force_login(self.author)
        # Rendering requires live telemetry, which is mocked for the unit test.
        from unittest.mock import patch
        with patch('shiftreport.views._a91_refresh_unsent_operational_snapshot',return_value=False):
            response=self.client.get(reverse('shiftreport:edit',args=[self.draft.pk]))
        self.assertEqual(response.status_code,200)
        self.assertTemplateUsed(response,'shiftreport/edit.html')
