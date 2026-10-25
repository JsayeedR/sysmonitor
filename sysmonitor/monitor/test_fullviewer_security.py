"""Additional route-based Full Viewer regression checks."""
from types import SimpleNamespace
from django.http import HttpResponse
from django.test import SimpleTestCase, RequestFactory
from monitor.fullviewer import FullViewerReadOnlyMiddleware

class FullViewerRouteSecurityTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.viewer = SimpleNamespace(username='fullviewer@sysmonitor', is_authenticated=True)

    def perform(self, path, method='get', response=None):
        request = getattr(self.factory, method)(path)
        request.user = self.viewer
        response = response or HttpResponse('ok', content_type='text/html')
        return FullViewerReadOnlyMiddleware(lambda r: response)(request)

    def test_safe_read_routes(self):
        for path in ('/', '/report/', '/outage-cycle-remarks/',
                     '/generator-cycle-audit/', '/users/', '/duty-roster/',
                     '/shift-report/', '/shift-report/mnoc-pfe/',
                     '/notifications/', '/system/', '/shift-report/config/settings/'):
            with self.subTest(path=path):
                self.assertEqual(self.perform(path).status_code, 200)

    def test_get_action_and_unsafe_routes_denied(self):
        for path in ('/users/1/toggle-active/', '/users/create/',
                     '/users/1/edit/', '/devices/1/edit/', '/maintenance/start/',
                     '/shift-report/1/send/', '/shift-report/mnoc-pfe/1/send/',
                     '/generator-cycle-audit/1/edit/', '/outage-cycle-remarks/1/edit/',
                     '/report/export/pdf/', '/media/private.pdf',
                     '/admin/',
                     '/no-such-page/'):
            with self.subTest(path=path):
                self.assertEqual(self.perform(path).status_code, 403)

    def test_mutations_denied(self):
        for method in ('post', 'put', 'patch', 'delete', 'options'):
            with self.subTest(method=method):
                self.assertEqual(self.perform('/report/', method).status_code, 403)

    def test_query_exports_denied(self):
        for path in ('/report/?export=csv', '/report/?download=1', '/report/?format=pdf'):
            self.assertEqual(self.perform(path).status_code, 403)

    def test_download_responses_denied(self):
        for content_type in ('application/pdf', 'text/csv', 'application/octet-stream'):
            response = HttpResponse('data', content_type=content_type)
            self.assertEqual(self.perform('/report/', response=response).status_code, 403)
        response = HttpResponse('data')
        response['Content-Disposition'] = 'attachment; filename=x.txt'
        self.assertEqual(self.perform('/report/', response=response).status_code, 403)

    def test_nonviewer_unaffected(self):
        request = self.factory.post('/report/')
        request.user = SimpleNamespace(username='adminuser', is_authenticated=True)
        guard = FullViewerReadOnlyMiddleware(lambda r: HttpResponse('normal'))
        self.assertEqual(guard(request).status_code, 200)
