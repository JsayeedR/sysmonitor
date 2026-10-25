"""RC24.8 UI and Full Viewer guard regression: no live DB credentials."""
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase, TestCase, RequestFactory
from django.contrib.auth.models import User
from django.http import HttpResponse
from monitor.fullviewer import FullViewerReadOnlyMiddleware, forbidden_request
from monitor.models import UserProfile
from monitor.page_access import hidden_pages_for_user
from monitor.access_policy import deny_remote_forward

class CompactDashboardTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html=(Path(settings.BASE_DIR)/'monitor/templates/monitor/dashboard.html').read_text()
    def test_upper_six_cards_and_pdb_dates_untouched(self):
        self.assertIn('grid-auto-rows:84px;',self.html)
        self.assertIn('const PDB_INITIAL_DATES = 35;',self.html)
        self.assertIn('id="sm-clock-second"',self.html)
    def test_single_compact_stats_card_and_persistent_refresh_dial(self):
        for marker in ('id="stat-total"','id="stat-up"','id="stat-down"',
            'id="refresh-dial"','id="countdown"','id="live-device-cards"'):
            self.assertEqual(self.html.count(marker),1,marker)
        self.assertNotIn('<!-- STATS ROW -->',self.html)
        self.assertIn('grid-template-columns:repeat(4,minmax(0,1fr));',self.html)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr));',self.html)
    def test_existing_poll_timer_and_refresh_status_remain(self):
        self.assertIn('setInterval(tick, 1000)',self.html)
        self.assertIn('refresh();',self.html)
        self.assertIn('aria-valuenow',self.html)
        self.assertIn('data.devices.filter(d => d.status',self.html)

class FullViewerReadOnlyRoutesTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('fullviewer@sysmonitor',password='synthetic')
        UserProfile.objects.create(user=self.user,role='viewer',hidden_pages=['devices','page_access'])
        self.factory=RequestFactory()
    def allowed(self,path):
        req=self.factory.get(path); req.user=self.user
        return not forbidden_request(req)
    def test_safe_overviews_accessible(self):
        for path in ('/devices/','/users/','/page-access/','/colocation-setpoints/',
            '/system/','/system/state/','/generator-cycle-audit/','/generator-fuel/'):
            self.assertTrue(self.allowed(path),path)
    def test_unknown_sensitive_and_exports_blocked(self):
        for path in ('/notifications/','/notifications/gateway/save/',
            '/manual/pdf/','/report/export/pdf/','/cctv-setup/','/users/1/delete/'):
            self.assertFalse(self.allowed(path),path)
        self.assertFalse(self.allowed('/events/?export=csv'))
    def test_mutations_blocked(self):
        for path in ('/page-access/','/system/','/shift-report/mnoc-pfe/'):
            req=self.factory.post(path,{});req.user=self.user
            self.assertEqual(FullViewerReadOnlyMiddleware(lambda r:HttpResponse('oops'))(req).status_code,403)
    def test_hidden_page_rules_dont_mask_fullviewer(self):
        self.assertEqual(hidden_pages_for_user(self.user),set())
    def test_master_only_operations_remain_denied_over_mirror(self):
        self.assertFalse(deny_remote_forward(self.user,'/system/','GET'))
        self.assertFalse(deny_remote_forward(self.user,'/system/state/','GET'))
        self.assertTrue(deny_remote_forward(self.user,'/system/restart-all/','GET'))
        self.assertTrue(deny_remote_forward(self.user,'/system/','POST'))
        self.assertTrue(deny_remote_forward(self.user,'/users/1/delete/','POST'))
        self.assertTrue(deny_remote_forward(self.user,'/notifications/gateway/save/','GET'))
