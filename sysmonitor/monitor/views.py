from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from .models import Device, DeviceStatus, Event, SystemStatus, UserProfile, ActivityLog, OutageCycle
from django.http import JsonResponse
from .kuma_client import get_kuma_monitors, get_monitor_log

import logging
logger = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_role(user):
    if user.is_superuser:
        return 'admin'
    try:
        return user.userprofile.role
    except:
        return 'guest'


def get_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def log_activity(user, action, detail='', ip=None):
    ActivityLog.objects.create(
        user=user,
        action=action,
        detail=detail,
        ip_address=ip,
    )


def role_required(*roles):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if get_role(request.user) not in roles:
                return render(request, 'monitor/denied.html', status=403)
            return view_func(request, *args, **kwargs)
        wrapper.__name__ = view_func.__name__
        return wrapper
    return decorator


# ─── Public — About / Documentation ────────────────────────────────────────────
# Intentionally has NO @login_required / @role_required decorator — this page
# is meant to be publicly readable (purpose, how it works, contact, app
# download) without needing an account. It must never contain login
# credentials, access URLs, tokens, or any other secret.

def about_view(request):
    from django.utils import timezone

    device_count  = Device.objects.filter(is_active=True).count()
    total_events  = Event.objects.count()
    total_cycles  = OutageCycle.objects.filter(is_complete=True).count()

    first_event = Event.objects.order_by('created_at').first()
    if first_event:
        days_monitoring = max((timezone.now() - first_event.created_at).days, 0)
    else:
        days_monitoring = 0

    return render(request, 'monitor/about.html', {
        'device_count':    device_count,
        'total_events':    total_events,
        'total_cycles':    total_cycles,
        'days_monitoring': days_monitoring,
        'role': get_role(request.user),
    })


# ─── Auth ─────────────────────────────────────────────────────────────────────

def login_view(request):
    error = None
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            log_activity(user, 'LOGIN',
                         f'User "{username}" logged in.',
                         ip=get_ip(request))
            return redirect('dashboard')
        else:
            fake_user = User.objects.filter(username=username).first()
            log_activity(fake_user, 'LOGIN_FAILED',
                         f'Failed login attempt for "{username}".',
                         ip=get_ip(request))
            error = 'Invalid username or password'
    return render(request, 'monitor/login.html', {'error': error})


def logout_view(request):
    if request.user.is_authenticated:
        log_activity(request.user, 'LOGOUT',
                     f'User "{request.user.username}" logged out.',
                     ip=get_ip(request))
    logout(request)
    return redirect('login')


# ─── Dashboard ────────────────────────────────────────────────────────────────

@login_required(login_url='login')
def dashboard(request):
    role = get_role(request.user)

    devices = []
    for device in Device.objects.filter(is_active=True):
        latest = DeviceStatus.objects.filter(device=device).first()
        devices.append({
            'obj':    device,
            'status': latest.status if latest else 'UNKNOWN',
            'ms':     latest.response_ms if latest else None,
            'time':   latest.checked_at if latest else None,
        })

    try:
        sys_status = SystemStatus.objects.get(id=1)
    except SystemStatus.DoesNotExist:
        sys_status = None

    last_cycle = OutageCycle.objects.filter(
        is_complete=True, pdb_duration_sec__gt=0
    ).first()

    active_cycle = OutageCycle.objects.filter(is_complete=False).first()
    events = Event.objects.all()[:20]

    context = {
        'devices':      devices,
        'sys_status':   sys_status,
        'events':       events,
        'role':         role,
        'user':         request.user,
        'last_cycle':   last_cycle,
        'active_cycle': active_cycle,
    }
    return render(request, 'monitor/dashboard.html', context)


# ─── API ──────────────────────────────────────────────────────────────────────

@login_required(login_url='login')
def api_status(request):
    import pytz
    bdt = pytz.timezone('Asia/Dhaka')

    devices = []
    for device in Device.objects.filter(is_active=True):
        latest = DeviceStatus.objects.filter(device=device).first()
        devices.append({
            'id':     device.id,
            'name':   device.name,
            'ip':     device.ip_address,
            'desc':   device.description,
            'status': latest.status if latest else 'UNKNOWN',
            'ms':     latest.response_ms if latest else None,
        })

    try:
        sys_status = SystemStatus.objects.get(id=1)
        overall    = sys_status.status
        note       = sys_status.note
        updated_at = sys_status.updated_at.astimezone(bdt).strftime('%I:%M:%S %p')
    except:
        overall    = 'UNKNOWN'
        note       = ''
        updated_at = '—'

    events = list(
        Event.objects.values(
            'level', 'message', 'created_at', 'device__name'
        )[:10]
    )
    for e in events:
        local_time       = e['created_at'].astimezone(bdt)
        e['created_at']  = local_time.strftime('%Y-%m-%d %I:%M:%S %p')
        e['device_name'] = e.pop('device__name') or ''

    last_cycle   = OutageCycle.objects.filter(
        is_complete=True, pdb_duration_sec__gt=0
    ).first()
    active_cycle = OutageCycle.objects.filter(is_complete=False).first()

    cycle_data = {}
    if active_cycle:
        gen_since = '—'
        if active_cycle.gen_start:
            gen_since = active_cycle.gen_start.astimezone(bdt).strftime('%I:%M:%S %p')
        cycle_data = {
            'state':     'ACTIVE',
            'gen_since': gen_since,
        }
    elif last_cycle:
        completed_at = '—'
        date_str     = '—'
        if last_cycle.cycle_end:
            completed_at = last_cycle.cycle_end.astimezone(bdt).strftime('%I:%M:%S %p')
        if last_cycle.outage_start:
            date_str = last_cycle.outage_start.astimezone(bdt).strftime('%d/%m/%Y')
        cycle_data = {
            'state':        'COMPLETE',
            'cycle_type':   last_cycle.cycle_type,
            'completed_at': completed_at,
            'date':         date_str,
            'pdb_duration': last_cycle.pdb_duration_fmt(),
            'gen_runtime':  last_cycle.gen_runtime_fmt(),
            'alarm_reason': last_cycle.alarm_reason,
        }
    else:
        cycle_data = {'state': 'NONE'}
    from monitor.models import GeneratorModeLog
    latest_gen_log = GeneratorModeLog.objects.order_by('-switched_at').first()
    generator_mode = None
    if latest_gen_log:
        generator_mode = {
            'active':      latest_gen_log.generator,
            'switched_at': latest_gen_log.switched_at.astimezone(bdt).strftime('%I:%M:%S %p'),
            'switched_date': latest_gen_log.switched_at.astimezone(bdt).strftime('%d/%m/%Y'),
        }

    return JsonResponse({
        'devices':    devices,
        'overall':    overall,
        'note':       note,
        'updated_at': updated_at,
        'events':     events,
        'cycle':      cycle_data,
        'generator_mode': generator_mode,
    })


