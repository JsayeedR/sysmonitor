"""Server-side Section C carry-forward. No future or draft sources.

Preference: previous two *operational shifts* in reverse order, then the
current engineer's newest older SUBMITTED/SENT report with actual activities.
A report older than three days remains eligible; searching continues backward
until an eligible report is found. No write occurs in this helper.
"""
from datetime import timedelta
from .models import ShiftReport
from .services import shift_window

ORDER = ('MORNING', 'EVENING', 'NIGHT')
ELIGIBLE = ('SUBMITTED', 'SENT')


def previous_slots(report_date, shift):
    if shift not in ORDER:
        raise ValueError('Invalid shift')
    pos = ORDER.index(shift)
    for _ in range(2):
        pos -= 1
        if pos < 0:
            pos = len(ORDER) - 1
            report_date -= timedelta(days=1)
        yield report_date, ORDER[pos]


def _has_rows(report):
    return any(
        any((row.activity_type, row.client_vendor, row.details,
             row.status, row.remarks))
        for row in report.activity_rows.all()
    )


def find_source(report_date, shift, engineer, exclude_id=None):
    if engineer is None or not getattr(engineer, 'pk', None):
        return None
    target_start, _ = shift_window(report_date, shift)
    base = (ShiftReport.objects.filter(status__in=ELIGIBLE,
                shift_start__lt=target_start)
            .exclude(pk=exclude_id) if exclude_id else
            ShiftReport.objects.filter(status__in=ELIGIBLE, shift_start__lt=target_start))
    for date, code in previous_slots(report_date, shift):
        candidate = (base.filter(report_date=date, shift=code)
                     .select_related('prepared_by', 'preparer_snapshot')
                     .prefetch_related('activity_rows').first())
        if candidate and _has_rows(candidate):
            return candidate
    # Fall back to the engineer's last completed report, scanning farther
    # backward if it has no Section C rows (including beyond three days).
    qs = (base.filter(prepared_by=engineer)
          .select_related('prepared_by', 'preparer_snapshot')
          .prefetch_related('activity_rows')
          .order_by('-shift_start', '-id'))
    for report in qs.iterator(chunk_size=100):
        if _has_rows(report):
            return report
    return None


def source_label(report):
    return '{} · {} · {}'.format(
        report.report_date.strftime('%d %b %Y'),
        report.get_shift_display(), report.prepared_by_display,
    )[:255]


def carry_forward_rows(report_date, shift, engineer, exclude_id=None):
    source = find_source(report_date, shift, engineer, exclude_id)
    if source is None:
        return []
    label = source_label(source)
    return [
        {
            'activity_type': row.activity_type,
            'client_vendor': row.client_vendor,
            'details': row.details,
            'status': row.status,
            'remarks': row.remarks,
            'source_reference': label,
        }
        for row in source.activity_rows.all()
        if any((row.activity_type, row.client_vendor, row.details,
                row.status, row.remarks))
    ]


MANUAL_MODES = ('mine', 'latest')

def find_manual_source(report_date, shift, engineer, mode, exclude_id=None):
    """Choose a strictly older submitted report; never copy draft/future data.

    `mine`: this engineer's latest SENT report with Section C rows.
    `latest`: newest available SENT/SUBMITTED report with Section C rows.
    """
    if mode not in MANUAL_MODES:
        raise ValueError('Unrecognized Section C source mode')
    start, _ = shift_window(report_date, shift)
    qs = (ShiftReport.objects.filter(status__in=ELIGIBLE, shift_start__lt=start)
          .select_related('prepared_by','preparer_snapshot')
          .prefetch_related('activity_rows').order_by('-shift_start','-id'))
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    if mode == 'mine':
        if engineer is None or not getattr(engineer, 'pk', None):
            return None
        qs = qs.filter(prepared_by=engineer, status='SENT')
    for report in qs.iterator(chunk_size=100):
        if _has_rows(report):
            return report
    return None

def rows_from_source(source):
    if source is None:
        return []
    label = source_label(source)
    return [
        {'activity_type': row.activity_type,
         'client_vendor': row.client_vendor, 'details': row.details,
         'status': row.status, 'remarks': row.remarks,
         'source_reference': label}
        for row in source.activity_rows.all()
        if any((row.activity_type,row.client_vendor,row.details,row.status,row.remarks))
    ]

def manual_source_rows(report_date, shift, engineer, mode, exclude_id=None):
    return rows_from_source(find_manual_source(
        report_date, shift, engineer, mode, exclude_id))
