"""Stage 2 archive foundation; intentionally NO user-delete operation or endpoint.

Only call capture_actor_and_admin_log within an explicitly isolated/rehearsal
transaction until report attribution FKs and display fallbacks have been added.
These rows snapshot identity and admin actions; they do not themselves make
Django's User.delete() safe, since report PROTECT and audit CASCADE remain.
"""
from django.contrib.admin.models import LogEntry
from django.db import transaction
from monitor.models import ArchivedAdminLog, HistoricalActor, UserProfile


def actor_fields_for(user):
    """Read-only minimal historical signature identity, never auth secrets."""
    if user.pk is None:
        raise ValueError('Cannot archive an unsaved user.')
    profile = UserProfile.objects.filter(user_id=user.pk).first()
    display = user.get_full_name().strip() or user.username
    return {
        'original_user_id': user.pk,
        'username': user.username,
        'display_name': display,
        'designation': profile.designation if profile else '',
        'signature_phone': (
            (profile.mobile_number or profile.whatsapp_number) if profile else ''
        ),
        'profile_picture_path': (
            str(profile.profile_picture.name or '') if profile else ''
        ),
    }


@transaction.atomic
def capture_actor_and_admin_log(user):
    """Create immutable snapshots. Does not modify or remove any login.

    The eventual deletion service must ensure this is called immediately
    before deletion under a serialized transaction, not when a user is active
    and their profile could change. This proof-of-schema helper is test-only.
    """
    fields = actor_fields_for(user)
    actor, created = HistoricalActor.objects.get_or_create(
        original_user_id=user.pk,
        defaults={k: v for k, v in fields.items() if k != 'original_user_id'},
    )
    if not created and any(getattr(actor, key) != val for key, val in fields.items()):
        raise ValueError('Actor archive already exists with different identity fields.')
    for log in LogEntry.objects.filter(user_id=user.pk).order_by('pk').iterator():
        ArchivedAdminLog.objects.get_or_create(
            source_log_id=log.pk,
            defaults={
                'actor': actor,
                'action_time': log.action_time,
                'action_flag': log.action_flag,
                'original_content_type_id': log.content_type_id,
                'object_id': log.object_id,
                'object_repr': log.object_repr,
                'change_message': log.change_message,
            },
        )
    return actor


@transaction.atomic
def link_all_historical_relations(user):
    """Attach archived attribution while keeping User, report/status and existing FK intact.

    Intended only for isolated Phase B tests. Never deletes or disables a user.
    A later deletion service will call this after locking and before deletion.
    """
    from monitor.models import (ActivityLog, ProfileChangeRequest,
        NotificationRecipient, NotificationRequest, DutyRoster, OutageCycleRemark)
    from shiftreport.models import (ShiftHandoverContact, ShiftReport,
        HistoricalReportRevision, ShiftImportantIssue, MnocPfeReport)

    if user.pk is None:
        raise ValueError('Cannot link an unsaved user.')
    actor = capture_actor_and_admin_log(user)
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
    linked = {}
    for model, user_column, actor_column in references:
        matches = model.objects.filter(**{user_column: user.pk})
        # Conflicting independent attribution is a hard rollback condition.
        conflicting = (matches.exclude(**{actor_column+'__isnull': True})
                       .exclude(**{actor_column: actor.pk}).exists())
        if conflicting:
            raise ValueError(f'Conflicting archived attribution: {model.__name__}.{actor_column}')
        linked[model.__name__+'.'+actor_column] = matches.filter(
            **{actor_column+'__isnull': True}).update(**{actor_column: actor.pk})
    return actor, linked
