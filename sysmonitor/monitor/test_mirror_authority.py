"""RC24 fail-closed mirror identity tests; synthetic database only."""
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from monitor.mirror import MirrorForwardMiddleware, MirrorInboundMiddleware, sign
from monitor.mirror_authority import identity_proof, mirror_auth_validate
from monitor.models import UserProfile


@override_settings(MIRROR_SHARED_SECRET='synthetic-rc24-test-key', IS_MIRROR=False)
class MirrorAuthorityTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user('target24', password='test-only-pw')
        UserProfile.objects.create(user=self.user, role='user')
        self.other = User.objects.create_user('other24', password='test-only-pw')
        UserProfile.objects.create(user=self.other, role='user')

    def invoke(self, user, **kwargs):
        request = self.factory.post('/mirror/auth/validate/',
                                    data=json.dumps({'uid': str(user.pk), 'proof': identity_proof(user), **kwargs}),
                                    content_type='application/json')
        request.user = user
        request._mirror_forwarded = True
        return mirror_auth_validate(request)

    def test_matching_master_identity_is_accepted(self):
        self.assertEqual(self.invoke(self.user).status_code, 200)

    def test_updated_password_rejects_old_mirror_proof(self):
        old = identity_proof(self.user)
        self.user.set_password('new-test-only-pw')
        self.user.save()
        req = self.factory.post('/mirror/auth/validate/', json.dumps({'uid': str(self.user.pk), 'proof': old}),
                                content_type='application/json')
        req.user = self.user
        req._mirror_forwarded = True
        self.assertEqual(mirror_auth_validate(req).status_code, 403)

    def test_deactivated_user_rejected(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.invoke(self.user).status_code, 403)

    def test_role_downgrade_rejects_stale_snapshot(self):
        old = identity_proof(self.user)
        self.user.userprofile.role = 'viewer'
        self.user.userprofile.save()
        req = self.factory.post('/mirror/auth/validate/', json.dumps({'uid': str(self.user.pk), 'proof': old}),
                                content_type='application/json')
        req.user = self.user
        req._mirror_forwarded = True
        self.assertEqual(mirror_auth_validate(req).status_code, 403)

    def test_page_access_restriction_change_revokes_stale_proof(self):
        old = identity_proof(self.user)
        self.user.userprofile.hidden_pages = ['generator-fuel']
        self.user.userprofile.save(update_fields=['hidden_pages'])
        self.assertNotEqual(identity_proof(self.user), old)

    def test_group_assignment_change_revokes_stale_proof(self):
        from django.contrib.auth.models import Group
        old = identity_proof(self.user)
        group = Group.objects.create(name='rc24-synthetic-group')
        self.user.groups.add(group)
        self.assertNotEqual(identity_proof(self.user), old)

    def test_untrusted_direct_browser_access_denied(self):
        req = self.factory.post('/mirror/auth/validate/', data='{}', content_type='application/json')
        req.user = self.user
        self.assertEqual(mirror_auth_validate(req).status_code, 403)

    def test_unrelated_account_proof_stays_valid(self):
        proof = identity_proof(self.other)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(identity_proof(self.other), proof)
        self.assertEqual(self.invoke(self.other).status_code, 200)

    def test_proof_is_bound_to_account_identity(self):
        self.assertNotEqual(identity_proof(self.user), identity_proof(self.other))

    def _inbound_auth_probe(self, accepted):
        # A signed remote identity-validation request is read-only despite POST.
        # It must not schedule snapshot uploads on either success or denial.
        path = '/mirror/auth/validate/'
        body = json.dumps({
            'uid': str(self.user.pk),
            'proof': identity_proof(self.user) if accepted else '0' * 64,
        }, separators=(',', ':')).encode('utf-8')
        ts = int(time.time())
        nonce = 'offline-inbound-auth-validation-nonce'
        signature = sign(ts, nonce, 'POST', path, '', self.user.username, body, '')
        req = self.factory.post(
            path, data=body, content_type='application/json',
            HTTP_X_MIRROR_TS=str(ts),
            HTTP_X_MIRROR_NONCE=nonce,
            HTTP_X_MIRROR_USER=self.user.username,
            HTTP_X_MIRROR_PREFIX='',
            HTTP_X_MIRROR_SIG=signature,
            REMOTE_ADDR='127.0.0.1',
        )
        middleware = MirrorInboundMiddleware(mirror_auth_validate)
        with patch('monitor.mirror._claim_nonce', return_value=True), \
                patch('monitor.mirror.threading.Thread') as thread:
            resp = middleware(req)
        self.assertEqual(resp.status_code, 200 if accepted else 403)
        thread.assert_not_called()

    def test_inbound_identity_validation_never_schedules_database_push(self):
        self._inbound_auth_probe(True)

    def test_denied_inbound_identity_validation_never_schedules_database_push(self):
        self._inbound_auth_probe(False)


@override_settings(MIRROR_SHARED_SECRET='synthetic-rc24-test-key')
class FailClosedRemoteTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user('remote24', password='test-only-pw')
        UserProfile.objects.create(user=self.user, role='user')
        self.middleware = MirrorForwardMiddleware(lambda request: HttpResponse('private', content_type='text/plain'))

    def request(self):
        req = self.factory.get('/some/private/page/')
        req.user = self.user
        req.session = SimpleNamespace(flush=lambda: None)
        return req

    def test_master_unreachable_returns_503(self):
        with patch('monitor.mirror._master_request', side_effect=ConnectionError):
            self.assertEqual(self.middleware(self.request()).status_code, 503)

    def test_master_rejection_returns_403(self):
        with patch('monitor.mirror._master_request', return_value=SimpleNamespace(status_code=403)):
            self.assertEqual(self.middleware(self.request()).status_code, 403)

    def test_successful_authority_check_serves_read(self):
        response = SimpleNamespace(status_code=200, json=lambda: {'valid': True})
        with patch('monitor.mirror._master_request', return_value=response) as master:
            self.assertEqual(self.middleware(self.request()).status_code, 200)
            self.assertEqual(master.call_count, 1)

    def test_invalid_success_payload_fails_closed(self):
        response = SimpleNamespace(status_code=200, json=lambda: {'valid': False})
        with patch('monitor.mirror._master_request', return_value=response):
            self.assertEqual(self.middleware(self.request()).status_code, 503)

    def test_logout_allowed_when_master_unavailable(self):
        req = self.factory.get('/logout/')
        req.user = self.user
        req.session = SimpleNamespace(flush=lambda: None)
        with patch('monitor.mirror._master_request', side_effect=ConnectionError):
            self.assertEqual(self.middleware(req).status_code, 200)
