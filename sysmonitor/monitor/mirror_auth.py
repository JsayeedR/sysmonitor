"""
Login check for the REMOTE site that never writes to the database copy.

Django's normal backend silently re-saves a user's password hash when the hash
format is older than the current default. On the remote that would be a write
to a throw-away copy (and the upgrade would be lost). This backend only checks
the password; the master upgrades hashes when people log in there.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.hashers import check_password


class ReadOnlyModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None
        try:
            user = UserModel._default_manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
            UserModel().set_password(password)      # same timing as a real check; nothing is saved
            return None
        if check_password(password, user.password) and self.user_can_authenticate(user):
            return user
        return None
