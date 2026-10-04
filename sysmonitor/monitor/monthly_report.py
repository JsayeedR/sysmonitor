"""
monitor/monthly_report.py
──────────────────────────
Builds the monthly loadshedding (power outage) report by aggregating every
day of the month through the same build_daily_summary() logic the daily
Generator Log report already uses — so generator attribution, midnight
clipping, and ongoing-cycle handling all stay consistent between the daily
and monthly reports.

Two things go out to recipients with monthly_report=True:
  - Email recipients get an Excel (.xlsx) workbook attached, plus a short
    HTML summary in the email body.
  - WhatsApp/Telegram recipients get a condensed text summary (no
    attachment support on those channels).
"""
import calendar
from datetime import date, datetime
from io import BytesIO

import pytz

BDT = pytz.timezone('Asia/Dhaka')


def build_monthly_summary(year, month):
    """
    Returns {
        'label': 'September 2026',
        'days': [ {date, totals per generator, grand_total, row_count}, ... one per day ],
        'month_totals': {'Gen-01': secs, 'Gen-02': secs, 'UNASSIGNED': secs},
        'grand_total': secs,
        'outage_count': int,
        'has_data': bool,
    }
    """
    from monitor.daily_summary import build_daily_summary

    _, last_day = calendar.monthrange(year, month)
    days = []
    month_totals = {'Gen-01': 0, 'Gen-02': 0, 'UNASSIGNED': 0}
    outage_count = 0

    for day_num in range(1, last_day + 1):
        d = date(year, month, day_num)
        summary = build_daily_summary(d)
        for gen, secs in summary['totals'].items():
            month_totals[gen] = month_totals.get(gen, 0) + secs
        outage_count += len(summary['rows'])
        days.append({
            'date': d,
            'date_str': d.strftime('%d/%m/%Y'),
            'weekday': d.strftime('%A'),
            'rows': summary['rows'],
            'totals': summary['totals'],
            'grand_total': summary['grand_total'],
            'row_count': len(summary['rows']),
        })

    grand_total = sum(month_totals.values())

    return {
        'label': date(year, month, 1).strftime('%B %Y'),
        'year': year,
        'month': month,
        'days': days,
        'month_totals': month_totals,
        'grand_total': grand_total,
        'outage_count': outage_count,
        'has_data': outage_count > 0,
    }


def fmt_duration(total_secs):
    total_secs = int(total_secs)
    h, r = divmod(total_secs, 3600)
    m, s = divmod(r, 60)
    if h:
        return f'{h}h {m:02d}m {s:02d}s'
    if m:
        return f'{m}m {s:02d}s'
    return f'{s}s'


def fmt_hours(total_secs):
    """Decimal-hours version for the Excel sheet (easier to chart/sum)."""
    return round(total_secs / 3600, 2)


def format_monthly_text(summary):
    """Short plain-text summary for WhatsApp/Telegram (no daily breakdown —
    just the monthly totals, which is all those channels can usefully show)."""
    lines = []
    width = 42
    lines.append('-' * width)
    lines.append(f"⚡ Loadshedding Report: {summary['label']}")
    lines.append('-' * width)
    if not summary['has_data']:
        lines.append('No outages recorded this month — all systems normal.')
        lines.append('-' * width)
        return '\n'.join(lines)

    lines.append(f"Total Outage Events: {summary['outage_count']}")
    lines.append(f"Total Downtime: {fmt_duration(summary['grand_total'])}")
    lines.append('')
    for gen in ('Gen-01', 'Gen-02'):
        secs = summary['month_totals'].get(gen, 0)
        if secs:
            lines.append(f"{gen}: {fmt_duration(secs)}")
    if summary['month_totals'].get('UNASSIGNED', 0) > 0:
        lines.append(f"Unassigned: {fmt_duration(summary['month_totals']['UNASSIGNED'])}")
    lines.append('-' * width)
    lines.append('Full day-by-day breakdown attached (email) / see dashboard.')
    lines.append('-' * width)
    return '\n'.join(lines)


