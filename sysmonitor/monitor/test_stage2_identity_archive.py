"""Isolated Stage 2 archival-schema tests. No User.delete() is called."""
from django.contrib.admin.models import LogEntry, ADDITION
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from monitor.historical_identity import actor_fields_for, capture_actor_and_admin_log
from monitor.models import (ActivityLog, ArchivedAdminLog, HistoricalActor,
                            ProfileChangeRequest, UserProfile)


class HistoricalIdentityArchiveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('engineer-archive-test',
                                             password='Test-only-password-not-stored',
                                             first_name='Test', last_name='Engineer')
        UserProfile.objects.create(user=self.user, role='user',
                                   designation='Shift Engineer',
                                   mobile_number='0000000000')
        self.activity = ActivityLog.objects.create(user=self.user, action='LOGIN',
                                                    detail='Test identity logged in')
        self.change = ProfileChangeRequest.objects.create(
            user=self.user, field='mobile', old_value='0', new_value='1')
        LogEntry.objects.create(user=self.user, action_flag=ADDITION,
                                content_type=ContentType.objects.get_for_model(User),
                                object_id=str(self.user.pk), object_repr='test-account',
                                change_message='test-admin-audit')

    def test_snapshot_does_not_capture_password_or_token(self):
        values = actor_fields_for(self.user)
        self.assertEqual(values['display_name'], 'Test Engineer')
        self.assertEqual(values['designation'], 'Shift Engineer')
        self.assertEqual(values['signature_phone'], '0000000000')
        self.assertFalse(any('password' in key or 'token' in key for key in values))
        self.assertNotIn(self.user.password, repr(values))

    def test_capture_preserves_login_and_live_history(self):
        actor = capture_actor_and_admin_log(self.user)
        self.assertEqual(HistoricalActor.objects.count(), 1)
        self.assertEqual(ArchivedAdminLog.objects.count(), 1)
        self.assertEqual(ArchivedAdminLog.objects.get().actor_id, actor.pk)
        self.assertEqual(LogEntry.objects.filter(user_id=self.user.pk).count(), 1)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(pk=self.activity.pk).exists())
        self.assertTrue(ProfileChangeRequest.objects.filter(pk=self.change.pk).exists())

    def test_snapshot_capture_idempotent(self):
        first = capture_actor_and_admin_log(self.user)
        second = capture_actor_and_admin_log(self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(HistoricalActor.objects.count(), 1)
        self.assertEqual(ArchivedAdminLog.objects.count(), 1)

    def test_snapshot_cannot_be_updated_through_model_save(self):
        actor = capture_actor_and_admin_log(self.user)
        actor.username = 'forbidden-change'
        with self.assertRaises(ValueError):
            actor.save()
        item = ArchivedAdminLog.objects.first()
        item.change_message = 'forbidden-change'
        with self.assertRaises(ValueError):
            item.save()
