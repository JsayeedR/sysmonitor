"""RC8 tests validate archive pointers while original user/report FKs remain intact."""
from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from monitor.historical_identity import link_all_historical_relations
from monitor.models import ActivityLog, NotificationRequest, ProfileChangeRequest, UserProfile
from shiftreport.models import (ShiftHandoverContact, ShiftReport,
                                MnocPfeReport, HistoricalReportRevision)

class HistoricalRelationshipTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('reporter-archive',password='synthetic-test-only',
                                          first_name='Report',last_name='Engineer')
        UserProfile.objects.create(user=self.user,role='user',designation='Engineer')
        self.activity=ActivityLog.objects.create(user=self.user,action='LOGIN',detail='synthetic')
        self.notification=NotificationRequest.objects.create(user=self.user)
        self.profile_request=ProfileChangeRequest.objects.create(user=self.user,field='email',
                                                   old_value='old@example.test',new_value='new@example.test')
        self.contact=ShiftHandoverContact.objects.create(user=self.user,name='Synthetic handover',
                                                          email='handover@example.test')
        now=timezone.now()
        self.report=ShiftReport.objects.create(report_date=date(2026,10,20),shift='MORNING',
            shift_start=now-timedelta(hours=10),shift_end=now-timedelta(hours=2),
            prepared_by=self.user,handover_to=self.contact,status='SENT')
        self.pfe=MnocPfeReport.objects.create(report_date=date(2026,10,20),
            prepared_by=self.user,voltage_v=Decimal('12.00'),current_ma=Decimal('13.00'),status='SENT')
        self.revision=HistoricalReportRevision.objects.create(owner=self.user,created_by=self.user,
                    revision_number=1,status='SENT',rows=[{'category_1':'sample'}])

    def test_links_and_statuses_survive_without_changing_logins(self):
        user_id=self.user.pk
        report_id=self.report.pk
        old_rows=list(self.revision.rows)
        actor,links=link_all_historical_relations(self.user)
        self.assertEqual(actor.original_user_id,user_id)
        self.assertTrue(User.objects.filter(pk=user_id).exists())
        self.report.refresh_from_db();self.pfe.refresh_from_db();self.revision.refresh_from_db()
        self.assertEqual(self.report.pk,report_id)
        self.assertEqual(self.report.status,'SENT')
        self.assertEqual(self.pfe.status,'SENT')
        self.assertEqual(self.revision.status,'SENT')
        self.assertEqual(self.revision.rows,old_rows)
        self.assertEqual(self.report.preparer_snapshot_id,actor.pk)
        self.assertEqual(self.pfe.preparer_snapshot_id,actor.pk)
        self.assertEqual(self.revision.owner_snapshot_id,actor.pk)
        self.assertEqual(self.revision.creator_snapshot_id,actor.pk)
        self.activity.refresh_from_db();self.notification.refresh_from_db()
        self.profile_request.refresh_from_db()
        self.assertEqual(self.activity.actor_snapshot_id,actor.pk)
        self.assertEqual(self.notification.requester_snapshot_id,actor.pk)
        self.assertEqual(self.profile_request.requester_snapshot_id,actor.pk)
        self.assertEqual(self.report.prepared_by_display,'Report Engineer')
        self.assertEqual(self.pfe.prepared_by_display,'Report Engineer')
        self.assertGreaterEqual(sum(links.values()),8)

    def test_linking_idempotent(self):
        first,_=link_all_historical_relations(self.user)
        second,counts=link_all_historical_relations(self.user)
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(sum(counts.values()),0)

    def test_missing_live_author_uses_archive_display(self):
        actor,_=link_all_historical_relations(self.user)
        # link_all_historical_relations uses QuerySet.update(), which does not
        # modify Python model instances already loaded in memory. Reload the
        # snapshot IDs exactly as a subsequent request would read them.
        self.report.refresh_from_db()
        self.pfe.refresh_from_db()
        self.activity.refresh_from_db()
        self.assertEqual(self.report.preparer_snapshot_id,actor.pk)
        self.assertEqual(self.pfe.preparer_snapshot_id,actor.pk)
        self.assertEqual(self.activity.actor_snapshot_id,actor.pk)
        # Simulate removal of the login FK in memory only. The migration
        # phase intentionally keeps the original FK fields unchanged.
        self.report.prepared_by_id=None
        self.pfe.prepared_by_id=None
        self.assertEqual(self.report.prepared_by_display,actor.display_name)
        self.assertEqual(self.pfe.prepared_by_display,actor.display_name)
        self.activity.user_id=None
        self.assertEqual(self.activity.actor_username,actor.username)

    def test_archive_mismatch_rolls_back(self):
        actor,_=link_all_historical_relations(self.user)
        self.user.first_name='Changed'
        self.user.save(update_fields=['first_name'])
        with self.assertRaises(ValueError):
            link_all_historical_relations(self.user)
        self.report.refresh_from_db()
        self.assertEqual(self.report.preparer_snapshot_id,actor.pk)
