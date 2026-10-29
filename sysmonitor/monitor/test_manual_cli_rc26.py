"""Read-only CLI disclosure/accordion tests for the Operations Manual."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from monitor.manual_content import ADMIN_TERMINAL_CLI
from monitor.models import UserProfile


class ManualCLIExpansionTests(TestCase):
    def login(self, name, role):
        user = User.objects.create_user(username=name, password='OnlyForTests!123')
        UserProfile.objects.create(user=user, role=role)
        self.client.force_login(user)

    def test_many_collapsible_admin_cli_groups(self):
        self.assertGreaterEqual(len(ADMIN_TERMINAL_CLI), 12)
        self.assertGreaterEqual(sum(len(g['commands']) for g in ADMIN_TERMINAL_CLI), 40)
        self.login('command-admin', 'admin')
        response = self.client.get(reverse('manual'))
        self.assertContains(response, 'Administrator Terminal CLI')
        self.assertContains(response, '<details class="cli-card">', html=False)
        self.assertContains(response, 'Copy command')
        self.assertContains(response, 'Controlled restart')

    def test_standard_user_receives_display_only_cli(self):
        self.login('command-user', 'user')
        response = self.client.get(reverse('manual'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<details class="cli-card">')
        self.assertContains(response, 'sudo systemctl restart')

    def test_reserved_full_viewer_cannot_read_admin_cli(self):
        self.login('fullviewer@sysmonitor', 'fullviewer')
        response = self.client.get(reverse('manual'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<details class="cli-card">')
        self.assertNotContains(response, 'ssh -p 3048 app-admin')

    def test_commands_are_display_only(self):
        self.login('command-display', 'admin')
        response = self.client.get(reverse('manual'))
        self.assertContains(response, 'do not execute commands')
        self.assertNotContains(response, 'data-shell-execute')