# ─── User Management ─────────────────────────────────────────────────────────

@role_required('admin')
def user_list(request):
    users = User.objects.all().order_by('username')
    user_data = []
    for u in users:
        role = get_role(u)
        user_data.append({'obj': u, 'role': role})

    # Pending self-service profile change requests (email / mobile number)
    # are shown as a second tab on this same page so admins don't need a
    # separate top-nav item to approve them.
    from monitor.models import ProfileChangeRequest
    pending = ProfileChangeRequest.objects.filter(status='PENDING').select_related('user')

    return render(request, 'monitor/user_list.html', {
        'user_data': user_data,
        'pending':   pending,
        'role':      get_role(request.user),
        'user':      request.user,
    })


@role_required('admin')
def user_create(request):
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        role     = request.POST.get('role', 'viewer')

        if not username or not password:
            error = 'Username and password are required.'
        elif User.objects.filter(username=username).exists():
            error = f'Username "{username}" already exists.'
        else:
            u = User.objects.create_user(username=username, password=password)
            UserProfile.objects.create(user=u, role=role)
            log_activity(request.user, 'USER_CREATED',
                         f'Created user "{username}" with role "{role}".',
                         ip=get_ip(request))
            messages.success(request, f'User "{username}" created as {role}.')
            return redirect('user_list')

    return render(request, 'monitor/user_form.html', {
        'error':  error,
        'action': 'Create',
        'role':   get_role(request.user),
        'user':   request.user,
    })


@role_required('admin')
def user_edit(request, user_id):
    target = get_object_or_404(User, id=user_id)
    error  = None

    if request.method == 'POST':
        new_role = request.POST.get('role', 'viewer')
        new_pass = request.POST.get('password', '').strip()

        profile, _ = UserProfile.objects.get_or_create(user=target)
        profile.role = new_role
        profile.save()

        if new_pass:
            target.set_password(new_pass)
            target.save()

        log_activity(request.user, 'USER_EDITED',
                     f'Edited user "{target.username}" — role set to "{new_role}"'
                     + (' + password changed.' if new_pass else '.'),
                     ip=get_ip(request))
        messages.success(request, f'User "{target.username}" updated.')
        return redirect('user_list')

    try:
        current_role = target.userprofile.role
    except:
        current_role = 'viewer'

    return render(request, 'monitor/user_form.html', {
        'error':        error,
        'action':       'Edit',
        'target_user':  target,
        'current_role': current_role,
        'role':         get_role(request.user),
        'user':         request.user,
    })


@role_required('admin')
def user_delete(request, user_id):
    target = get_object_or_404(User, id=user_id)
    if target == request.user:
        messages.error(request, "You can't delete yourself.")
        return redirect('user_list')
    if request.method == 'POST':
        username = target.username
        log_activity(request.user, 'USER_DELETED',
                     f'Deleted user "{username}".',
                     ip=get_ip(request))
        target.delete()
        messages.success(request, f'User "{username}" deleted.')
        return redirect('user_list')
    return render(request, 'monitor/user_confirm_delete.html', {
        'target_user': target,
        'role':        get_role(request.user),
        'user':        request.user,
    })


# ─── Device Management ───────────────────────────────────────────────────────

@role_required('admin')
def device_list(request):
    devices = []
    for device in Device.objects.all().order_by('-added_at'):
        latest = DeviceStatus.objects.filter(device=device).first()
        devices.append({
            'obj':    device,
            'status': latest.status if latest else 'UNKNOWN',
            'ms':     latest.response_ms if latest else None,
            'time':   latest.checked_at if latest else None,
        })
    return render(request, 'monitor/device_list.html', {
        'devices': devices,
        'role':    get_role(request.user),
        'user':    request.user,
    })


@role_required('admin')
def device_create(request):
    error = None
    if request.method == 'POST':
        name        = request.POST.get('name', '').strip()
        ip_address  = request.POST.get('ip_address', '').strip()
        description = request.POST.get('description', '').strip()

        if not name or not ip_address:
            error = 'Name and IP address are required.'
        elif Device.objects.filter(ip_address=ip_address).exists():
            error = f'Device with IP {ip_address} already exists.'
        else:
            device = Device.objects.create(
                name=name,
                ip_address=ip_address,
                description=description,
                is_active=True,
            )
            Event.objects.create(
                device=device,
                level='INFO',
                message=f'Device "{name}" ({ip_address}) added by {request.user.username}.'
            )
            log_activity(request.user, 'DEVICE_ADDED',
                         f'Added device "{name}" ({ip_address}).',
                         ip=get_ip(request))
            messages.success(request, f'Device "{name}" added successfully.')
            return redirect('device_list')

    return render(request, 'monitor/device_form.html', {
        'error':  error,
        'action': 'Add',
        'role':   get_role(request.user),
        'user':   request.user,
    })


@role_required('admin')
def device_edit(request, device_id):
    device = get_object_or_404(Device, id=device_id)
    error  = None

    if request.method == 'POST':
        device.name        = request.POST.get('name', device.name).strip()
        device.description = request.POST.get('description', '').strip()
        device.is_active   = request.POST.get('is_active') == 'on'
        device.save()
        log_activity(request.user, 'DEVICE_EDITED',
                     f'Edited device "{device.name}" ({device.ip_address}).',
                     ip=get_ip(request))
        messages.success(request, f'Device "{device.name}" updated.')
        return redirect('device_list')

    return render(request, 'monitor/device_form.html', {
        'error':   error,
        'action':  'Edit',
        'device':  device,
        'role':    get_role(request.user),
        'user':    request.user,
    })


@role_required('admin')
def device_delete(request, device_id):
    device = get_object_or_404(Device, id=device_id)
    if request.method == 'POST':
        name = device.name
        ip   = device.ip_address
        log_activity(request.user, 'DEVICE_DELETED',
                     f'Deleted device "{name}" ({ip}).',
                     ip=get_ip(request))
        device.delete()
        messages.success(request, f'Device "{name}" deleted.')
        return redirect('device_list')
    return render(request, 'monitor/device_confirm_delete.html', {
        'device': device,
        'role':   get_role(request.user),
        'user':   request.user,
    })


# ─── Event Log ────────────────────────────────────────────────────────────────

@role_required('user', 'admin', 'viewer')
def event_log(request):
    role = get_role(request.user)

    filter_device = request.GET.get('device', '')
    filter_level  = request.GET.get('level', '')
    filter_date   = request.GET.get('date', '')

    events = Event.objects.all()
    if filter_device:
        events = events.filter(device__id=filter_device)
    if filter_level:
        events = events.filter(level=filter_level)
    if filter_date:
        events = events.filter(created_at__date=filter_date)

    events       = list(events[:200])
    outage_count = sum(1 for e in events if e.level == 'OUTAGE')
    devices      = Device.objects.all()

    context = {
        'events':        events,
        'devices':       devices,
        'filter_device': filter_device,
        'filter_level':  filter_level,
        'filter_date':   filter_date,
        'role':          role,
        'user':          request.user,
        'level_choices': ['INFO', 'NOTICE', 'OUTAGE', 'GEN-UP', 'ATS', 'NORMAL', 'CRITICAL'],
        'outage_count':  outage_count,
    }
    return render(request, 'monitor/event_log.html', context)


