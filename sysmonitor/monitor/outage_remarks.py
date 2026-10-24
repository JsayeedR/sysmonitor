"""Engineer remark page. No modifications to OutageCycle records."""
from datetime import datetime, timedelta, date
import csv
from io import BytesIO
from urllib.parse import urlencode
from xml.sax.saxutils import escape
import pytz
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpResponseForbidden, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import OutageCycle, OutageCycleRemark
from .views import get_role

BDT = pytz.timezone('Asia/Dhaka')

def _range_dates(name, today):
    week_start = today - timedelta(days=today.weekday())
    if name == 'this_week':
        return week_start, today
    if name == 'last_week':
        return week_start - timedelta(days=7), week_start - timedelta(days=1)
    if name == 'this_month':
        return today.replace(day=1), today
    if name == 'last_month':
        first = today.replace(day=1)
        return (first - timedelta(days=1)).replace(day=1), first - timedelta(days=1)
    if name == 'this_year':
        return today.replace(month=1, day=1), today
    return None, None


@login_required
def remark_list(request):
    role = get_role(request.user)
    if role not in ('user', 'admin'):
        return HttpResponseForbidden('Unauthorized')

    today = timezone.localtime(timezone.now(), BDT).date()
    range_name = request.GET.get('range', 'this_week')
    presets = ('this_week', 'last_week', 'this_month', 'last_month', 'this_year', 'all_time', 'last_10', 'custom')
    if range_name not in presets:
        return HttpResponseForbidden('Invalid range')

    from_raw = request.GET.get('from', '')
    to_raw = request.GET.get('to', '')
    if (from_raw or to_raw) and 'range' not in request.GET:
        range_name = 'custom'
    if range_name == 'custom':
        try:
            start_date = date.fromisoformat(from_raw) if from_raw else None
            end_date = date.fromisoformat(to_raw) if to_raw else None
        except ValueError:
            return HttpResponseForbidden('Invalid date filter')
    else:
        start_date, end_date = _range_dates(range_name, today)

    if start_date and end_date and start_date > end_date:
        return HttpResponseForbidden('From date must not be later than To date')

    cycles = OutageCycle.objects.prefetch_related('engineer_remarks__author').order_by('-outage_start')
    if start_date:
        cycles = cycles.filter(outage_start__gte=BDT.localize(datetime.combine(start_date, datetime.min.time())))
    if end_date:
        cycles = cycles.filter(outage_start__lt=BDT.localize(datetime.combine(end_date + timedelta(days=1), datetime.min.time())))
    limit = 10 if range_name == 'last_10' else 150
    cycles = list(cycles[:limit])

    export_format = request.GET.get('format', '')
    if export_format:
        if request.user.username.lower() == 'fullviewer@sysmonitor':
            return HttpResponseForbidden('Downloads disabled for Full Viewer')
        if export_format not in ('csv', 'pdf'):
            return HttpResponseForbidden('Invalid format')
        rows = []
        for cycle in cycles:
            remarks = ' | '.join(f'{item.author_name}: {item.text}' for item in cycle.engineer_remarks.all())
            rows.append([str(cycle.pk), timezone.localtime(cycle.outage_start, BDT).strftime('%d %b %Y %I:%M %p'),
                         str(cycle.pdb_duration_fmt()), str(cycle.cycle_type), remarks])
        headers = ['Cycle', 'Start (BDT)', 'Duration', 'Type', 'Remarks / Notes']
        if export_format == 'csv':
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = 'attachment; filename="outage_cycle_remarks.csv"'
            response.write('\ufeff')
            writer = csv.writer(response)
            writer.writerow(headers)
            # Prevent CSV formula injection for user-generated remarks.
            for row in rows:
                writer.writerow(["'" + cell if cell.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else cell for cell in row])
            return response
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
        bio = BytesIO()
        pdf = SimpleDocTemplate(bio, pagesize=landscape(A4), leftMargin=20, rightMargin=20)
        styles = getSampleStyleSheet()
        body = styles['BodyText']
        body.fontSize = 8
        table_rows = [[Paragraph(escape(x), body) for x in headers]]
        for row in rows:
            table_rows.append([Paragraph(escape(x).replace('\n', '<br/>'), body) for x in row])
        table = Table(table_rows, colWidths=[42, 115, 80, 75, 460], repeatRows=1, hAlign='LEFT')
        table.setStyle(TableStyle([('GRID', (0,0),(-1,-1),0.3,colors.grey),
                                   ('BACKGROUND',(0,0),(-1,0),colors.lightgrey),
                                   ('VALIGN',(0,0),(-1,-1),'TOP')]))
        pdf.build([table])
        response = HttpResponse(bio.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="outage_cycle_remarks.pdf"'
        return response

    query = {'range': range_name}
    if range_name == 'custom':
        query['from'] = from_raw
        query['to'] = to_raw
    return render(request, 'monitor/outage_remarks.html', {
        'cycles': cycles, 'is_admin':role == 'admin', 'role':role,
        'from_date':start_date.isoformat() if start_date else '',
        'to_date':end_date.isoformat() if end_date else '',
        'active_range':range_name, 'export_query':urlencode(query),
    })

@login_required
@require_POST
def remark_save(request, cycle_id):
    if get_role(request.user) not in ('user','admin'):
        return HttpResponseForbidden('Unauthorized')
    cycle = get_object_or_404(OutageCycle, pk=cycle_id)
    content = request.POST.get('text','').strip()
    if not content or len(content)>2000:
        return HttpResponseForbidden('Remarks must be 1–2000 characters')
    try:
        # The unique constraint is the authoritative one-submission rule.
        with transaction.atomic():
            OutageCycleRemark.objects.create(cycle=cycle, author=request.user, text=content)
    except IntegrityError:
        return HttpResponseForbidden('Your remark for this cycle already exists. Only admins may edit.')
    return redirect('outage_cycle_remarks')

@login_required
@require_POST
def remark_admin_edit(request, remark_id):
    if get_role(request.user) != 'admin':
        return HttpResponseForbidden('Admin only')
    remark = get_object_or_404(OutageCycleRemark, pk=remark_id)
    text = request.POST.get('text','').strip()
    if not text or len(text)>2000:
        return HttpResponseForbidden('Invalid remark')
    remark.text=text
    remark.save(update_fields=['text','updated_at'])
    return redirect('outage_cycle_remarks')

@login_required
@require_POST
def remark_admin_delete(request, remark_id):
    if get_role(request.user) != 'admin':
        return HttpResponseForbidden('Admin only')
    get_object_or_404(OutageCycleRemark, pk=remark_id).delete()
    return redirect('outage_cycle_remarks')
