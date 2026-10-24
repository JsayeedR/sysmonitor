"""TEST DB ONLY. Exercises Django ORM deletion on synthetic accounts."""
from datetime import date
from unittest.mock import patch

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from monitor.models import (ActivityLog, ArchivedAdminLog, HistoricalActor,
                            NotificationRecipient, NotificationRequest,
                            ProfileChangeRequest, UserProfile)
from monitor.stage2_deletion import (AccountDeletionRehearsalDenied,
                                     delete_ordinary_account_in_test_db)
from shiftreport.models import HistoricalReportRevision, ShiftHandoverContact, ShiftReport


@override_settings(SYSMONITOR_STAGE2_TEST_REHEARSAL=True)
class TestStage2Rehearsal(TestCase):
    def setUp(self):
        self.root=User.objects.create_superuser('root-test', 'root@example.test', 'test-password-only')
        self.normal=User.objects.create_user('former-operator',password='test-password-only',
                                              first_name='Former',last_name='Operator')
        UserProfile.objects.create(user=self.normal, role='user', designation='Shift Engineer')
        self.log=ActivityLog.objects.create(user=self.normal,action='LOGIN',detail='test')
        self.request=NotificationRequest.objects.create(user=self.normal, status='APPROVED', pairing_code='TESTCODE1')
        self.recipient=NotificationRecipient.objects.create(user=self.normal,name='Test Recipient',
                                      channel='email',contact='test@example.test')
        self.profile_request=ProfileChangeRequest.objects.create(user=self.normal,field='email',
                                  old_value='a@example.test', new_value='b@example.test',status='APPROVED')
        self.contact=ShiftHandoverContact.objects.create(user=self.normal,name='Shift Engineer',
                                                          email='test@example.test')
        self.revision=HistoricalReportRevision.objects.create(owner=self.normal, created_by=self.normal,
                                  revision_number=1, status='SENT',
                                  rows=[{'category_1':'X','category_2':'Y','details':'Z',
                                         'remarks':'R','event_type':'E'}])
        self.adminlog=LogEntry.objects.create(user=self.normal, content_type=None,
                                           object_id='1', object_repr='Example',
                                           action_flag=ADDITION,change_message='test')

    def do_delete(self, user_id=None):
        return delete_ordinary_account_in_test_db(target_user_id=user_id or self.normal.pk,
                                                  authorizer_user_id=self.root.pk)

    def test_deletes_only_login_and_keeps_history(self):
        user_id=self.normal.pk
        actor=self.do_delete()
        self.assertFalse(User.objects.filter(pk=user_id).exists())
        self.assertEqual(actor.original_user_id,user_id)
        self.assertEqual(actor.display_name,'Former Operator')
        self.assertEqual(actor.designation,'Shift Engineer')
        self.assertEqual(HistoricalActor.objects.filter(original_user_id=user_id).count(),1)
        self.log.refresh_from_db()
        self.assertIsNone(self.log.user_id)
        self.assertEqual(self.log.actor_snapshot_id, actor.pk)
        self.assertEqual(self.log.actor_username,'former-operator')
        self.request.refresh_from_db()
        self.assertIsNone(self.request.user_id)
        self.assertEqual(self.request.requester_snapshot_id,actor.pk)
        self.assertEqual(self.request.pairing_code,'')
        self.profile_request.refresh_from_db()
        self.assertIsNone(self.profile_request.user_id)
        self.assertEqual(self.profile_request.status,'APPROVED')
        self.assertEqual(self.profile_request.requester_name,'former-operator')
        self.recipient.refresh_from_db()
        self.assertFalse(self.recipient.is_active)
        self.assertIsNone(self.recipient.user_id)
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.is_active)
        self.assertEqual(self.contact.name,'Shift Engineer')
        self.revision.refresh_from_db()
        self.assertIsNone(self.revision.owner_id)
        self.assertIsNone(self.revision.created_by_id)
        self.assertEqual(self.revision.owner_snapshot_id,actor.pk)
        self.assertEqual(self.revision.status,'SENT')
        self.assertEqual(self.revision.rows[0]['details'],'Z')
        self.assertFalse(LogEntry.objects.filter(pk=self.adminlog.pk).exists())
        self.assertTrue(ArchivedAdminLog.objects.filter(actor=actor,source_log_id=self.adminlog.pk).exists())

    def test_denies_superadmin_and_fullviewer(self):
        with self.assertRaises(AccountDeletionRehearsalDenied):
            self.do_delete(self.root.pk)
        self.normal.username='fullviewer@sysmonitor'
        self.normal.save(update_fields=['username'])
        with self.assertRaises(AccountDeletionRehearsalDenied):
            self.do_delete()

    def test_denies_regular_admin_role(self):
        self.normal.userprofile.role='admin'
        self.normal.userprofile.save(update_fields=['role'])
        with self.assertRaises(AccountDeletionRehearsalDenied):
            self.do_delete()

    def test_denies_report_author_until_exact_signature_supported(self):
        from django.utils import timezone
        from datetime import timedelta
        t=timezone.now()
        ShiftReport.objects.create(report_date=date(2026,10,20), shift='NIGHT',
          shift_start=t-timedelta(hours=8),shift_end=t-timedelta(hours=2),
          prepared_by=self.normal,handover_to=self.contact,status='SENT')
        with self.assertRaisesRegex(AccountDeletionRehearsalDenied, 'sent-time signatures'):
            self.do_delete()
        self.assertTrue(User.objects.filter(pk=self.normal.pk).exists())
        self.assertEqual(HistoricalActor.objects.count(),0)

    def test_rolls_back_all_deletion_if_archive_integrity_check_fails(self):
        with patch('monitor.stage2_deletion._assert_snapshot_links',side_effect=RuntimeError('failed-test')):
            with self.assertRaises(RuntimeError):
                self.do_delete()
        self.assertTrue(User.objects.filter(pk=self.normal.pk).exists())
        self.assertEqual(HistoricalActor.objects.count(),0)
        self.assertEqual(ArchivedAdminLog.objects.count(),0)
        self.log.refresh_from_db()
        self.assertIsNone(self.log.actor_snapshot_id)

    @override_settings(SYSMONITOR_STAGE2_TEST_REHEARSAL=False)
    def test_refuses_deletion_without_explicit_rehearsal_mode(self):
        with self.assertRaisesRegex(AccountDeletionRehearsalDenied,'exclusively'):
            self.do_delete()
        self.assertTrue(User.objects.filter(pk=self.normal.pk).exists())
