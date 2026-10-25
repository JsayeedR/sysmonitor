"""RC24.6 display / navbar regression checks (no live data writes)."""
from pathlib import Path
from types import SimpleNamespace
from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class DashboardClockAndLampTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.page = (Path(settings.BASE_DIR) / 'monitor/templates/monitor/dashboard.html').read_text()

    def test_clock_is_between_ats_and_colocation_in_second_row(self):
        self.assertIn('grid-template-areas:"status lamps cycle" "ats clock colo"', self.page)
        self.assertIn('"status" "lamps" "cycle" "ats" "clock" "colo"', self.page)
        for elem in ('clock-banner', 'sm-clock-day', 'sm-clock-digital',
                     'sm-clock-date', 'sm-clock-hour', 'sm-clock-minute', 'sm-clock-second'):
            self.assertIn(f'id="{elem}"', self.page)
        self.assertIn("timeZone: 'Asia/Dhaka'", self.page)
        self.assertIn("window.setInterval(paintClock, 100)", self.page)

    def test_lamps_glow_only_when_up_and_off_is_dark(self):
        for state in ('pdb-up','gen-on','nvr-up','off','unknown'):
            self.assertIn('sm-light.' + state, self.page)
        self.assertIn('.sm-light.off {', self.page)
        self.assertIn('--lamp-aura:transparent;', self.page)
        self.assertIn("?'unknown':'off'", self.page)
        self.assertIn('sm-light.pdb-up::after', self.page)


class NavbarRestrictedAccessTests(SimpleTestCase):
    def nav(self, hidden, superuser=False):
        user = SimpleNamespace(is_superuser=superuser, is_authenticated=True)
        request = SimpleNamespace(user=user, path='/', path_info='/',
                                  resolver_match=SimpleNamespace(url_name='dashboard'))
        return render_to_string('monitor/navbar.html', {
            'request': request, 'role': 'admin', 'page_hidden': hidden,
            'is_mirror': False, 'is_fullviewer': False,
            'base_path': '', 'sys_version': 'test'
        })

    def test_page_access_link_absent_for_hidden_normal_admin(self):
        page = self.nav({'page_access'})
        self.assertNotIn('href="/page-access/"', page)
        self.assertIn('Administrative', page)

    def test_page_access_link_visible_for_authorized_admin(self):
        self.assertIn('href="/page-access/"', self.nav(set()))

    def test_superadmin_recovery_remains_visible(self):
        self.assertIn('href="/page-access/"', self.nav({'page_access'}, True))
