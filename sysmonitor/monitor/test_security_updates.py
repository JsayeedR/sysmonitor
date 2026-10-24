"""Security release bulletin role and navigation regression tests."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from monitor.models import UserProfile


class SecurityUpdatesPageTests(TestCase):
    def setUp(self):
        self.url = reverse('security_patch_updates')

    def _account(self, role):
        user = User.objects.create_user(username=f'security-{role}', password='TemporaryTestOnly1!')
        UserProfile.objects.create(user=user, role=role)
        self.client.force_login(user)
        return user

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_guest_denied_at_endpoint(self):
        self._account('guest')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_read_only_permitted_roles(self):
        for role in ('viewer', 'user', 'admin', 'fullviewer'):
            with self.subTest(role=role):
                self.client.logout()
                self._account(role)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Security Patch Updates')
                self.assertContains(response, '94 / 94')
                self.assertEqual(response['Cache-Control'], 'private, no-store')
                self.assertIn(b'/security-patch-updates/', response.content)
                self.assertIn(b'About Us', response.content)

    def test_individual_page_access_restriction(self):
        user = self._account('viewer')
        user.userprofile.hidden_pages = ['security_patch_updates']
        user.userprofile.save(update_fields=['hidden_pages'])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_post_is_not_supported(self):
        self._account('user')
        self.assertEqual(self.client.post(self.url).status_code, 405)
