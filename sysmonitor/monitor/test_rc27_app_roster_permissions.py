"""RC27: role-ceiling, per-user Page Access and source-derived roster tests."""
from datetime import date, datetime, timedelta

import pytz
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import DutyRoster, DutyRosterAssignment, UserProfile
from .page_access import PAGE_DEFINITIONS, page_key_for_path
from .roster_service import rolling_duty_days, resolve_current_and_next

BDT = pytz.timezone('Asia/Dhaka')


class NewPagesPermissionsTests(TestCase):
    def account(self, name, role, superuser=False, hidden=None):
        u = User.objects.create_user(username=name, password='ForRegressionOnly#7')
        u.is_superuser = superuser
        u.is_staff = superuser
        u.save(update_fields=['is_superuser', 'is_staff'])
        UserProfile.objects.create(user=u, role=role, hidden_pages=hidden or [])
        return u

    def test_page_access_registration_and_route_mapping(self):
        for key, path in (
            ('android_application', '/android-application/'),
            ('duty_roster_view', '/duty-schedule/'),
            ('code_reference', '/code-reference/'),
        ):
            self.assertIn(key, PAGE_DEFINITIONS)
            self.assertEqual(page_key_for_path(path), key)
        self.assertIn('administrator_cli', PAGE_DEFINITIONS)
        from .code_reference_content import FILE_COUNT
        self.assertGreaterEqual(FILE_COUNT, 245)
        self.assertEqual(PAGE_DEFINITIONS['administrator_cli']['roles'], ('user', 'admin'))
        self.assertEqual(page_key_for_path('/duty-roster/'), 'duty_roster')

    def test_allowed_roles_pages_and_navigation(self):
        for role in ('user', 'admin'):
            with self.subTest(role=role):
                self.client.force_login(self.account('r27-' + role, role))
                for url in ('/android-application/', '/duty-schedule/', '/code-reference/'):
                    self.assertEqual(self.client.get(url).status_code, 200, url)
                page = self.client.get(reverse('manual'))
                self.assertContains(page, 'Administrator Terminal CLI')
                self.assertContains(page, '/android-application/')
                self.assertContains(page, '/duty-schedule/')
                self.assertContains(page, '/code-reference/')
                self.client.logout()
        self.client.force_login(self.account('r27-root', 'admin', superuser=True))
        self.assertEqual(self.client.get('/code-reference/').status_code, 200)

    def test_other_roles_and_fullviewer_denied(self):
        for role in ('guest', 'viewer', 'fullviewer'):
            with self.subTest(role=role):
                name = 'fullviewer@sysmonitor' if role == 'fullviewer' else 'r27-' + role
                self.client.force_login(self.account(name, role))
                for url in ('/android-application/', '/duty-schedule/', '/code-reference/'):
                    self.assertEqual(self.client.get(url).status_code, 403)
                if role == 'viewer':
                    menu = self.client.get(reverse('manual'))
                    self.assertNotContains(menu, '/android-application/')
                    self.assertNotContains(menu, '/duty-schedule/')
                    self.assertNotContains(menu, '/code-reference/')
                    self.assertNotContains(menu, '<details class="cli-card">')
                self.client.logout()

    def test_page_access_hides_new_routes_and_cli_section(self):
        keys = ['android_application', 'duty_roster_view',
                'code_reference', 'administrator_cli']
        self.client.force_login(self.account('r27-hidden', 'user', hidden=keys))
        for url in ('/android-application/', '/duty-schedule/', '/code-reference/'):
            self.assertEqual(self.client.get(url).status_code, 403)
        manual = self.client.get('/manual/')
        self.assertEqual(manual.status_code, 200)
        self.assertNotContains(manual, '<details class="cli-card">')
        self.assertNotContains(manual, 'Administrator Terminal CLI')
        self.assertNotContains(manual, '/android-application/')

    def test_no_mutating_or_download_endpoints(self):
        self.client.force_login(self.account('r27-readonly', 'user'))
        for url in ('/android-application/', '/duty-schedule/'):
            self.assertEqual(self.client.post(url, {}).status_code, 405)
        roster = self.client.get('/duty-schedule/')
        self.assertNotContains(roster, '<form')
        self.assertNotContains(roster, 'Download Excel Template')
        self.assertNotContains(roster, '/duty-roster/template.xlsx/')
        self.assertContains(roster, '<details id="full-roster">')
        self.assertNotContains(roster, '<details id="full-roster" open')
        android = self.client.get('/android-application/')
        self.assertContains(android, 'https://bscplc.com.bd/uploads/file-manager/b80e5308-8b3b-4453-bbe7-5a2bfcdbc2ed.apk')
        self.assertContains(android, 'Source version:')

    def test_admin_only_roster_overview(self):
        for role in ('viewer', 'user'):
            with self.subTest(role=role):
                self.client.force_login(self.account('r27-mgr-' + role, role))
                self.assertEqual(self.client.get('/duty-roster/').status_code, 403)
                self.client.logout()
        self.client.force_login(self.account('r27-mgr-admin', 'admin'))
        manager = self.client.get('/duty-roster/')
        self.assertContains(manager, '10-Day Duty Matrix')
        self.assertContains(manager, 'Imported Roster History')
        self.client.logout()
        self.client.force_login(self.account('fullviewer@sysmonitor', 'fullviewer'))
        manager = self.client.get('/duty-roster/')
        self.assertEqual(manager.status_code, 200)  # Existing Full Viewer read-only behavior
        self.assertNotContains(manager, '10-Day Duty Matrix')


class RollingRosterDataTests(TestCase):
    def add_assignment(self, day, code='M', engineer='Engineer A'):
        roster, _ = DutyRoster.objects.get_or_create(month=day.replace(day=1))
        DutyRosterAssignment.objects.create(
            roster=roster, duty_date=day,
            engineer_name=engineer, duty_code=code, source_row=8)

    def test_rolling_windows_across_month_boundary_no_fabricated_shifts(self):
        today = date(2026, 10, 29)
        self.add_assignment(today, 'M', 'Engineer A')
        self.add_assignment(date(2026, 11, 1), 'E', 'Engineer B')
        window = rolling_duty_days(start_date=today, days=10)
        self.assertEqual(len(window), 10)
        self.assertEqual(window[0]['date'], today)
        self.assertEqual(window[-1]['date'], date(2026, 11, 7))
        self.assertEqual(window[0]['shifts'][0]['display'], 'Engineer A')
        self.assertEqual(window[3]['shifts'][1]['display'], 'Engineer B')
        self.assertEqual(window[0]['shifts'][2]['display'], 'Not assigned')
        full = rolling_duty_days(start_date=today, days=31)
        self.assertEqual(len(full), 31)
        self.assertEqual(full[-1]['date'], date(2026, 11, 28))

    def test_current_next_shift_uses_real_service_timings(self):
        for hour, cur, nxt, expected_duty_date in (
            (8, 'N', 'M', date(2026, 10, 28)),
            (12, 'M', 'E', date(2026, 10, 29)),
            (20, 'E', 'N', date(2026, 10, 29)),
            (23, 'N', 'M', date(2026, 10, 29)),
        ):
            with self.subTest(hour=hour):
                now = BDT.localize(datetime(2026, 10, 29, hour, 30))
                state = resolve_current_and_next(now=now)
                self.assertEqual(state['current']['code'], cur)
                self.assertEqual(state['next']['code'], nxt)
                self.assertEqual(state['current']['duty_date'], expected_duty_date.isoformat())
