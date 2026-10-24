"""Authoritative mirror session validation; no database writes.

Only a correctly signed one-time forwarded request from the remote can reach
this master check. It never returns passwords, password hashes or user details.
The remote must call the master for every authenticated request. Master
unavailability deliberately denies authenticated mirror access (HTTP 503).
"""
import hashlib
import hmac
import json

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse
from django.views.decorators.http import require_POST


def identity_proof(user):
    """Secret-bound fingerprint of auth identity AND privileges.

    Remote and master both know the mirror shared secret and have their own
    copy of the user's password hash. The HMAC makes the proof non-reusable
    as a raw password hash and is tied to ID, login, status and permissions.
    """
    secret = getattr(settings, 'MIRROR_SHARED_SECRET', '')
    if not secret or not getattr(user, 'is_authenticated', False):
        return ''
    try:
        profile = user.userprofile
        role = profile.role
        hidden_pages = json.dumps(profile.hidden_pages or [], sort_keys=True, separators=(',', ':'))
    except (AttributeError, ObjectDoesNotExist):
        # Some superusers have no UserProfile; avoid a false denial.
        role = ''
        hidden_pages = '[]'
    parts = (str(user.pk), user.username, user.password,
             str(int(user.is_active)), str(int(user.is_staff)),
             str(int(user.is_superuser)), role, hidden_pages,
             tuple(sorted(user.groups.values_list('pk', flat=True))),
             tuple(sorted(user.user_permissions.values_list('pk', flat=True))))
    message = json.dumps(parts, separators=(',', ':'), ensure_ascii=False)
    return hmac.new(secret.encode('utf-8'), message.encode('utf-8'),
                    hashlib.sha256).hexdigest()


@require_POST
def mirror_auth_validate(request):
    """Check master identity + role/password version against remote snapshot.

    This endpoint is not a browser API. Master middleware verifies HMAC,
    timestamp, nonce and localhost tunnel before setting _mirror_forwarded.
    """
    response_headers = {'Cache-Control': 'no-store'}
    if getattr(settings, 'IS_MIRROR', False) or not getattr(request, '_mirror_forwarded', False):
        return JsonResponse({'valid': False}, status=403, headers=response_headers)
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not user.is_active:
        return JsonResponse({'valid': False}, status=403, headers=response_headers)
    try:
        payload = json.loads(request.body)
        uid = str(payload.get('uid', ''))
        supplied = payload.get('proof', '')
        expected = identity_proof(user)
        approved = (uid == str(user.pk) and isinstance(supplied, str)
                    and len(supplied) == 64 and bool(expected)
                    and hmac.compare_digest(supplied, expected))
    except (UnicodeError, ValueError, TypeError, AttributeError):
        approved = False
    if not approved:
        return JsonResponse({'valid': False}, status=403, headers=response_headers)
    return JsonResponse({'valid': True}, headers=response_headers)
