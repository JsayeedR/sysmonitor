"""Proof-of-safety account removal for Django's IN-MEMORY test database ONLY.

Not registered as a URL, management command, scheduled task, admin action, or
service. Fails closed for on-disk databases including the actual SysMonitor
master and remote mirror. Not suitable for production account deletion yet:
Shift/PFE previews and original sent-time signatures require further work.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction

from monitor.access_policy import FULL_VIEWER_USERNAME, is_full_viewer
from monitor.historical_identity import link_all_historical_relations


class AccountDeletionRehearsalDenied(PermissionError):
    pass


def _require_in_memory_test_db():
    name = str(connection.settings_dict.get('NAME') or '')
    # Django 5 SQLite tests use a shared-cache memory database. The second
    # explicit flag makes accidental calls through a normal manage.py shell
    # impossible, including when the master uses a SQLite disk file.
    if not (getattr(settings, 'SYSMONITOR_STAGE2_TEST_REHEARSAL', False)
            and (name == ':memory:' or name.startswith('file:memorydb_'))):
        raise AccountDeletionRehearsalDenied(
            'Stage 2 account deletion is available exclusively in an isolated '
            'in-memory Django test database.'
        )


def _assert_snapshot_links(target_id, actor):
    from monitor.models import (ActivityLog, ProfileChangeRequest,
        NotificationRequest, NotificationRecipient, DutyRoster, OutageCycleRemark)
    from shiftreport.models import (ShiftHandoverContact, ShiftReport,
        HistoricalReportRevision, ShiftImportantIssue, MnocPfeReport)

    references = [
        (ActivityLog, 'user_id', 'actor_snapshot_id'),
        (ProfileChangeRequest, 'user_id', 'requester_snapshot_id'),
        (NotificationRecipient, 'user_id', 'account_snapshot_id'),
        (NotificationRequest, 'user_id', 'requester_snapshot_id'),
        (DutyRoster, 'imported_by_id', 'importer_snapshot_id'),
        (OutageCycleRemark, 'author_id', 'author_snapshot_id'),
        (ShiftHandoverContact, 'user_id', 'contact_snapshot_id'),
        (ShiftReport, 'prepared_by_id', 'preparer_snapshot_id'),
        (HistoricalReportRevision, 'owner_id', 'owner_snapshot_id'),
        (HistoricalReportRevision, 'created_by_id', 'creator_snapshot_id'),
        (ShiftImportantIssue, 'added_by_id', 'creator_snapshot_id'),
        (MnocPfeReport, 'prepared_by_id', 'preparer_snapshot_id'),
    ]
    snapshot_totals = {}
    for model, old, new in references:
        total = model.objects.filter(**{old: target_id}).count()
        archived = model.objects.filter(**{old: target_id, new: actor.pk}).count()
        if total != archived:
            raise RuntimeError('Incomplete actor attribution archive: ' + model.__name__)
        snapshot_totals[(model, old, new)] = total
    return snapshot_totals


@transaction.atomic
def delete_ordinary_account_in_test_db(*, target_user_id, authorizer_user_id):
    """Execute full ORM deletion only on synthetic test data; never on disk.

    The public user-delete endpoint intentionally does NOT call this helper.
    Denies accounts with any Shift/PFE reports until immutable, sent-time
    signature HTML and all preview/export paths are ready for archived authors.
    """
    _require_in_memory_test_db()
    User = get_user_model()
    authorizer = User.objects.select_for_update().get(pk=authorizer_user_id)
    target = User.objects.select_for_update().get(pk=target_user_id)
    if not authorizer.is_active or not authorizer.is_superuser:
        raise AccountDeletionRehearsalDenied('Only an active Superadmin may authorize rehearsal deletion.')
    if target.pk == authorizer.pk or target.is_superuser or target.is_staff:
        raise AccountDeletionRehearsalDenied('Self-deletion and privileged accounts are protected.')
    if (target.username.casefold() == FULL_VIEWER_USERNAME or is_full_viewer(target)):
        raise AccountDeletionRehearsalDenied('The reserved Full Viewer account is protected.')
    role = getattr(getattr(target, 'userprofile', None), 'role', 'viewer')
    if role in {'admin', 'fullviewer'}:
        raise AccountDeletionRehearsalDenied('Admin and Full Viewer accounts are protected.')

    from monitor.models import (ActivityLog, NotificationRequest, NotificationRecipient,
                                HistoricalActor, ArchivedAdminLog)
    from shiftreport.models import (ShiftReport, MnocPfeReport, ShiftHandoverContact,
                                    HistoricalReportRevision)
    from django.contrib.admin.models import LogEntry

    if ShiftReport.objects.filter(prepared_by=target).exists() or MnocPfeReport.objects.filter(prepared_by=target).exists():
        raise AccountDeletionRehearsalDenied(
            'Report creators cannot be removed until original sent-time signatures '
            'and every report view/export are preserved independently of login accounts.'
        )

    historic_count_before = HistoricalReportRevision.objects.count()
    activity_count_before = ActivityLog.objects.count()
    notification_count_before = NotificationRequest.objects.count()
    admin_logs_before = LogEntry.objects.filter(user=target).count()
    original_user_id = target.pk

    actor, _ = link_all_historical_relations(target)
    totals = _assert_snapshot_links(original_user_id, actor)
    if ArchivedAdminLog.objects.filter(actor=actor).count() != admin_logs_before:
        raise RuntimeError('Admin audit archival incomplete; account deletion aborted.')

    # This is NOT deletion of operational notification history: only a live
    # subscription is disabled and a one-time pairing token made unusable.
    NotificationRecipient.objects.filter(user=target).update(is_active=False)
    NotificationRequest.objects.filter(user=target).update(pairing_code='', pairing_created_at=None)
    ShiftHandoverContact.objects.filter(user=target).update(is_active=False)

    # Django's ORM cascades UserProfile, sessions and built-in django_admin_log;
    # all historical project FKs have been made nullable and independently linked.
    target.delete()
    if User.objects.filter(pk=original_user_id).exists():
        raise RuntimeError('Account remained after deletion.')

    for (model, old, new), expected in totals.items():
        if model.objects.filter(**{old: original_user_id}).exists():
            raise RuntimeError('Orphaned live-user reference: ' + model.__name__)
        if model.objects.filter(**{new: actor.pk}).count() < expected:
            raise RuntimeError('Historical records were lost: ' + model.__name__)

    if HistoricalReportRevision.objects.count() != historic_count_before:
        raise RuntimeError('Historical revision records were lost.')
    if NotificationRequest.objects.count() != notification_count_before:
        raise RuntimeError('Notification requests were lost.')
    if ActivityLog.objects.count() != activity_count_before:
        raise RuntimeError('Activity logs were lost.')
    if ArchivedAdminLog.objects.filter(actor=actor).count() != admin_logs_before:
        raise RuntimeError('Archived Django admin audit records were lost.')

    # Deletion audit belongs to the active Superadmin, not the deleted actor.
    ActivityLog.objects.create(
        user=authorizer,
        action='USER_DELETED',
        detail=f'Isolated Stage 2 test deletion of login ID {original_user_id}; '
               f'archived HistoricalActor ID {actor.pk}.',
    )
    return actor