# ─── Activity Log ─────────────────────────────────────────────────────────────

@role_required('admin')
def activity_log(request):
    filter_user   = request.GET.get('user', '')
    filter_action = request.GET.get('action', '')
    filter_date   = request.GET.get('date', '')

    logs = ActivityLog.objects.all()
    if filter_user:
        logs = logs.filter(user__id=filter_user)
    if filter_action:
        logs = logs.filter(action=filter_action)
    if filter_date:
        logs = logs.filter(timestamp__date=filter_date)

    logs = logs[:300]

    return render(request, 'monitor/activity_log.html', {
        'logs':           logs,
        'all_users':      User.objects.all(),
        'filter_user':    filter_user,
        'filter_action':  filter_action,
        'filter_date':    filter_date,
        'action_choices': ActivityLog.ACTION_CHOICES,
        'role':           get_role(request.user),
        'user':           request.user,
    })


# ─── Daily Cycle Summary API ──────────────────────────────────────────────────

@login_required(login_url='login')
def api_daily_summary(request):
    import pytz
    from datetime import datetime as dt_class, timedelta
    from django.utils import timezone as dj_timezone
    from monitor.day_split import split_cycle_by_day
    bdt = pytz.timezone('Asia/Dhaka')

    date_str = request.GET.get('date', '')
    try:
        filter_date = dt_class.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        filter_date = dt_class.now(bdt).date()

    day_start_bdt = bdt.localize(dt_class(filter_date.year, filter_date.month, filter_date.day, 0, 0, 0))
    day_end_bdt   = day_start_bdt + timedelta(days=1)
    now = dj_timezone.now()

    # Candidates: anything that could overlap this day. Look back a few days
    # so a cycle that started earlier and either is still ongoing, or ended
    # late, isn't missed — same approach as the cron's build_daily_summary().
    from monitor.models import GeneratorModeLog
    from monitor.daily_summary import get_generator_for_cycle, fmt_duration
    mode_logs = list(GeneratorModeLog.objects.filter(switched_at__lt=day_end_bdt).order_by('switched_at'))

    candidates = OutageCycle.objects.filter(
        outage_start__lt=day_end_bdt,
        outage_start__gte=day_start_bdt - timedelta(days=3),
    ).order_by('outage_start')

    total_secs_sum = 0
    rows = []
    for c in candidates:
        seg = None
        for s in split_cycle_by_day(c, bdt, now=now):
            if s['date'] == filter_date:
                seg = s
                break
        if not seg or seg['duration_sec'] <= 0:
            continue

        total_secs = seg['duration_sec']
        duration_str = fmt_duration(total_secs)
        gen = get_generator_for_cycle(c, mode_logs)
        rows.append({
            'start':       seg['start'].strftime('%I:%M:%S %p'),
            'end':         'ongoing…' if seg['is_ongoing'] else seg['end'].strftime('%I:%M:%S %p'),
            'duration':    duration_str,
            'generator':   gen,
            'is_complete': c.is_complete and not seg['is_ongoing'],
            'is_ongoing':  seg['is_ongoing'],
            'cycle_type':  c.cycle_type,
        })

        # Only count minutes toward the day's total for real (non-blip) cycles,
        # matching the previous "completed, pdb_duration_sec > 0" intent —
        # but now measured per calendar day instead of per whole cycle.
        if c.pdb_duration_sec > 0 or seg['is_ongoing']:
            total_secs_sum += total_secs

    dates_with_cycles = set()
    today_bdt = dt_class.now(bdt).date()
    for i in range(14):
        dates_with_cycles.add((today_bdt - timedelta(days=i)).strftime('%Y-%m-%d'))
    for c in OutageCycle.objects.filter(pdb_duration_sec__gt=0):
        if c.outage_start:
            dates_with_cycles.add(
                c.outage_start.astimezone(bdt).strftime('%Y-%m-%d')
            )
            # Also register the END date if it landed on the next day —
            # otherwise a split outage's second half wouldn't show as a
            # date with data in the picker.
            end_dt = c.pdb_restored if c.pdb_restored else c.cycle_end
            if end_dt:
                dates_with_cycles.add(
                    end_dt.astimezone(bdt).strftime('%Y-%m-%d')
                )

    today_bdt      = dt_class.now(bdt).date()
    is_today       = (filter_date == today_bdt)
    has_incomplete = any(r['is_ongoing'] for r in rows)
    day_complete   = (not is_today) and (not has_incomplete)

    return JsonResponse({
        'date':            filter_date.strftime('%d/%m/%Y'),
        'date_val':        filter_date.strftime('%Y-%m-%d'),
        'rows':            rows,
        'total_secs':      total_secs_sum,
        'day_complete':    day_complete,
        'available_dates': sorted(dates_with_cycles, reverse=True),
    })


# ─── Report Page ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
def report_view(request):
    role = get_role(request.user)
    return render(request, 'monitor/report.html', {
        'role': role,
        'user': request.user,
    })


