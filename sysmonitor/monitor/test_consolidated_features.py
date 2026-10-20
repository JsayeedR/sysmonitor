"""Release smoke tests for the read-only viewer and outage remarks."""
from types import SimpleNamespace
from django.http import HttpResponse
from django.test import SimpleTestCase, RequestFactory
from django.urls import reverse
from .fullviewer import FullViewerReadOnlyMiddleware
from .views import get_role
from .page_access import user_role


class FullViewerRestrictionTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = SimpleNamespace(is_authenticated=True, is_superuser=False,
                                    is_staff=False, username='fullviewer@sysmonitor')
        self.guard = FullViewerReadOnlyMiddleware(lambda request: HttpResponse('read-only page'))

    def check(self, method, path, expected):
        request = getattr(self.factory, method.lower())(path)
        request.user = self.user
        self.assertEqual(self.guard(request).status_code, expected)

    def test_identity_presented_as_admin_for_read_pages(self):
        self.assertEqual(get_role(self.user), 'admin')
        self.assertEqual(user_role(self.user), 'admin')

    def test_mutations_blocked(self):
        for path in ('/devices/', '/shift-report/create/', '/outage-cycle-remarks/cycle/1/add/'):
            self.check('POST', path, 403)

    def test_sensitive_and_download_blocked(self):
        for path in ('/notifications/', '/system/', '/shift-report/config/',
                     '/duty-roster/template.xlsx/', '/manual/pdf/', '/report/?export=csv'):
            self.check('GET', path, 403)

    def test_normal_report_view_allowed(self):
        self.check('GET','/report/',200)

    def test_remark_route_exists(self):
        self.assertEqual(reverse('outage_cycle_remarks'),'/outage-cycle-remarks/')
