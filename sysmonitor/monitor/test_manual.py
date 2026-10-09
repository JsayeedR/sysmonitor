from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .manual_content import PAGE_GUIDES
from .manual_pdf import build_manual_pdf
from .models import UserProfile
from .page_access import page_key_for_path


class ManualFeatureTests(TestCase):
    def _user(self, username, role):
        user = User.objects.create_user(
            username=username,
            password='manual-test-password',
        )
        UserProfile.objects.create(user=user, role=role)
        return user

    def test_manual_page_registered_in_page_access(self):
        self.assertEqual(page_key_for_path('/manual/'), 'manual')
        self.assertEqual(page_key_for_path('/manual/pdf/'), 'manual')

    def test_viewer_can_open_manual(self):
        user = self._user('viewer_manual', 'viewer')
        self.client.force_login(user)
        response = self.client.get(reverse('manual'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SysMonitor')
        self.assertContains(response, 'Operations Manual')

    def test_guest_role_cannot_open_manual(self):
        user = self._user('guest_manual', 'guest')
        self.client.force_login(user)
        response = self.client.get(reverse('manual'))
        self.assertEqual(response.status_code, 403)

    def test_manual_covers_core_pages(self):
        ids = {item['id'] for item in PAGE_GUIDES}
        self.assertTrue({
            'dashboard',
            'colocation',
            'load-shedding',
            'shift-report',
            'generator-runtime',
            'events',
            'cctv',
            'notifications-admin',
            'system',
        }.issubset(ids))

    def test_manual_pdf_builds(self):
        pdf = build_manual_pdf('1.7.9999')
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1000)
