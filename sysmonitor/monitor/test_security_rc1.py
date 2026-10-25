"""Regression checks for source-only RC1 constraints.
Tests use a temporary Django test DB; no live SQLite or SMTP is required.
"""
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth.models import User
from django.urls import reverse
from monitor.models import UserProfile
from monitor.access_policy import role_assignment_error, deny_remote_forward
from monitor.fullviewer import is_fullviewer, forbidden_request

class PolicyTests(SimpleTestCase):
    def test_only_superadmin_can_assign_privileged_role(self):
        class Admin:
            is_superuser = False
        self.assertIsNotNone(role_assignment_error(Admin(), 'admin'))
        self.assertIsNotNone(role_assignment_error(Admin(), 'fullviewer'))
        self.assertIsNotNone(role_assignment_error(Admin(), 'unexpected'))

    def test_master_rejects_sensitive_mirror_actions(self):
        class Actor:
            is_superuser = False
        self.assertTrue(deny_remote_forward(Actor(), '/users/1/edit/', 'POST'))
        self.assertTrue(deny_remote_forward(Actor(), '/system/restart-all/', 'POST'))
        self.assertFalse(deny_remote_forward(Actor(), '/shift-report/pfe/3/send/', 'POST'))

class ViewSecurityTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('the-admin', password='S3cureTestPassword-01')
        UserProfile.objects.create(user=self.admin, role='admin')
        self.member = User.objects.create_user('member', password='S3cureTestPassword-01')
        UserProfile.objects.create(user=self.member, role='user')
        self.superadmin = User.objects.create_superuser('master-root', 'root@example.test', 'S3cureTestPassword-01')

    def test_admin_cannot_promote_existing_user(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('user_edit', args=[self.member.pk]), {'role':'admin'})
        self.assertEqual(r.status_code, 302)
        self.member.refresh_from_db()
        self.assertEqual(self.member.userprofile.role, 'user')

    def test_no_account_deletion_until_stage2(self):
        self.client.force_login(self.superadmin)
        self.client.post(reverse('user_delete', args=[self.member.pk]))
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())
        self.client.post(reverse('user_delete', args=[self.superadmin.pk]))
        self.assertTrue(User.objects.filter(pk=self.superadmin.pk).exists())

    def test_anonymous_sensor_api_denied(self):
        self.assertIn(self.client.get(reverse('api_sensor_status')).status_code, (302, 403))
        self.assertIn(self.client.get(reverse('api_sensor_history')).status_code, (302, 403))
        self.assertIn(self.client.get(reverse('uptime_status')).status_code, (302, 403))

    def test_password_reset_request_does_not_change_hash(self):
        self.member.email = 'member@example.test'
        self.member.save(update_fields=['email'])
        before = self.member.password
        with patch('monitor.notifications.send_password_reset_link_email', return_value=(True, '')):
            r = self.client.post(reverse('password_reset'), {'identifier':'member@example.test'})
        self.assertEqual(r.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.password, before)

    def test_fullviewer_historical_get_is_read_only_without_draft_creation(self):
        from shiftreport.models import HistoricalReportRevision
        self.member.userprofile.role = 'fullviewer'
        self.member.userprofile.save(update_fields=['role'])
        self.client.force_login(self.member)
        old_count = HistoricalReportRevision.objects.count()
        r = self.client.get(reverse('shiftreport:historical_home'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(HistoricalReportRevision.objects.count(), old_count)

    def test_user_list_renders_accounts_without_userprofile(self):
        # Superadmins may not have a UserProfile, and the user list must still render.
        self.assertFalse(UserProfile.objects.filter(user=self.superadmin).exists())
        self.client.force_login(self.admin)
        response = self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'master-root')

    def test_user_list_does_not_offer_unimplemented_delete(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'/delete/', response.content)