def generate_excel_report(summary):
    """Returns the .xlsx file as raw bytes — one row per day, plus a totals
    section, plus a second sheet with every individual outage event."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    # ── Sheet 1: Daily Summary ──────────────────────────────────────────
    ws = wb.active
    ws.title = 'Daily Summary'

    header_fill = PatternFill(start_color='1D4ED8', end_color='1D4ED8', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True)
    total_fill  = PatternFill(start_color='DBEAFE', end_color='DBEAFE', fill_type='solid')
    thin = Side(style='thin', color='CBD5E1')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws['A1'] = f"SysMonitor — Loadshedding Report: {summary['label']}"
    ws['A1'].font = Font(bold=True, size=14)
    ws.merge_cells('A1:F1')
    ws['A2'] = f"Generated {datetime.now(BDT).strftime('%d/%m/%Y %I:%M %p')} BDT — COXCLS NOC, BSCPLC"
    ws['A2'].font = Font(italic=True, size=9, color='64748B')
    ws.merge_cells('A2:F2')

    headers = ['Date', 'Day', 'Outage Events', 'Gen-01 (hrs)', 'Gen-02 (hrs)', 'Total Downtime']
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=4, column=col, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal='center')
        c.border = border

    row = 5
    for d in summary['days']:
        ws.cell(row=row, column=1, value=d['date_str']).border = border
        ws.cell(row=row, column=2, value=d['weekday']).border = border
        ws.cell(row=row, column=3, value=d['row_count']).border = border
        ws.cell(row=row, column=4, value=fmt_hours(d['totals'].get('Gen-01', 0))).border = border
        ws.cell(row=row, column=5, value=fmt_hours(d['totals'].get('Gen-02', 0))).border = border
        ws.cell(row=row, column=6, value=fmt_duration(d['grand_total'])).border = border
        row += 1

    # Totals row
    ws.cell(row=row, column=1, value='TOTAL').font = Font(bold=True)
    ws.cell(row=row, column=3, value=summary['outage_count']).font = Font(bold=True)
    ws.cell(row=row, column=4, value=fmt_hours(summary['month_totals'].get('Gen-01', 0))).font = Font(bold=True)
    ws.cell(row=row, column=5, value=fmt_hours(summary['month_totals'].get('Gen-02', 0))).font = Font(bold=True)
    ws.cell(row=row, column=6, value=fmt_duration(summary['grand_total'])).font = Font(bold=True)
    for col in range(1, 7):
        ws.cell(row=row, column=col).fill = total_fill
        ws.cell(row=row, column=col).border = border

    widths = [14, 12, 15, 13, 13, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ── Sheet 2: Event Detail ───────────────────────────────────────────
    ws2 = wb.create_sheet('Event Detail')
    headers2 = ['Date', 'Start', 'End', 'Duration', 'Generator']
    for col, h in enumerate(headers2, start=1):
        c = ws2.cell(row=1, column=col, value=h)
        c.font = header_font
        c.fill = header_fill
        c.border = border

    row = 2
    for d in summary['days']:
        for r in d['rows']:
            ws2.cell(row=row, column=1, value=d['date_str']).border = border
            ws2.cell(row=row, column=2, value=r['start']).border = border
            ws2.cell(row=row, column=3, value=r['end']).border = border
            ws2.cell(row=row, column=4, value=fmt_duration(r['duration_sec'])).border = border
            ws2.cell(row=row, column=5, value=r['generator']).border = border
            row += 1

    for i, w in enumerate([14, 12, 12, 12, 12], start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def send_monthly_report(year=None, month=None):
    """
    Sends the report for the given year/month (defaults to the month that
    JUST ended, since this is meant to run on the 1st of the new month) to
    every active recipient with monthly_report=True.
    Returns the summary dict, for logging by the caller.
    """
    from monitor.models import NotificationGateway, NotificationRecipient
    from monitor.notifications import send_raw_email, send_whatsapp, send_telegram

    if year is None or month is None:
        now_bdt = datetime.now(BDT)
        first_of_this_month = now_bdt.replace(day=1)
        last_month_end = first_of_this_month.date()
        # step back one day to land in the previous month
        from datetime import timedelta
        prev = last_month_end - timedelta(days=1)
        year, month = prev.year, prev.month

    summary = build_monthly_summary(year, month)
    text = format_monthly_text(summary)
    excel_bytes = generate_excel_report(summary)
    excel_filename = f"loadshedding_report_{year}-{month:02d}.xlsx"

    gateways = {gw.channel: gw for gw in NotificationGateway.objects.filter(is_enabled=True)}
    recipients = NotificationRecipient.objects.filter(is_active=True, monthly_report=True)

    sent_count = 0
    failed = []

    for r in recipients:
        gw = gateways.get(r.channel)
        if not gw:
            failed.append(f'{r.name} ({r.channel}: no gateway configured)')
            continue

        if r.channel == 'email':
            html = (f"<html><body style='font-family:Arial,sans-serif;color:#1e293b;'>"
                    f"<h3>⚡ Loadshedding Report — {summary['label']}</h3>"
                    f"<p>{summary['outage_count']} outage event(s), "
                    f"{fmt_duration(summary['grand_total'])} total downtime this month.</p>"
                    f"<p>Full day-by-day breakdown is attached as an Excel workbook.</p>"
                    f"<p style='color:#64748b;font-size:12px;'>SysMonitor · COXCLS NOC, BSCPLC</p>"
                    f"</body></html>")
            ok, err = send_raw_email(
                gw, r.contact, f"⚡ Loadshedding Report — {summary['label']}",
                html, text,
                attachment=(excel_filename, excel_bytes,
                            'vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            )
        elif r.channel == 'whatsapp':
            ok, err = send_whatsapp(gw, r.contact, text)
        elif r.channel == 'telegram':
            ok, err = send_telegram(gw, r.contact, text)
        else:
            ok, err = False, f'Unsupported channel: {r.channel}'

        if ok:
            sent_count += 1
        else:
            failed.append(f'{r.name} ({r.channel}): {err}')

    return summary, sent_count, failed