@login_required(login_url='login')
def api_report(request):
    import pytz
    from datetime import datetime as dt_class, timedelta
    bdt = pytz.timezone('Asia/Dhaka')

    role = get_role(request.user)

    # Parse date range
    from_str = request.GET.get('from', '')
    to_str   = request.GET.get('to', '')

    try:
        date_from = dt_class.strptime(from_str, '%Y-%m-%d').date()
    except Exception:
        date_from = None

    try:
        date_to = dt_class.strptime(to_str, '%Y-%m-%d').date()
    except Exception:
        date_to = None

    # Build date range label
    if date_from and date_to:
        date_range = (
            f'{date_from.strftime("%d/%m/%Y")} — {date_to.strftime("%d/%m/%Y")}'
        )
    elif date_from:
        date_range = f'From {date_from.strftime("%d/%m/%Y")}'
    elif date_to:
        date_range = f'Up to {date_to.strftime("%d/%m/%Y")}'
    else:
        date_range = 'All time'

    # Filter cycles
    cycles_qs = OutageCycle.objects.filter(
        is_complete=True,
        pdb_duration_sec__gt=0
    ).order_by('outage_start')

    if date_from:
        from datetime import datetime as dt2
        import pytz as pytz2
        bdt2 = pytz2.timezone('Asia/Dhaka')
        start_dt = bdt2.localize(dt2(date_from.year, date_from.month, date_from.day, 0, 0, 0))
        cycles_qs = cycles_qs.filter(outage_start__gte=start_dt)

    if date_to:
        from datetime import datetime as dt3
        import pytz as pytz3
        bdt3 = pytz3.timezone('Asia/Dhaka')
        end_dt = bdt3.localize(dt3(date_to.year, date_to.month, date_to.day, 23, 59, 59))
        cycles_qs = cycles_qs.filter(outage_start__lte=end_dt)

    cycles = list(cycles_qs)

    # Build cycles list
    cycle_rows = []
    from monitor.models import GeneratorModeLog
    from monitor.daily_summary import get_generator_for_cycle, fmt_duration
    mode_logs_all = list(GeneratorModeLog.objects.order_by('switched_at'))
    for c in cycles:
        local_start = c.outage_start.astimezone(bdt)
        # End time = pdb_restored (when Holder came back = grid restored)
        end_dt      = c.pdb_restored if c.pdb_restored else c.cycle_end
        local_end   = end_dt.astimezone(bdt) if end_dt else None
        # Duration = pdb_duration_sec (Holder DOWN → Holder UP only)
        total_secs  = c.pdb_duration_sec if c.pdb_duration_sec else (
            int((end_dt - c.outage_start).total_seconds()) if end_dt else 0
        )
        dur_str = fmt_duration(total_secs)

        gen = get_generator_for_cycle(c, mode_logs_all)
        cycle_rows.append({
            'date':         local_start.strftime('%Y-%m-%d'),
            'start':        local_start.strftime('%I:%M:%S %p'),
            'end':          local_end.strftime('%I:%M:%S %p') if local_end else '—',
            'duration':     dur_str,
            'generator':    gen,
            'pdb_duration': c.pdb_duration_fmt(),
            'gen_runtime':  c.gen_runtime_fmt(),
            'cycle_type':   c.cycle_type,
        })

    # Daily totals for bar chart — clipped at midnight so a cycle that
    # crosses into the next day only contributes the minutes that actually
    # happened on each day (not the whole duration dumped on the start day).
    from monitor.day_split import split_cycle_by_day
    daily_map = {}
    for c in cycles:
        for seg in split_cycle_by_day(c, bdt):
            d_key = seg['date'].strftime('%Y-%m-%d')
            mins = round(seg['duration_sec'] / 60)
            if mins <= 0:
                continue
            daily_map[d_key] = daily_map.get(d_key, 0) + mins

    # Fill in all dates in range for chart continuity
    daily_list = []
    if date_from and date_to:
        cur = date_from
        while cur <= date_to:
            key = cur.strftime('%Y-%m-%d')
            daily_list.append({'date': key, 'total_mins': daily_map.get(key, 0)})
            cur += timedelta(days=1)
    else:
        for key in sorted(daily_map.keys()):
            daily_list.append({'date': key, 'total_mins': daily_map[key]})

    # Summary stats
    total_mins = sum(d['total_mins'] for d in daily_list)
    days_with  = len([d for d in daily_list if d['total_mins'] > 0])
    avg_per_day = round(total_mins / days_with, 1) if days_with else 0

    all_cycle_mins = [
        round(c.pdb_duration_sec / 60)
        for c in cycles if c.pdb_duration_sec
    ]
    max_mins  = max(all_cycle_mins) if all_cycle_mins else 0
    min_mins  = min(all_cycle_mins) if all_cycle_mins else 0

    # Find dates of max/min
    max_date = min_date = None
    for c in cycles:
        if not c.pdb_duration_sec:
            continue
        mins = round(c.pdb_duration_sec / 60)
        if mins == max_mins:
            max_date = c.outage_start.astimezone(bdt).strftime('%Y-%m-%d')
        if mins == min_mins and min_date is None:
            min_date = c.outage_start.astimezone(bdt).strftime('%Y-%m-%d')

    # Monthly breakdown
    monthly_map = {}
    monthly_order = []
    for c in cycles:
        if not c.cycle_end:
            continue
        local_start = c.outage_start.astimezone(bdt)
        m_key  = local_start.strftime('%Y-%m')
        m_label = local_start.strftime('%B %Y')
        mins   = round(c.pdb_duration_sec / 60) if c.pdb_duration_sec else 0
        if m_key not in monthly_map:
            monthly_map[m_key] = {
                'month': m_label, 'count': 0, 'total_mins': 0,
                'max_mins': 0, 'days': set()
            }
            monthly_order.append(m_key)
        monthly_map[m_key]['count']      += 1
        monthly_map[m_key]['total_mins'] += mins
        monthly_map[m_key]['days'].add(local_start.strftime('%Y-%m-%d'))
        if mins > monthly_map[m_key]['max_mins']:
            monthly_map[m_key]['max_mins'] = mins

    monthly_list = []
    for k in monthly_order:
        m = monthly_map[k]
        days_in_month = len(m['days'])
        monthly_list.append({
            'month':       m['month'],
            'count':       m['count'],
            'total_mins':  m['total_mins'],
            'avg_per_day': round(m['total_mins'] / days_in_month, 1) if days_in_month else 0,
            'max_mins':    m['max_mins'],
        })

    return JsonResponse({
        'date_range': date_range,
        'cycles':     cycle_rows,
        'daily':      daily_list,
        'monthly':    monthly_list,
        'summary': {
            'count':             len(cycles),
            'total_mins':        total_mins,
            'avg_per_day':       avg_per_day,
            'max_mins':          max_mins,
            'max_date':          max_date,
            'min_mins':          min_mins,
            'min_date':          min_date,
            'days_with_outages': days_with,
        },
    })


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION VIEWS
# ══════════════════════════════════════════════════════════════════════════════

from monitor.models import NotificationGateway, NotificationRecipient, NotificationLog
from monitor.notifications import send_test, get_telegram_chat_id, dispatch as notify_dispatch

@role_required('admin')
def notifications_page(request):
    """Main notification settings page."""
    channels = ['whatsapp', 'telegram', 'email']
    gateways = {}
    for ch in channels:
        gw, _ = NotificationGateway.objects.get_or_create(channel=ch)
        gateways[ch] = gw

    recipients  = NotificationRecipient.objects.all()
    recent_logs = NotificationLog.objects.all()[:20]
    return render(request, 'monitor/notifications.html', {
        'gateways':    gateways,
        'recipients':  recipients,
        'recent_logs': recent_logs,
        'role': 'admin',
    })


