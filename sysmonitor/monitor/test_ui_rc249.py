"""RC24.9 Full Viewer display-only page regression; synthetic database only."""
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from monitor.access_policy import deny_remote_forward
from monitor.fullviewer import forbidden_request
from monitor.models import UserProfile, NotificationGateway
from shiftreport.models import HistoricalReportRevision

class FullViewerPageRegression(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user('fullviewer@sysmonitor', password='synthetic-rc249')
        UserProfile.objects.create(user=self.viewer, role='viewer')
        self.client.force_login(self.viewer)

    def test_eight_informational_pages_render_and_no_historical_draft(self):
        engineer = User.objects.create_user('synthetic-eng', password='synthetic')
        HistoricalReportRevision.objects.create(
            revision_number=1, owner=engineer, status='SENT',
            rows=[{'category_1':'Sample','category_2':'Risk','details':'Synthetic only',
                   'remarks':'Synthetic','event_type':'Test'}],
        )
        initial = HistoricalReportRevision.objects.count()
        paths = [
            '/notification-request/', '/shift-report/historical/',
            '/shift-report/config/settings/', '/shift-report/config/wet-circuits/',
            '/page-access/', '/cctv-setup/', '/colocation-setpoints/',
            '/notifications/',
        ]
        with patch('monitor.notifications.get_telegram_bot_identity', return_value=(False, {})):
            for path in paths:
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200, path)
        self.assertEqual(HistoricalReportRevision.objects.count(), initial)
        self.assertEqual(NotificationGateway.objects.count(), 0)

    def test_explicit_fullviewer_role_not_just_reserved_username(self):
        # Regression for PageAccessMiddleware: non-reserved Full Viewer
        # must be able to READ historical/configuration data as well.
        assigned = User.objects.create_user('synthetic-assigned-fv', password='synthetic')
        UserProfile.objects.create(user=assigned, role='fullviewer')
        self.client.force_login(assigned)
        from monitor.page_access import user_role
        self.assertEqual(user_role(assigned), 'admin')
        before = HistoricalReportRevision.objects.count()
        for url in ('/shift-report/historical/', '/page-access/',
                    '/shift-report/config/settings/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(HistoricalReportRevision.objects.count(), before)
        self.assertEqual(self.client.post('/page-access/', {}).status_code, 403)

    def test_notifications_mask_secrets_without_writing(self):
        NotificationGateway.objects.create(
            channel='telegram', tg_bot_token='SYNTHETIC_FAKE_SECRET_ABC123'
        )
        response = self.client.get('/notifications/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'SYNTHETIC_FAKE_SECRET_ABC123', response.content)
        self.assertEqual(NotificationGateway.objects.count(), 1)

    def test_page_access_form_visible_but_post_is_forbidden(self):
        response = self.client.get('/page-access/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="visible_pages"', response.content)
        self.assertNotIn(b'form[method="post"],form[method="POST"]{display:none', response.content)
        self.assertEqual(self.client.post('/page-access/', {}).status_code, 403)

    def test_historical_owner_visible_without_edit_form(self):
        engineer = User.objects.create_user('synthetic_history_owner')
        HistoricalReportRevision.objects.create(
            revision_number=1, owner=engineer, status='DRAFT',
            rows=[{'category_1':'Example','category_2':'','details':'','remarks':'','event_type':''}]
        )
        response = self.client.get('/shift-report/historical/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'synthetic_history_owner')
        self.assertNotContains(response, 'id="historicalForm"')

    def test_sensitive_write_export_and_unknown_routes_denied(self):
        for path in ('/notifications/gateway/save/', '/manual/pdf/',
                     '/report/?export=csv', '/cctv-setup/unknown/', '/admin/'):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        for path in ('/notifications/', '/cctv-setup/',
                     '/shift-report/config/settings/', '/colocation-setpoints/'):
            self.assertEqual(self.client.post(path, {}).status_code, 403, path)

    def test_signed_mirror_master_cctv_inventory_get_only(self):
        self.assertFalse(deny_remote_forward(self.viewer, '/cctv-setup/', 'GET'))
        self.assertTrue(deny_remote_forward(self.viewer, '/cctv-setup/', 'POST'))
        self.assertTrue(deny_remote_forward(self.viewer, '/cctv-setup/1/delete/', 'GET'))
        for url in ('/shift-report/config/settings/', '/shift-report/config/wet-circuits/'):
            self.assertFalse(deny_remote_forward(self.viewer, url, 'GET'))
            self.assertTrue(deny_remote_forward(self.viewer, url, 'POST'))
        self.assertTrue(deny_remote_forward(self.viewer, '/shift-report/config/wet-circuits/1/delete/', 'GET'))

class GeneratorNavigationRegression(SimpleTestCase):
    def test_remarks_after_manual_entry_in_generator_menu(self):
        text = (Path(settings.BASE_DIR)/'monitor/templates/monitor/navbar.html').read_text()
        marker = '>🕵️ Generator Manual Cycle Entry</a>'
        remarks = '>⚡ Outage Cycle Remarks</a>'
        self.assertIn(marker, text)
        self.assertEqual(text.count(remarks), 1)
        self.assertGreater(text.index(remarks), text.index(marker))
        # Validate the updated operational menu order within the User/Admin
        # dropdown. The Viewer-only menu appears earlier in this template.
        generator_start = text.index('    {% if page_show_generator %}')
        admin_start = text.index('    {% elif role == "user" or role == "admin" %}', generator_start)
        others_start = text.index('    {% if page_show_others %}', admin_start)
        admin_menu = text[admin_start:others_start]
        labels = (
            '>📘 Generator Shifting Guideline</a>',
            '>🔄 Generator Shifting Entry</a>',
            '>⛽ Generator Fuel Entry</a>',
            '>📊 Generator Fuel Report</a>',
            '>🕵️ Generator Manual Cycle Entry</a>',
            '>⚡ Outage Cycle Remarks</a>',
            '>⏱️ Generator Runtime</a>',
        )
        positions = [admin_menu.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
