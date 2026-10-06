from django.apps import AppConfig


class MonitorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'monitor'

    def ready(self):
        from django.conf import settings
        if getattr(settings, 'IS_MIRROR', False):
            # The remote never writes to its database copy, so do not let
            # Django record "last login" there (the master records the login).
            from django.contrib.auth.models import update_last_login
            from django.contrib.auth.signals import user_logged_in
            user_logged_in.disconnect(update_last_login, dispatch_uid='update_last_login')