@role_required('admin')
def notif_gateway_save(request):
    """Save gateway credentials via AJAX POST."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    import json as _json
    data    = _json.loads(request.body)
    channel = data.get('channel')
    if channel not in ('whatsapp', 'telegram', 'email'):
        return JsonResponse({'ok': False, 'error': 'Invalid channel'})

    gw, _ = NotificationGateway.objects.get_or_create(channel=channel)
    gw.is_enabled = data.get('is_enabled', False)

    if channel == 'whatsapp':
        gw.wa_phone_number_id = data.get('wa_phone_number_id', '').strip()
        gw.wa_access_token    = data.get('wa_access_token',    '').strip()
        gw.wa_from_number     = data.get('wa_from_number',     '').strip()
    elif channel == 'telegram':
        gw.tg_bot_token = data.get('tg_bot_token', '').strip()
    elif channel == 'email':
        gw.email_host     = data.get('email_host',     'smtp.gmail.com').strip()
        gw.email_port     = int(data.get('email_port', 587))
        gw.email_username = data.get('email_username', '').strip()
        gw.email_password = data.get('email_password', '').strip()
        gw.email_from     = data.get('email_from',     '').strip()

    gw.save()
    return JsonResponse({'ok': True})


@role_required('admin')
def notif_gateway_test(request):
    """Test a gateway by sending to a specific address."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    import json as _json
    data    = _json.loads(request.body)
    channel = data.get('channel')
    contact = data.get('contact', '').strip()
    if not contact:
        return JsonResponse({'ok': False, 'error': 'No contact provided'})
    try:
        gw = NotificationGateway.objects.get(channel=channel)
    except NotificationGateway.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Gateway not configured'})

    ok, err = send_test(channel, contact, gw)
    return JsonResponse({'ok': ok, 'error': err})


@role_required('admin')
def notif_telegram_chats(request):
    """Fetch recent Telegram chat IDs from bot updates."""
    try:
        gw = NotificationGateway.objects.get(channel='telegram')
    except NotificationGateway.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Telegram not configured'})
    ok, result = get_telegram_chat_id(gw.tg_bot_token)
    if ok:
        return JsonResponse({'ok': True, 'chats': result})
    return JsonResponse({'ok': False, 'error': result})


