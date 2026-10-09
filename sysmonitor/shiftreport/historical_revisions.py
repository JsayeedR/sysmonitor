"""Version management for historical report attachments."""

from copy import deepcopy

from django.db import transaction
from django.utils import timezone

from .models import HistoricalReportRevision


ORIGINAL_HISTORICAL_ROWS = [{'category_1': 'NOC', 'category_2': 'Cooling at Equipment room', 'details': 'High temp alarm on COX-SAT-6500_SH1 SL9', 'remarks': '">10-06-2026 Alarm appeared due to less cooling around SLTE isle. Post Hard Reset alarm cleared. Additinally, run stand fan to cool down (fan also need maintenance) ***This board does not contain any traffic right now. If any traffic carrying board experience such issue, full traffic within the card will be down instantly. (For East such each card carrying 300G & for West such each card carrying 400G) >11-06-2026 MNOC reported same alarm on multiple TRM. No alarm observed on COX terminal Replied the same in email & TT (7N110626), asked to share alarm log >12-06-2026 No alarm appears"', 'event_type': 'Reporting'}, {'category_1': 'MNOC', 'category_2': '10G Tester', 'details': 'Testing revealed that the tester is faulty.', 'remarks': '"09-05-2026 >Test was clear but test report can not be saved due to appearing an error message. MNOC has been informed. Tried application restart and tester reboot but no luck. 13-05-2026 >Query update from MNOC regarding repair procedure. 19-05-2026 >Ask MNOC for update 20-05-2026 >Share error details with MNOC again for checking repair scope 22-06-2026 >MNOC suggested to check this scope with O&MSC on quarterly conference call"', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Power', 'details': 'DC Distribution Capacity Assessment', 'remarks': '"Contact HS Engineering for costing of Rectifier module. Cost is yet to receive."', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Co-location', 'details': 'Removal of BTCL dismantled equipment from station.', 'remarks': '">According to Mr. Hasib (DGM-Tx), they are waiting for BSCPLC reply on their letter regarding removal. >Requested Mr. Hasib to communicate with respective department and accelerate removal process."', 'event_type': 'Reporting'}, {'category_1': 'MNOC', 'category_2': 'Secure VPN solution proposal', 'details': '"Red Sea Cable Cut: To restore SMW4 Network Supervision => Secure VPN solution proposal- COX IIG propose conference call for clarification."', 'remarks': '">MNOC requested their queries on 28/11/25 >COX NOC requests IIG for taking care of the queries on 03/12/25 >IIG requests for conference call on 10/12/25 >Conference call organized on 14/12/25 >IIG unit requested to discuss price schedule with Marketing team on 30/12/25 >COX NOC loop MNS team for price schedule on 01/01/26 >Ask MNS for update on 7 JAN 25. >MNS team will share the cost after including into current tariff plan post discussion with IIG team. > Asked MNS for update on 18 JAN 26 Again"', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Air Conditioner', 'details': 'Cooling at NOC equipment room', 'remarks': '**AC-7: Compressor damaged since August, 2024.\n**AC-12: is not running post burning smell issue at PFE room.\n**AC-1: Compressor issue', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Air Conditioner', 'details': 'Cooling at Co-location', 'remarks': "*** Timers have been installed for 2X2 ton AC's and 1X5 ton AC on 28/10/2025. After the installation:\nminimum running capacity: 6 ton (including Summit's 2-ton AC)\nmaximum running capacity: 11 ton (including Summit's 2-ton AC) ", 'event_type': 'Followup'}, {'category_1': 'Power', 'category_2': 'Rectifier', 'details': 'Rectifier Module', 'remarks': 'An alarm is being observed on Rectifier-A, Module 25. Removed on 02/02/2026 (stored in store)', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Roof water leakage', 'details': "It didn't rain during the shift.", 'remarks': '', 'event_type': 'Reporting'}, {'category_1': 'NOC', 'category_2': 'Automatic Fire Control System', 'details': 'No active alarms. Cylinder pressure is fine on indicator.', 'remarks': '', 'event_type': 'Reporting'}, {'category_1': 'NOC', 'category_2': 'Colocation Room & Generator Room', 'details': '"For proper monitoring and record-keeping, we urgently need at least two cameras in the colocation room to cover the rack faces, especially when someone works alone. Additionally, one camera is required in the generator room to cover the ATS, AVR, and transformer area."', 'remarks': '"Camera Coverage in Colocation Room & Generator Room Remarks: Additional cameras are required to achieve complete monitoring coverage in the colocation room and generator room."', 'event_type': 'Followup'}]

def get_personal_draft(user):
    """Return active per-user DRAFT, initializing from the 11-row seed."""
    with transaction.atomic():
        qs = HistoricalReportRevision.objects.filter(owner=user)
        active = qs.filter(status='DRAFT', shift_report__isnull=True).order_by('-revision_number').first()
        if active is not None:
            return active
        previous = qs.order_by('-revision_number').first()
        if previous is None:
            # Personal V1 always comes from the original uploaded workbook.
            # The legacy global V1 may now be edited/reserved/sent and must
            # be preserved untouched rather than used as a changing seed.
            number, data = 1, deepcopy(ORIGINAL_HISTORICAL_ROWS)
        else:
            number, data = previous.revision_number + 1, deepcopy(previous.rows)
        result, _ = HistoricalReportRevision.objects.get_or_create(
            owner=user, revision_number=number,
            defaults={'status': 'DRAFT', 'rows': data, 'created_by': user},
        )
        return result


@transaction.atomic
def save_personal_draft(revision, owner, rows):
    """Keep old version SAVED/read-only; each save produces a new DRAFT."""
    if revision.owner_id != owner.pk or revision.status != 'DRAFT':
        raise ValueError('Only your active historical draft can be saved.')
    if revision.shift_report_id is not None:
        raise ValueError('This revision is reserved for email and cannot be edited.')
    if HistoricalReportRevision.objects.filter(owner=owner, revision_number__gt=revision.revision_number).exists():
        raise ValueError('This is an outdated historical revision. Reload the page.')
    revision.status = 'SAVED'
    revision.save(update_fields=['status', 'updated_at'])
    next_revision = HistoricalReportRevision.objects.create(
        owner=owner, created_by=owner,
        revision_number=revision.revision_number + 1,
        status='DRAFT', rows=deepcopy(rows),
    )
    return next_revision


@transaction.atomic
def freeze_sent_historical_revision(revision_id, shift_report):
    """Freeze precisely the reserved attachment after main SMTP success."""
    revision = HistoricalReportRevision.objects.select_for_update().get(pk=revision_id)
    if revision.shift_report_id != shift_report.pk:
        raise ValueError('The attachment revision belongs to another report.')
    if revision.owner_id not in (None, shift_report.prepared_by_id):
        raise ValueError("Cannot freeze another engineer's attachment.")
    if revision.status == 'SENT':
        return revision
    if revision.status != 'DRAFT':
        raise ValueError('Only an attached DRAFT can be marked SENT.')
    revision.status = 'SENT'
    revision.sent_at = timezone.now()
    revision.save(update_fields=['status', 'sent_at', 'updated_at'])
    if revision.owner_id is not None:
        get_personal_draft(shift_report.prepared_by)
    return revision


def reserve_historical_revision(report):
    """Reserve the prepared engineer's draft, never somebody else's."""
    with transaction.atomic():
        existing = HistoricalReportRevision.objects.filter(shift_report=report).order_by('-id').first()
        if existing is not None:
            if existing.owner_id not in (None, report.prepared_by_id):
                raise ValueError('Historical attachment owner mismatch.')
            return existing
        active = get_personal_draft(report.prepared_by)
        changed = HistoricalReportRevision.objects.filter(
            pk=active.pk, owner=report.prepared_by,
            status='DRAFT', shift_report__isnull=True,
        ).update(shift_report=report)
        if changed != 1:
            raise ValueError('Historical draft was reserved elsewhere; please retry.')
        active.refresh_from_db()
        return active
