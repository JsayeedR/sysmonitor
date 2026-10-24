"""Regression tests for the RC24.3 Full Viewer signed master verification fix.

No real accounts, live databases, cookies or secrets are used.
"""
import json
import time

from unittest.mock import patch
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from monitor.fullviewer import FullViewerReadOnlyMiddleware
from monitor.mirror import MirrorInboundMiddleware, sign
from monitor.mirror_authority import identity_proof, mirror_auth_validate
from monitor.models import UserProfile


@override_settings(MIRROR_SHARED_SECRET='synthetic-rc243-validation-test-secret', IS_MIRROR=False)
class FullViewerSignedAuthorityRegressionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='fullviewer@sysmonitor', password='synthetic-password')
        UserProfile.objects.create(user=self.user, role='viewer')

    def signed(self, proof=None, path='/mirror/auth/validate/', method='POST', valid_signature=True):
        if proof is None:
            proof = identity_proof(self.user)
        body = json.dumps({'uid': str(self.user.pk), 'proof': proof}, separators=(',', ':')).encode()
        ts = int(time.time())
        nonce = 'regressionnonce12345'
        signature = sign(ts, nonce, method, path, '', self.user.username, body, '')
        if not valid_signature:
            signature = '0' * 64
        return self.factory.generic(
            method, path, body, 'application/json',
            HTTP_X_MIRROR_TS=str(ts),
            HTTP_X_MIRROR_NONCE=nonce,
            HTTP_X_MIRROR_USER=self.user.username,
            HTTP_X_MIRROR_PREFIX='',
            HTTP_X_MIRROR_SIG=signature,
            REMOTE_ADDR='127.0.0.1',
        )

    def invoke(self, request):
        # This is the deployed relative middleware order: signature validation
        # first, then Full Viewer read-only middleware, then the authority view.
        app = MirrorInboundMiddleware(FullViewerReadOnlyMiddleware(mirror_auth_validate))
        with patch('monitor.mirror._claim_nonce', return_value=True):
            return app(request)

    def test_valid_signed_fullviewer_auth_returns_200(self):
        response = self.invoke(self.signed())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'valid': True})

    def test_wrong_proof_still_rejected(self):
        response = self.invoke(self.signed(proof='0' * 64))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content), {'valid': False})

    def test_bad_signature_still_rejected(self):
        response = self.invoke(self.signed(valid_signature=False))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content).get('error'), 'bad signature')

    def test_unsigned_browser_validation_post_still_forbidden(self):
        request = self.factory.post('/mirror/auth/validate/', '{}', content_type='application/json')
        request.user = self.user
        response = FullViewerReadOnlyMiddleware(mirror_auth_validate)(request)
        self.assertEqual(response.status_code, 403)
        self.assertIn(b'Full Viewer:', response.content)

    def test_fullviewer_regular_post_still_forbidden(self):
        request = self.factory.post('/profile/password/save/', {'a': 'b'})
        request.user = self.user
        response = FullViewerReadOnlyMiddleware(lambda r: HttpResponse('unexpected'))(request)
        self.assertEqual(response.status_code, 403)

    def test_forwarded_account_admin_still_denied(self):
        request = self.signed(path='/users/', method='POST')
        response = self.invoke(request)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content).get('error'), 'local-only operation')
