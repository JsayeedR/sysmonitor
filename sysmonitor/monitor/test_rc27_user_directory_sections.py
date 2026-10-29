"""RC27: account lifecycle directory and Android telemetry truthfulness tests."""
import re
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from .models import ActivityLog, UserProfile


class AccountDirectorySectionsTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser('root-directory', 'root@example.test', 'synthetic')
        self.admin = User.objects.create_user('admin-directory', password='synthetic')
        UserProfile.objects.create(user=self.admin, role='admin')
        self.active = User.objects.create_user('active-directory', password='synthetic')
        UserProfile.objects.create(user=self.active, role='user')
        self.disabled = User.objects.create_user('disabled-directory', password='synthetic', is_active=False)
        UserProfile.objects.create(user=self.disabled, role='user')
        self.retired = User.objects.create_user('retired-directory', password='synthetic')
        UserProfile.objects.create(user=self.retired, role='user')

    @staticmethod
    def _tab(html, tab):
        found = re.search(
            r'<div id="tab-' + re.escape(tab) + r'" class="tab-panel(?: active)?">(.*?)</div>\s*(?=<div id="tab-|$)',
            html, flags=re.DOTALL,
        )
        # Nested divs are allowed, so inspect by the next tab ID instead.
        marker = 'id="tab-' + tab + '"'
        start = html.index(marker)
        next_tab = re.search(r'<div id="tab-[a-z]+" class="tab-panel', html[start+len(marker):])
        end = start + len(marker) + next_tab.start() if next_tab else len(html)
        return html[start:end]

    def test_disabled_retired_and_active_automatically_separate(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(
            reverse('user_delete', args=[self.retired.pk]),
            {'confirm_username': self.retired.username},
        ).status_code, 302)
        response = self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        active = self._tab(text, 'all')
        disabled = self._tab(text, 'disabled')
        retired = self._tab(text, 'retired')
        self.assertIn('active-directory', active)
        self.assertNotIn('disabled-directory', active)
        self.assertNotIn('retired-directory', active)
        self.assertIn('disabled-directory', disabled)
        self.assertNotIn('retired-directory', disabled)
        self.assertIn('retired-directory', retired)
        self.assertIn(str(self.retired.pk), retired)
        self.assertNotIn('active-directory', retired)
        self.assertContains(response, 'Retired / Removed')
        self.assertContains(response, 'Android App Users')
        self.assertNotIn('retired-directory', self._tab(text, 'details'))

    def test_disabled_account_can_be_reenabled_but_retired_cannot(self):
        self.client.force_login(self.root)
        self.client.post(reverse('user_delete', args=[self.retired.pk]),
                         {'confirm_username': self.retired.username})
        self.client.post(reverse('user_toggle_active', args=[self.disabled.pk]))
        self.disabled.refresh_from_db()
        self.assertTrue(self.disabled.is_active)
        self.client.post(reverse('user_toggle_active', args=[self.retired.pk]))
        self.retired.refresh_from_db()
        self.assertFalse(self.retired.is_active)
        self.assertFalse(self.retired.has_usable_password())
        self.assertEqual(
            self.client.get(reverse('user_edit', args=[self.retired.pk])).status_code,
            302,
        )

    def test_disabled_without_retirement_audit_is_not_misclassified(self):
        self.disabled.set_unusable_password()
        self.disabled.save(update_fields=['password'])
        self.client.force_login(self.admin)
        response = self.client.get(reverse('user_list'))
        text = response.content.decode()
        self.assertIn('disabled-directory', self._tab(text, 'disabled'))
        self.assertNotIn('disabled-directory', self._tab(text, 'retired'))

    def test_normal_admin_cannot_retire_and_viewer_cannot_view_directory(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(
            reverse('user_delete', args=[self.active.pk]),
            {'confirm_username': self.active.username},
        ).status_code, 403)
        self.active.refresh_from_db()
        self.assertTrue(self.active.is_active)
        self.client.force_login(self.active)
        self.assertNotEqual(self.client.get(reverse('user_list')).status_code, 200)

    def test_android_tab_explicitly_does_not_fabricate_usage(self):
        self.client.force_login(self.root)
        response = self.client.get(reverse('user_list'))
        android = self._tab(response.content.decode(), 'android')
        self.assertIn('not yet enabled', android)
        self.assertIn('not Android-only statistics', android)
        self.assertNotIn('Installed versions:', android)

    def test_retired_audit_is_kept_and_original_user_pk_is_preserved(self):
        self.client.force_login(self.root)
        pk = self.retired.pk
        self.client.post(reverse('user_delete', args=[pk]),
                         {'confirm_username': self.retired.username})
        self.assertTrue(User.objects.filter(pk=pk).exists())
        self.assertTrue(ActivityLog.objects.filter(
            action='USER_EDITED',
            detail=f'Account login retired for ID {pk}; history and user record retained.'
        ).exists())
