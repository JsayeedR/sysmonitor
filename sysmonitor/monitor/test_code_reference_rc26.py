"""Access-control and documentation regression tests for internal Code Reference."""
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from monitor.code_reference import code_reference_view
from monitor.models import UserProfile


class CodeReferencePageTests(TestCase):
    def setUp(self):
        self.url = reverse('code_reference')

    def login(self, name, *, role='user', superuser=False):
        user = User.objects.create_user(username=name, password='OnlyForTests!123')
        user.is_superuser = superuser
        user.is_staff = superuser
        user.save(update_fields=['is_superuser', 'is_staff'])
        UserProfile.objects.create(user=user, role=role)
        self.client.force_login(user)
        return user

    def test_anonymous_request_redirects(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_unauthorized_roles_forbidden(self):
        for role in ('guest', 'viewer', 'fullviewer'):
            with self.subTest(role=role):
                self.client.logout()
                self.login('role-' + role, role=role)
                self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_approved_user_admin_and_superuser_read_only_page(self):
        self.login('reference-root', role='admin', superuser=True)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Code Reference')
        self.assertContains(response, 'sysmonitor/monitor/models.py')
        self.assertContains(response, 'sysmonitor/shiftreport/historical_sources.py')
        self.assertContains(response, 'Repository hierarchy')
        self.assertContains(response, 'Django project &amp; configuration')
        self.assertContains(response, 'Complete folder tree')
        self.assertContains(response, 'f892ae7fbc0d')
        self.assertEqual(response['Cache-Control'], 'private, no-store, max-age=0')
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')

    def test_post_rejected_and_no_source_edit_action(self):
        self.login('reference-post', role='admin', superuser=True)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        response = self.client.get(self.url)
        self.assertNotContains(response, 'method="post"')
        self.assertNotContains(response, 'PRIVATE KEY-----')

    def test_navigation_superuser_only(self):
        self.login('nav-admin', role='admin')
        manual = self.client.get(reverse('manual'))
        self.assertEqual(manual.status_code, 200)
        self.assertContains(manual, '🧭 Code Reference')
        self.client.logout()
        self.login('nav-root', role='admin', superuser=True)
        response = self.client.get(reverse('manual'))
        self.assertContains(response, '🧭 Code Reference')

    def test_standard_user_and_admin_allowed(self):
        for role in ('user', 'admin'):
            with self.subTest(role=role):
                self.client.logout()
                self.login('reference-' + role, role=role)
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_forwarded_identity_denied_even_for_superuser(self):
        user = User.objects.create_superuser('forwarded-root', password='OnlyForTests!123')
        request = RequestFactory().get(self.url)
        request.user = user
        request._mirror_forwarded = True
        response = code_reference_view(request)
        self.assertEqual(response.status_code, 403)

    def test_reserved_full_viewer_superuser_still_denied(self):
        user = User.objects.create_superuser('fullviewer@sysmonitor', password='OnlyForTests!123')
        request = RequestFactory().get(self.url)
        request.user = user
        response = code_reference_view(request)
        self.assertEqual(response.status_code, 403)
