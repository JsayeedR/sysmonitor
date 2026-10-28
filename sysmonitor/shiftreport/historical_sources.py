"""Safe, read-only source lookup for the NOC Historical editor.

A contact is an *identity*, not an editable data owner. Resolve by its
explicit User link or an unambiguous case-insensitive exact account email.
Never infer identity from similar names and never read another user's DRAFT.
"""
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import HistoricalReportRevision, ShiftHandoverContact


def user_for_contact(contact):
    if contact.user_id is not None:
        user = contact.user
        return user if user is not None and user.is_active else None
    email = (contact.email or '').strip()
    if not email:
        return None
    matches = list(get_user_model().objects.filter(
        email__iexact=email, is_active=True,
    ).order_by('pk')[:2])
    return matches[0] if len(matches) == 1 else None


def latest_sent(owner):
    if owner is None:
        return None
    return (HistoricalReportRevision.objects
            .filter(owner_id=owner.pk, status='SENT')
            .select_related('shift_report')
            .order_by('-sent_at', '-updated_at', '-pk').first())


def sent_source_metadata(sent):
    """Source metadata only; absent Shift Report linkage must remain explicit."""
    report = sent.shift_report if sent and sent.shift_report_id else None
    if report:
        start = timezone.localtime(report.shift_start) if report.shift_start else None
        end = timezone.localtime(report.shift_end) if report.shift_end else None
        shift_time = (
            f'{start:%d %b %Y, %I:%M %p} – {end:%d %b %Y, %I:%M %p}'
            if start and end else 'Not recorded'
        )
    else:
        shift_time = 'Not linked'
    return dict(
        available=sent is not None,
        last_updated=sent.updated_at if sent else None,
        last_sent=sent.sent_at if sent else None,
        report_date=report.report_date if report else None,
        shift_label=report.get_shift_display() if report else 'Not linked',
        shift_time=shift_time,
        entry_count=len(sent.rows) if sent and isinstance(sent.rows, list) else 0,
        revision_number=sent.revision_number if sent else None,
    )


def historical_source_choices(viewer):
    """Source selector metadata: SENT records only, no draft writes."""
    own = latest_sent(viewer)
    result = [dict(key='mine', name='My last SENT historical',
                   **sent_source_metadata(own))]
    for contact in (ShiftHandoverContact.objects
                    .filter(is_active=True).select_related('user')
                    .order_by('display_order', 'name', 'pk')):
        owner = user_for_contact(contact)
        if owner is not None and owner.pk == viewer.pk:
            continue  # Own account is represented by "mine".
        sent = latest_sent(owner)
        result.append(dict(key=f'contact:{contact.pk}',
                           name=contact.name,
                           **sent_source_metadata(sent)))
    return result


def resolve_historical_source(viewer, selection):
    """Returns (display_name, sent_revision), or raises ValueError.

    Source selection is validated independently from client-provided metadata.
    """
    if selection == 'mine':
        label, owner = 'My last SENT historical', viewer
    elif selection.startswith('contact:') and selection[8:].isascii() and selection[8:].isdigit():
        pk = int(selection[8:])
        if pk < 1:
            raise ValueError('Invalid historical source.')
        contact = (ShiftHandoverContact.objects
                   .filter(pk=pk, is_active=True)
                   .select_related('user').first())
        if contact is None:
            raise ValueError('Source officer is unavailable.')
        owner = user_for_contact(contact)
        if owner is not None and owner.pk == viewer.pk:
            # Same logged-in user is always represented by the "mine" option.
            label = 'My last SENT historical'
        else:
            label = contact.name
    else:
        raise ValueError('Invalid historical source.')
    sent = latest_sent(owner)
    return label, sent
