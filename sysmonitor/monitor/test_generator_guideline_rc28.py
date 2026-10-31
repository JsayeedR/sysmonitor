"""RC28 guideline: permissions, no-write design, scenario separation, navigation."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import UserProfile
from .page_access import PAGE_DEFINITIONS, page_key_for_path


class GeneratorGuidelineTests(TestCase):
    def account(self, role, *, hidden=None, superuser=False):
        username = ('fullviewer@sysmonitor' if role == 'fullviewer' else 'rc28-' + role)
        user = User.objects.create_user(username=username, password='RC28SyntheticTest#31')
        user.is_superuser = superuser
        user.is_staff = superuser
        user.save(update_fields=('is_superuser', 'is_staff'))
        UserProfile.objects.create(user=user, role=role, hidden_pages=hidden or [])
        return user

    def test_registration_and_routes(self):
        cfg = PAGE_DEFINITIONS['generator_shifting_guideline']
        self.assertEqual(cfg['roles'], ('viewer', 'user', 'admin'))
        self.assertEqual(cfg['group'], 'Generator Data')
        for url in ('/generator-shifting-guideline/',
                    '/generator-shifting-guideline/pdb-active/',
                    '/generator-shifting-guideline/pdb-out/'):
            self.assertEqual(page_key_for_path(url), 'generator_shifting_guideline')
        self.assertEqual(reverse('generator_guideline_active'),
                         '/generator-shifting-guideline/pdb-active/')
        self.assertEqual(reverse('generator_guideline_out'),
                         '/generator-shifting-guideline/pdb-out/')
        self.assertEqual(reverse('generator_guideline_illustration'),
                         '/generator-shifting-guideline/illustration/')

    def test_guest_cannot_view(self):
        self.assertEqual(self.client.get(reverse('generator_guideline_active')).status_code, 302)

    def test_viewer_user_admin_and_superadmin_read_only(self):
        for role in ('viewer', 'user', 'admin'):
            with self.subTest(role=role):
                self.client.force_login(self.account(role))
                active = self.client.get(reverse('generator_guideline_active'))
                self.assertEqual(active.status_code, 200)
                self.assertContains(active, 'DRAFT — PENDING TECHNICAL APPROVAL')
                self.assertContains(active, 'Generator Shifting Guideline')
                self.assertContains(active, 'class="step-no"', count=13)
                self.assertContains(active, '/generator-shifting-guideline/illustration/')
                self.assertNotContains(active, '<form')
                self.assertContains(active, 'Scenario 2 — PDB Out')
                self.assertContains(active, 'About / Docs')
                self.assertContains(active, '© 2026 COXCLS NOC')
                self.assertEqual(active['Cache-Control'], 'private, no-store, max-age=0')
                self.assertEqual(self.client.post(reverse('generator_guideline_active')).status_code, 405)
                self.client.logout()
        self.client.force_login(self.account('superadmin', superuser=True))
        self.assertEqual(self.client.get(reverse('generator_guideline_active')).status_code, 200)

    def test_out_scenario_has_no_switching_steps(self):
        self.client.force_login(self.account('viewer'))
        response = self.client.get(reverse('generator_guideline_out'))
        self.assertContains(response, 'Procedure Not Yet Provided')
        self.assertContains(response, 'About / Docs')
        self.assertContains(response, '© 2026 COXCLS NOC')
        self.assertNotContains(response, 'class="step-no"')
        self.assertNotContains(response, 'proposed generator test')
        self.assertNotContains(response, '<form')

    def test_individual_page_access_cannot_be_bypassed(self):
        for role in ('viewer', 'user', 'admin'):
            with self.subTest(role=role):
                u = self.account(role, hidden=['generator_shifting_guideline'])
                self.client.force_login(u)
                self.assertEqual(self.client.get(reverse('generator_guideline_active')).status_code, 403)
                self.assertEqual(self.client.get(reverse('generator_guideline_out')).status_code, 403)
                self.client.logout()

    def test_fullviewer_and_guest_are_not_granted_access(self):
        for role in ('fullviewer', 'guest'):
            with self.subTest(role=role):
                self.client.force_login(self.account(role))
                self.assertEqual(self.client.get(reverse('generator_guideline_active')).status_code, 403)
                self.assertEqual(self.client.get(reverse('generator_guideline_out')).status_code, 403)
                self.client.logout()

    def test_navigation_location_and_generator_fuel_report_order(self):
        for role in ('viewer', 'user', 'admin'):
            with self.subTest(role=role):
                self.client.force_login(self.account(role))
                html = self.client.get(reverse('generator_guideline_active')).content.decode('utf-8')
                self.assertIn('generator-shifting-guideline/pdb-active/', html)
                self.assertIn('📊 Generator Fuel Report', html)
                if role in ('user', 'admin'):
                    self.assertLess(html.index('⛽ Generator Fuel Entry'),
                                    html.index('📊 Generator Fuel Report'))
                self.client.logout()

    def test_image_private_permission_and_type(self):
        self.client.force_login(self.account('viewer'))
        response = self.client.get(reverse('generator_guideline_illustration'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.post(reverse('generator_guideline_illustration')).status_code, 405)

    def test_invalid_method_and_mirror_forwarding_rejected(self):
        self.client.force_login(self.account('viewer'))
        self.assertEqual(self.client.put(reverse('generator_guideline_active')).status_code, 405)
        self.assertEqual(self.client.patch(reverse('generator_guideline_out')).status_code, 405)