@role_required('admin')
def notif_recipient_add(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    import json as _json
    d = _json.loads(request.body)

    name    = d.get('name', '').strip()
    channel = d.get('channel', '')
    contact = d.get('contact', '').strip()

    if not name or not contact:
        return JsonResponse({'ok': False, 'error': 'Name and contact are required'})

    # Prevent duplicates: same channel + contact already exists
    existing = NotificationRecipient.objects.filter(channel=channel, contact=contact).first()
    if existing:
        return JsonResponse({
            'ok': False,
            'error': f'"{existing.name}" already has this {channel} contact saved. '
                     f'Edit the existing entry instead of adding a duplicate.'
        })

    r = NotificationRecipient.objects.create(
        name           = name,
        channel        = channel,
        contact        = contact,
        is_active      = True,
        alert_outage   = d.get('alert_outage',   True),
        alert_critical = d.get('alert_critical', True),
        alert_alarm    = d.get('alert_alarm',    True),
        alert_complete = d.get('alert_complete', True),
        daily_summary  = d.get('daily_summary',  False),
    )
    return JsonResponse({'ok': True, 'id': r.id})


@role_required('admin')
def notif_recipient_edit(request, rid):
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    import json as _json
    d = _json.loads(request.body)
    try:
        r = NotificationRecipient.objects.get(id=rid)
        r.name           = d.get('name', r.name).strip()
        r.channel        = d.get('channel', r.channel)
        r.contact        = d.get('contact', r.contact).strip()
        r.alert_outage   = d.get('alert_outage',   r.alert_outage)
        r.alert_critical = d.get('alert_critical', r.alert_critical)
        r.alert_alarm    = d.get('alert_alarm',    r.alert_alarm)
        r.alert_complete = d.get('alert_complete', r.alert_complete)
        r.daily_summary  = d.get('daily_summary',  r.daily_summary)
        r.save()
        return JsonResponse({'ok': True})
    except NotificationRecipient.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Not found'})


@role_required('admin')
def notif_recipient_delete(request, rid):
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    NotificationRecipient.objects.filter(id=rid).delete()
    return JsonResponse({'ok': True})


@role_required('admin')
def notif_recipient_test(request, rid):
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    try:
        r  = NotificationRecipient.objects.get(id=rid)
        gw = NotificationGateway.objects.get(channel=r.channel, is_enabled=True)
        ok, err = send_test(r.channel, r.contact, gw)
        return JsonResponse({'ok': ok, 'error': err})
    except NotificationGateway.DoesNotExist:
        return JsonResponse({'ok': False, 'error': f'{r.channel} gateway is not enabled'})
    except NotificationRecipient.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Recipient not found'})


@role_required('admin')
def notif_recipient_toggle(request, rid):
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    try:
        r = NotificationRecipient.objects.get(id=rid)
        r.is_active = not r.is_active
        r.save()
        return JsonResponse({'ok': True, 'is_active': r.is_active})
    except NotificationRecipient.DoesNotExist:
        return JsonResponse({'ok': False})


@role_required('admin')
def notif_log(request):
    import pytz
    bdt = pytz.timezone('Asia/Dhaka')
    logs = NotificationLog.objects.all()[:100]
    data = [{'sent_at': l.sent_at.astimezone(bdt).strftime('%d/%m %I:%M:%S %p'),
             'event_type': l.event_type, 'channel': l.channel,
             'recipient': l.recipient, 'status': l.status, 'error': l.error}
            for l in logs]
    return JsonResponse({'logs': data})


@role_required('admin')
def notif_whatsapp_health(request):
    """
    Returns WhatsApp token health status for the dashboard warning banner.
    Cached for 30 minutes to avoid hammering Meta's API on every page load.
    """
    from django.core.cache import cache
    from monitor.notifications import check_whatsapp_token_health

    cached = cache.get('wa_token_health')
    if cached:
        return JsonResponse(cached)

    try:
        gw = NotificationGateway.objects.get(channel='whatsapp')
        if not gw.is_enabled:
            result = {'ok': True, 'status': 'DISABLED', 'message': '', 'expires_at': None}
        else:
            result = check_whatsapp_token_health(gw)
    except NotificationGateway.DoesNotExist:
        result = {'ok': True, 'status': 'DISABLED', 'message': '', 'expires_at': None}

    # expires_at is a datetime, not JSON serializable — convert to string
    if result.get('expires_at'):
        result['expires_at'] = result['expires_at'].strftime('%d %b %Y %I:%M:%S %p')

    cache.set('wa_token_health', result, 60 * 60 * 6)  # cache 6 hours
    return JsonResponse(result)


# ══════════════════════════════════════════════════════════════════════════════
# GENERATOR MODE LOG (for daily summary generator assignment)
# ══════════════════════════════════════════════════════════════════════════════

from monitor.models import GeneratorModeLog

@role_required('user', 'admin')
def generator_log_page(request):
    """Page to view and submit Generator Mode Log entries."""
    import pytz
    bdt = pytz.timezone('Asia/Dhaka')

    entries = GeneratorModeLog.objects.all()[:50]
    entries_display = [{
        'id': e.id,
        'generator': e.generator,
        'switched_at': e.switched_at.astimezone(bdt).strftime('%d/%m/%Y %I:%M:%S %p'),
        'switched_date_raw': e.switched_at.astimezone(bdt).strftime('%Y-%m-%d'),
        'switched_time_raw': e.switched_at.astimezone(bdt).strftime('%H:%M'),
        'note': e.note,
        'added_by': e.added_by,
    } for e in entries]

    return render(request, 'monitor/generator_log.html', {
        'entries': entries_display,
        'role':    get_role(request.user),
        'user':    request.user,
    })


@login_required
def generator_log_add(request):
    """AJAX POST to add a new Generator Mode Log entry. Any logged-in user can add."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    import json as _json
    import pytz
    from datetime import datetime as dt

    d = _json.loads(request.body)
    generator = d.get('generator', '').strip()
    date_str  = d.get('date', '').strip()   # expected 'YYYY-MM-DD'
    time_str  = d.get('time', '').strip()   # expected 'HH:MM'
    note      = d.get('note', '').strip()

    if generator not in ('Gen-01', 'Gen-02'):
        return JsonResponse({'ok': False, 'error': 'Invalid generator selection'})
    if not date_str or not time_str:
        return JsonResponse({'ok': False, 'error': 'Date and time are required'})

    try:
        bdt = pytz.timezone('Asia/Dhaka')
        naive_dt = dt.strptime(f'{date_str} {time_str}', '%Y-%m-%d %H:%M')
        switched_at = bdt.localize(naive_dt)
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Invalid date/time format'})

    # Prevent duplicates: same generator switched at the exact same time
    existing = GeneratorModeLog.objects.filter(
        generator=generator, switched_at=switched_at
    ).first()
    if existing:
        return JsonResponse({
            'ok': False,
            'error': f'An entry for {generator} at this exact date/time already '
                     f'exists (logged by {existing.added_by or "unknown"}). '
                     f'Contact an admin if it needs correction.'
        })

    GeneratorModeLog.objects.create(
        generator=generator,
        switched_at=switched_at,
        note=note,
        added_by=request.user.username,
    )
    return JsonResponse({'ok': True})


@role_required('admin')
def generator_log_edit(request, eid):
    """AJAX POST to edit an existing entry. Admin only."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    import json as _json
    import pytz
    from datetime import datetime as dt

    d = _json.loads(request.body)
    try:
        e = GeneratorModeLog.objects.get(id=eid)
    except GeneratorModeLog.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Entry not found'})

    generator = d.get('generator', e.generator).strip()
    date_str  = d.get('date', '').strip()
    time_str  = d.get('time', '').strip()
    note      = d.get('note', e.note).strip()

    if generator not in ('Gen-01', 'Gen-02'):
        return JsonResponse({'ok': False, 'error': 'Invalid generator selection'})

    if date_str and time_str:
        try:
            bdt = pytz.timezone('Asia/Dhaka')
            naive_dt = dt.strptime(f'{date_str} {time_str}', '%Y-%m-%d %H:%M')
            e.switched_at = bdt.localize(naive_dt)
        except ValueError:
            return JsonResponse({'ok': False, 'error': 'Invalid date/time format'})

    e.generator = generator
    e.note = note
    e.save()
    return JsonResponse({'ok': True})


@role_required('admin')
def generator_log_delete(request, eid):
    """Delete an entry. Admin only."""
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    GeneratorModeLog.objects.filter(id=eid).delete()
    return JsonResponse({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# MAINTENANCE MODE
# ══════════════════════════════════════════════════════════════════════════════

from monitor.models import MaintenanceMode

@login_required
def maintenance_status(request):
    """Returns current maintenance mode status for dashboard polling."""
    import pytz
    from datetime import datetime
    bdt = pytz.timezone('Asia/Dhaka')

    m = MaintenanceMode.objects.filter(id=1).first()
    if not m or not m.is_active:
        return JsonResponse({'active': False})

    # Check expiry here too (in addition to ping_monitor's own check) so
    # the dashboard reflects reality even if ping_monitor hasn't ticked yet
    now = datetime.now(pytz.utc)
    if m.expires_at and now >= m.expires_at:
        return JsonResponse({'active': False})

    return JsonResponse({
        'active': True,
        'started_at': m.started_at.astimezone(bdt).strftime('%I:%M:%S %p') if m.started_at else None,
        'expires_at': m.expires_at.astimezone(bdt).strftime('%I:%M:%S %p') if m.expires_at else None,
        'started_by': m.started_by,
        'reason': m.reason,
    })


@role_required('admin')
def maintenance_start(request):
    """Starts Maintenance Mode for a set duration. Admin only."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    import json as _json
    from datetime import datetime, timedelta
    import pytz

    d = _json.loads(request.body)
    minutes = int(d.get('minutes', 10))
    minutes = max(1, min(minutes, 120))  # clamp between 1 and 120 minutes
    reason = d.get('reason', '').strip()

    now = datetime.now(pytz.utc)
    expires = now + timedelta(minutes=minutes)

    m, _ = MaintenanceMode.objects.get_or_create(id=1)
    m.is_active = True
    m.started_at = now
    m.expires_at = expires
    m.started_by = request.user.username
    m.reason = reason
    m.save()

    return JsonResponse({'ok': True, 'expires_in_minutes': minutes})


@role_required('admin')
def maintenance_stop(request):
    """Manually ends Maintenance Mode early. Admin only."""
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    m = MaintenanceMode.objects.filter(id=1).first()
    if m:
        m.is_active = False
        m.save()
    return JsonResponse({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# USER PROFILE — view, edit, password change, admin approval workflow
# ══════════════════════════════════════════════════════════════════════════════

from monitor.models import ProfileChangeRequest
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm


def _get_or_create_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'role': 'viewer'})
    return profile


@login_required
def profile_view(request):
    """Read-only profile view page."""
    profile = _get_or_create_profile(request.user)
    pending = ProfileChangeRequest.objects.filter(user=request.user, status='PENDING')
    return render(request, 'monitor/profile_view.html', {
        'role': get_role(request.user),
        'profile': profile,
        'pending_requests': pending,
    })


@login_required
def profile_edit(request):
    """Profile edit form page."""
    profile = _get_or_create_profile(request.user)
    pending = ProfileChangeRequest.objects.filter(user=request.user, status='PENDING')
    return render(request, 'monitor/profile_edit.html', {
        'role': get_role(request.user),
        'profile': profile,
        'pending_requests': pending,
    })


@login_required
def profile_edit_save(request):
    """
    Saves profile edits. Name, designation, WhatsApp, Telegram, and picture
    apply immediately. Email and mobile number go through admin approval —
    a ProfileChangeRequest is created instead of changing the value directly.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})

    user = request.user
    profile = _get_or_create_profile(user)

    # Immediate fields
    first_name  = request.POST.get('first_name', '').strip()
    last_name   = request.POST.get('last_name', '').strip()
    designation = request.POST.get('designation', '').strip()
    whatsapp    = request.POST.get('whatsapp_number', '').strip()
    telegram    = request.POST.get('telegram_handle', '').strip()

    if first_name:
        user.first_name = first_name
    user.last_name = last_name
    user.save()

    profile.designation = designation
    profile.whatsapp_number = whatsapp
    profile.telegram_handle = telegram

    if 'profile_picture' in request.FILES:
        profile.profile_picture = request.FILES['profile_picture']

    profile.save()

    # Approval-required fields
    new_email  = request.POST.get('email', '').strip()
    new_mobile = request.POST.get('mobile_number', '').strip()

    messages_out = []

    if new_email and new_email != user.email:
        ProfileChangeRequest.objects.create(
            user=user, field='email',
            old_value=user.email, new_value=new_email,
        )
        messages_out.append('Email change submitted for admin approval.')

    if new_mobile and new_mobile != profile.mobile_number:
        ProfileChangeRequest.objects.create(
            user=user, field='mobile',
            old_value=profile.mobile_number, new_value=new_mobile,
        )
        messages_out.append('Mobile number change submitted for admin approval.')

    log_activity(user, 'PROFILE_UPDATE', 'Profile fields updated', get_ip(request))

    return JsonResponse({'ok': True, 'messages': messages_out})


@login_required
def profile_password(request):
    """Password change form page."""
    return render(request, 'monitor/profile_password.html', {
        'role': get_role(request.user),
    })


@login_required
def profile_password_save(request):
    """Handles password change via Django's built-in PasswordChangeForm."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})

    form = PasswordChangeForm(user=request.user, data={
        'old_password': request.POST.get('old_password', ''),
        'new_password1': request.POST.get('new_password1', ''),
        'new_password2': request.POST.get('new_password2', ''),
    })

    if form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)  # keep user logged in after password change
        log_activity(request.user, 'PASSWORD_CHANGE', 'Password changed', get_ip(request))
        return JsonResponse({'ok': True})
    else:
        errors = []
        for field, errs in form.errors.items():
            errors.extend(errs)
        return JsonResponse({'ok': False, 'error': ' '.join(errors)})


@role_required('admin')
def profile_pending_changes(request):
    """Admin page listing all pending profile change requests."""
    pending = ProfileChangeRequest.objects.filter(status='PENDING').select_related('user')
    return render(request, 'monitor/profile_pending.html', {
        'role': 'admin',
        'pending': pending,
    })


@role_required('admin')
def profile_change_approve(request, pid):
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    try:
        req = ProfileChangeRequest.objects.get(id=pid, status='PENDING')
    except ProfileChangeRequest.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Request not found or already reviewed'})

    user = req.user
    if req.field == 'email':
        user.email = req.new_value
        user.save()
    elif req.field == 'mobile':
        profile = _get_or_create_profile(user)
        profile.mobile_number = req.new_value
        profile.save()

    req.status = 'APPROVED'
    from django.utils import timezone
    req.reviewed_at = timezone.now()
    req.reviewed_by = request.user.username
    req.save()

    return JsonResponse({'ok': True})


@role_required('admin')
def profile_change_reject(request, pid):
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    from django.utils import timezone
    try:
        req = ProfileChangeRequest.objects.get(id=pid, status='PENDING')
    except ProfileChangeRequest.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Request not found or already reviewed'})

    req.status = 'REJECTED'
    req.reviewed_at = timezone.now()
    req.reviewed_by = request.user.username
    req.save()

    return JsonResponse({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM ADMIN TOOL PAGE
# ══════════════════════════════════════════════════════════════════════════════

@role_required('admin')
def system_tools(request):
    """System admin tool page — live state, journal, cycle cleanup."""
    return render(request, 'monitor/system_tools.html', {
        'role': 'admin',
        'user': request.user,
    })


@role_required('admin')
def system_live_state(request):
    """Returns live system state as JSON for the admin tool page."""
    import pytz
    bdt = pytz.timezone('Asia/Dhaka')

    # Current system status
    try:
        sys_status = SystemStatus.objects.filter(id=1).first()
    except Exception:
        sys_status = None

    # Incomplete (ongoing) cycles
    ongoing = list(OutageCycle.objects.filter(is_complete=False).order_by('-outage_start')[:5])
    ongoing_data = []
    for c in ongoing:
        from datetime import datetime
        now_bdt = datetime.now(pytz.utc)
        elapsed = int((now_bdt - c.outage_start).total_seconds()) if c.outage_start else 0
        ongoing_data.append({
            'id': c.id,
            'start': c.outage_start.astimezone(bdt).strftime('%d/%m/%Y %I:%M:%S %p') if c.outage_start else '—',
            'elapsed_min': elapsed // 60,
            'elapsed_sec': elapsed % 60,
            'type': c.cycle_type,
            'gen_start': c.gen_start.astimezone(bdt).strftime('%I:%M:%S %p') if c.gen_start else None,
            'pdb_restored': c.pdb_restored.astimezone(bdt).strftime('%I:%M:%S %p') if c.pdb_restored else None,
        })

    # Device states
    from monitor.models import Device, DeviceStatus
    devices = []
    for d in Device.objects.filter(is_active=True):
        latest = DeviceStatus.objects.filter(device=d).order_by('-checked_at').first()
        devices.append({
            'name': d.name,
            'ip': d.ip_address,
            'status': latest.status if latest else 'UNKNOWN',
            'checked': latest.checked_at.astimezone(bdt).strftime('%I:%M:%S %p') if latest else '—',
        })

    return JsonResponse({
        'system_status': sys_status.status if sys_status else 'UNKNOWN',
        'system_note': sys_status.note if sys_status else '',
        'ongoing_cycles': ongoing_data,
        'devices': devices,
    })


@role_required('admin')
def system_journal(request):
    """Returns last N lines from sysmonitor-ping journal as JSON."""
    import subprocess
    lines = int(request.GET.get('lines', 50))
    lines = max(10, min(lines, 200))
    try:
        result = subprocess.run(
            ['sudo', 'journalctl', '-u', 'sysmonitor-ping',
             '-n', str(lines), '--no-pager', '--output=short'],
            capture_output=True, text=True, timeout=10
        )
        return JsonResponse({'ok': True, 'log': result.stdout})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})


@role_required('admin')
def system_cycle_action(request, cid):
    """
    Admin action on an ongoing/fake cycle.
    action: 'close' (mark complete as NORMAL), 'delete' (remove entirely)
    Both restart the ping service so STATE is re-initialized cleanly.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    import json as _json
    import subprocess

    d = _json.loads(request.body)
    action = d.get('action', '')

    try:
        cycle = OutageCycle.objects.get(id=cid)
    except OutageCycle.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Cycle not found'})

    if action == 'close':
        from django.utils import timezone
        import pytz
        bdt = pytz.timezone('Asia/Dhaka')
        now = timezone.now()
        cycle.cycle_end       = now
        cycle.is_complete     = True
        cycle.cycle_type      = 'NORMAL'
        if cycle.outage_start:
            cycle.pdb_duration_sec = int((now - cycle.outage_start).total_seconds())
        cycle.save()
        log_activity(request.user, 'CYCLE_CLOSE',
            f'Admin force-closed cycle ID:{cid}', get_ip(request))
    elif action == 'delete':
        cycle.delete()
        log_activity(request.user, 'CYCLE_DELETE',
            f'Admin deleted fake cycle ID:{cid}', get_ip(request))
    else:
        return JsonResponse({'ok': False, 'error': 'Unknown action'})

    return JsonResponse({'ok': True})


@role_required('admin')
def system_manual_cycle_add(request):
    """
    Admin-only: manually log an audited outage cycle that the automatic
    ping monitor missed (or needs correcting), with an explicit start time,
    end time, and which generator was in use.

    Expects JSON body:
        {
            "start_date": "YYYY-MM-DD", "start_time": "HH:MM",
            "end_date":   "YYYY-MM-DD", "end_time":   "HH:MM",
            "generator":  "Gen-01" | "Gen-02" | "",   (optional)
            "note":       "free text reason"           (optional)
        }
    All times are interpreted in Asia/Dhaka (BDT), matching the rest of
    the app.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})

    import json as _json
    import pytz
    from datetime import datetime as dt_class

    bdt = pytz.timezone('Asia/Dhaka')
    try:
        d = _json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid request body'})

    start_date = (d.get('start_date') or '').strip()
    start_time = (d.get('start_time') or '').strip()
    end_date   = (d.get('end_date') or '').strip()
    end_time   = (d.get('end_time') or '').strip()
    generator  = (d.get('generator') or '').strip()
    note       = (d.get('note') or '').strip()

    if not (start_date and start_time and end_date and end_time):
        return JsonResponse({'ok': False, 'error': 'Start and end date/time are required'})

    if generator not in ('', 'Gen-01', 'Gen-02'):
        return JsonResponse({'ok': False, 'error': 'Generator must be Gen-01, Gen-02, or left unassigned'})

    try:
        naive_start = dt_class.strptime(f'{start_date} {start_time}', '%Y-%m-%d %H:%M')
        naive_end   = dt_class.strptime(f'{end_date} {end_time}', '%Y-%m-%d %H:%M')
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Invalid date/time format'})

    start_dt = bdt.localize(naive_start)
    end_dt   = bdt.localize(naive_end)

    if end_dt <= start_dt:
        return JsonResponse({'ok': False, 'error': 'End time must be after start time'})

    duration_sec = int((end_dt - start_dt).total_seconds())

    cycle = OutageCycle.objects.create(
        outage_start=start_dt,
        pdb_restored=end_dt,
        cycle_end=end_dt,
        pdb_duration_sec=duration_sec,
        gen_runtime_sec=0,
        cycle_type='MANUAL',
        is_complete=True,
        is_manual=True,
        manual_generator=generator,
        alarm_reason=note,
        added_by=request.user.username,
    )

    log_activity(
        request.user, 'CYCLE_MANUAL_ADD',
        f'Manually added audited cycle ID:{cycle.id} '
        f'({start_dt.strftime("%d/%m/%Y %I:%M:%S %p")} → {end_dt.strftime("%d/%m/%Y %I:%M:%S %p")}, '
        f'{generator or "unassigned"})',
        get_ip(request)
    )

    try:
        notify_dispatch('COMPLETE', cycle=cycle)
    except Exception as e:
        logger.error(f"Manual cycle notify failed for cycle ID:{cycle.id}: {e}")

    from monitor.daily_summary import fmt_duration
    return JsonResponse({
        'ok': True,
        'cycle_id': cycle.id,
        'duration_str': fmt_duration(duration_sec),
    })


@role_required('admin')
def system_recent_cycles(request):
    """Returns recent cycles for the system admin panel."""
    import pytz
    bdt = pytz.timezone('Asia/Dhaka')
    limit = min(int(request.GET.get('limit', 10)), 100)
    cycles = OutageCycle.objects.all().order_by('-outage_start')[:limit]
    data = []
    for c in cycles:
        end_dt = c.pdb_restored or c.cycle_end
        pdb = c.pdb_duration_sec or 0
        h, r = divmod(pdb, 3600)
        m, s = divmod(r, 60)
        if h:    dur = f"{h}h {m:02d}m"
        elif m:  dur = f"{m}min {s}s"
        else:    dur = f"{s}s"
        data.append({
            'id':       c.id,
            'start':    c.outage_start.astimezone(bdt).strftime('%d/%m %I:%M:%S %p') if c.outage_start else '—',
            'end':      end_dt.astimezone(bdt).strftime('%d/%m %I:%M:%S %p') if end_dt else '—',
            'duration': dur,
            'type':     c.cycle_type,
            'complete': c.is_complete,
        })
    return JsonResponse({'cycles': data})


@role_required('admin')
def system_restart_ping(request):
    """Restart the sysmonitor-ping service.
    Restricted to Django superusers only (not just role=admin).
    Requires the superuser's own password to confirm.
    """
    # Extra guard: only Django superusers can restart the service
    # This means only the 'admin' account — not other role=admin users
    if not request.user.is_superuser:
        return JsonResponse({'ok': False,
            'error': 'Only the system superuser (admin) can restart the ping service.'})
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    import json as _json
    import subprocess
    from django.contrib.auth import authenticate

    try:
        d = _json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid request'})

    password = d.get('password', '').strip()
    if not password:
        return JsonResponse({'ok': False, 'error': 'Password is required to confirm this action.'})

    # Triple-check: verify password hash directly against current user object
    # AND re-fetch from DB to ensure no stale session data
    from django.contrib.auth.models import User as AuthUser
    from django.contrib.auth.hashers import check_password as check_pw
    try:
        fresh_user = AuthUser.objects.get(pk=request.user.pk)
    except AuthUser.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'User not found.'})
    if not check_pw(password, fresh_user.password):
        log_activity(request.user, 'SERVICE_RESTART_DENIED',
            f'Failed password confirmation for ping restart (user: {request.user.username})',
            get_ip(request))
        return JsonResponse({'ok': False, 'error': f'Incorrect password for user "{request.user.username}". Action denied.'})

    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'restart', 'sysmonitor-ping'],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            log_activity(request.user, 'SERVICE_RESTART',
                'Admin restarted sysmonitor-ping service (password confirmed)', get_ip(request))
            return JsonResponse({'ok': True})
        else:
            return JsonResponse({'ok': False, 'error': result.stderr})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})


#------- Uptime KUMA
def uptime_status(request):
    monitors = get_kuma_monitors()
    return render(request, "monitor/uptime_status.html", {
        "monitors": monitors,
        "role": get_role(request.user),
    })

def uptime_status_log(request, monitor_id):
    logs = get_monitor_log(monitor_id)
    return JsonResponse({"logs": logs})
