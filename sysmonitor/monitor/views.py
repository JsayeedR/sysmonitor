from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from .models import Device, DeviceStatus, Event, SystemStatus, UserProfile, ActivityLog, OutageCycle, SensorAlarmConfig
from django.http import JsonResponse
from .kuma_client import get_kuma_monitors, get_monitor_log
from .pac_client import get_all_pac_status

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
    if settings.IS_MIRROR:
        return    # remote: the master writes the log (login/logout are reported to it)
    ActivityLog.objects.create(
        user=user,
        action=action,
        detail=detail,
        ip_address=ip,
    )


def mirror_activity(request):
    """Remote server tells us about a login/logout there (signed, tunnel-only)."""
    if not getattr(request, '_mirror_forwarded', False) or request.method != 'POST':
        return JsonResponse({'ok': False}, status=403)
    import json as _json
    try:
        d = _json.loads(request.body)
    except ValueError:
        return JsonResponse({'ok': False}, status=400)
    if d.get('action') not in ('LOGIN', 'LOGOUT', 'LOGIN_FAILED'):
        return JsonResponse({'ok': False}, status=400)
    user = User.objects.filter(username=d.get('username', '')).first()
    log_activity(user, d['action'], (d.get('detail') or '')[:280] + ' (via remote site)',
                 ip=d.get('ip') or None)
    return JsonResponse({'ok': True})


def mirror_track(request):
    """Remote site reports one page view (signed, tunnel-only). Counts it in the
    master's page counter; the user's usage time is updated by the normal
    UsageTrackingMiddleware because the request carries that user."""
    if not getattr(request, '_mirror_forwarded', False) or request.method != 'POST':
        return JsonResponse({'ok': False}, status=403)
    from django.db.models import F
    from .models import PageViewCounter
    PageViewCounter.objects.get_or_create(id=1, defaults={'count': 789})
    PageViewCounter.objects.filter(id=1).update(count=F('count') + 1)
    return JsonResponse({'ok': True})


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
            # Temporary password issued by the "Forgot password" flow is only
            # valid for 30 minutes — if that window has passed, refuse the
            # login even though the password itself still matches, and make
            # them request a fresh reset.
            if settings.IS_MIRROR:
                # remote: read-only — never create a profile or change a password here
                profile = UserProfile.objects.filter(user=user).first()
            else:
                profile, _ = UserProfile.objects.get_or_create(user=user)
            if profile and profile.must_change_password and profile.temp_password_expires_at:
                from django.utils import timezone
                if timezone.now() > profile.temp_password_expires_at:
                    if not settings.IS_MIRROR:      # the master invalidates it when it is used there
                        user.set_unusable_password()
                        user.save()
                    log_activity(user, 'LOGIN_FAILED',
                                 f'Temporary password for "{username}" had expired.',
                                 ip=get_ip(request))
                    error = ('That temporary password has expired (30-minute limit). '
                              'Request a new password reset.')
                    return render(request, 'monitor/login.html', {'error': error})

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
            if fake_user and not fake_user.is_active:
                error = 'This account has been disabled. Contact your administrator.'
            else:
                error = 'Invalid username or password'
    return render(request, 'monitor/login.html', {'error': error})


# ─── Password Reset (forgot password) ──────────────────────────────────────────

def password_reset_request(request):
    """
    Public "Forgot password" page. Looks up the account by username OR
    email, issues a random temporary password valid for 30 minutes, forces
    a password change on next login, and emails the temp password + a
    login link via the configured email gateway.

    Always shows the same generic confirmation message whether or not a
    matching account was found, so this can't be used to enumerate usernames.
    """
    sent = False
    error = None
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        if not identifier:
            error = 'Enter your username or email.'
        else:
            from django.db.models import Q
            target = User.objects.filter(
                Q(username__iexact=identifier) | Q(email__iexact=identifier)
            ).first()

            if target and target.is_active and target.email:
                import secrets, string
                from django.utils import timezone
                from datetime import timedelta
                from monitor.models import NotificationGateway
                from monitor.notifications import send_password_reset_email

                alphabet = string.ascii_letters + string.digits
                temp_password = ''.join(secrets.choice(alphabet) for _ in range(12))
                target.set_password(temp_password)
                target.save()

                profile, _ = UserProfile.objects.get_or_create(user=target)
                profile.must_change_password = True
                profile.temp_password_expires_at = timezone.now() + timedelta(minutes=30)
                profile.save()

                gw = NotificationGateway.objects.filter(channel='email', is_enabled=True).first()
                if getattr(request, '_mirror_forwarded', False):
                    # asked from the remote site → link to the remote login page
                    login_url = settings.MIRROR_PUBLIC_URL + '/login/'
                else:
                    login_url = request.build_absolute_uri('/login/')
                if gw:
                    send_password_reset_email(gw, target.email, target.username,
                                               temp_password, login_url)

                log_activity(target, 'PASSWORD_RESET_REQUESTED',
                             f'Temporary password issued for "{target.username}" '
                             f'(valid 30 min).', ip=get_ip(request))
            # Same message regardless of whether target was found/emailed —
            # avoids revealing which usernames/emails exist in the system.
            sent = True

    return render(request, 'monitor/password_reset.html', {
        'sent': sent, 'error': error,
    })


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

def _fmt_usage(total_seconds):
    """Format accumulated usage seconds as 'Xh Ym' / 'Ym' / '—'."""
    if not total_seconds:
        return '—'
    h, r = divmod(int(total_seconds), 3600)
    m = r // 60
    if h:
        return f'{h}h {m}m'
    if m:
        return f'{m}m'
    return '<1m'


@role_required('admin')
def user_list(request):
    users = User.objects.all().order_by('username')
    user_data = []
    user_details = []

    for u in users:
        role = get_role(u)

        try:
            profile = u.userprofile
            usage_seconds = profile.total_usage_seconds or 0
            last_activity = profile.last_activity_at
            designation = profile.designation
            mobile_number = profile.mobile_number
            whatsapp_number = profile.whatsapp_number
            telegram_handle = profile.telegram_handle
        except Exception:
            profile = None
            usage_seconds = 0
            last_activity = None
            designation = ''
            mobile_number = ''
            whatsapp_number = ''
            telegram_handle = ''

        logs = ActivityLog.objects.filter(user=u)

        login_count = logs.filter(action='LOGIN').count()
        logout_count = logs.filter(action='LOGOUT').count()
        failed_login_count = logs.filter(action='LOGIN_FAILED').count()

        last_login_log = logs.filter(action='LOGIN').first()
        last_logout_log = logs.filter(action='LOGOUT').first()
        last_failed_login_log = logs.filter(action='LOGIN_FAILED').first()

        recent_logs = logs[:8]

        user_data.append({
            'obj': u,
            'role': role,
            'usage_time': _fmt_usage(usage_seconds),
        })

        user_details.append({
            'obj': u,
            'profile': profile,
            'role': role,
            'designation': designation,
            'mobile_number': mobile_number,
            'whatsapp_number': whatsapp_number,
            'telegram_handle': telegram_handle,
            'usage_time': _fmt_usage(usage_seconds),
            'last_activity': last_activity,
            'login_count': login_count,
            'logout_count': logout_count,
            'failed_login_count': failed_login_count,
            'last_login': last_login_log.timestamp if last_login_log else None,
            'last_login_ip': last_login_log.ip_address if last_login_log else None,
            'last_logout': last_logout_log.timestamp if last_logout_log else None,
            'last_failed_login': last_failed_login_log.timestamp if last_failed_login_log else None,
            'profile_picture': profile.profile_picture,
            'recent_logs': recent_logs,
        })

    # Pending self-service profile change requests (email / mobile number)
    # are shown as a second tab on this same page so admins don't need a
    # separate top-nav item to approve them.
    from monitor.models import ProfileChangeRequest
    pending = ProfileChangeRequest.objects.filter(
        status='PENDING'
    ).select_related('user')

    return render(request, 'monitor/user_list.html', {
        'user_data': user_data,
        'user_details': user_details,
        'pending': pending,
        'role': get_role(request.user),
        'user': request.user,
    })


@role_required('admin')
def colocation_setpoints(request):
    config = SensorAlarmConfig.objects.first()

    if request.method == 'POST':
        def get_float(name):
            value = request.POST.get(name, '').strip()
            return float(value) if value else None

        try:
            temperature_low = get_float('temperature_low')
            temperature_high = get_float('temperature_high')
            humidity_low = get_float('humidity_low')
            humidity_high = get_float('humidity_high')

            cooldown_raw = request.POST.get(
                'alarm_cooldown_minutes', '30'
            ).strip()
            cooldown = int(cooldown_raw)

            if cooldown < 1:
                raise ValueError('Cooldown must be at least 1 minute.')

            if config is None:
                config = SensorAlarmConfig()

            config.temperature_low = temperature_low
            config.temperature_high = temperature_high
            config.humidity_low = humidity_low
            config.humidity_high = humidity_high
            config.alarm_cooldown_minutes = cooldown
            config.save()

            messages.success(
                request,
                'Colocation temperature/humidity alarm setpoints saved successfully.'
            )
            return redirect('colocation_setpoints')

        except (TypeError, ValueError):
            messages.error(
                request,
                'Please enter valid numeric values. Cooldown must be at least 1 minute.'
            )

    return render(request, 'monitor/colocation_setpoints.html', {
        'config': config,
        'role': get_role(request.user),
        'user': request.user,
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
    profile, _ = UserProfile.objects.get_or_create(user=target)

    if request.method == 'POST':
        new_role = request.POST.get('role', 'viewer')
        new_pass = request.POST.get('password', '').strip()

        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        designation = request.POST.get('designation', '').strip()
        mobile_number = request.POST.get('mobile_number', '').strip()
        whatsapp_number = request.POST.get('whatsapp_number', '').strip()
        telegram_handle = request.POST.get('telegram_handle', '').strip()

        if email and User.objects.exclude(id=target.id).filter(email=email).exists():
            error = f'Email "{email}" is already used by another user.'
        else:
            target.first_name = first_name
            target.last_name = last_name
            target.email = email
            target.save()

            profile.role = new_role
            profile.designation = designation
            profile.mobile_number = mobile_number
            profile.whatsapp_number = whatsapp_number
            profile.telegram_handle = telegram_handle

            profile_picture = request.FILES.get('profile_picture')
            if profile_picture:
                profile.profile_picture = profile_picture

            profile.save()

            password_changed = False
            if new_pass:
                target.set_password(new_pass)
                target.save()
                password_changed = True

            changed = (
                f'role="{new_role}", profile information updated'
            )
            if password_changed:
                changed += ', password changed'

            log_activity(
                request.user,
                'USER_EDITED',
                f'Edited user "{target.username}" — {changed}.',
                ip=get_ip(request)
            )

            messages.success(request, f'User "{target.username}" updated.')
            return redirect('user_list')

    current_role = profile.role or 'viewer'

    return render(request, 'monitor/user_form.html', {
        'error': error,
        'action': 'Edit',
        'target_user': target,
        'target_profile': profile,
        'current_role': current_role,
        'role': get_role(request.user),
        'user': request.user,
    })


@role_required('admin')
def user_toggle_active(request, user_id):
    """Enable/disable a login without deleting the account. A disabled
    account fails authenticate() immediately (Django's ModelBackend checks
    is_active) and login_view() shows a clear 'account disabled' message."""
    target = get_object_or_404(User, id=user_id)
    if target == request.user:
        messages.error(request, "You can't disable your own account.")
        return redirect('user_list')
    if request.method == 'POST':
        target.is_active = not target.is_active
        target.save()
        state = 'enabled' if target.is_active else 'disabled'
        log_activity(request.user, 'USER_EDITED',
                     f'Account "{target.username}" {state}.',
                     ip=get_ip(request))
        messages.success(request, f'Account "{target.username}" {state}.')
    return redirect('user_list')


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
    from monitor.daily_summary import get_generator_segments, fmt_duration
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

        pieces = get_generator_segments(c, seg['start'], seg['end'], mode_logs)
        for i, (p_start, p_end, gen) in enumerate(pieces):
            piece_secs = int((p_end - p_start).total_seconds())
            if piece_secs <= 0:
                continue
            is_ongoing_piece = seg['is_ongoing'] and (i == len(pieces) - 1)

            rows.append({
                'start':       p_start.astimezone(bdt).strftime('%I:%M:%S %p'),
                'end':         'ongoing…' if is_ongoing_piece else p_end.astimezone(bdt).strftime('%I:%M:%S %p'),
                'duration':    fmt_duration(piece_secs),
                'generator':   gen,
                'is_complete': c.is_complete and not is_ongoing_piece,
                'is_ongoing':  is_ongoing_piece,
                'cycle_type':  c.cycle_type,
            })

            # Only count minutes toward the day's total for real (non-blip) cycles,
            # matching the previous "completed, pdb_duration_sec > 0" intent —
            # but now measured per calendar day (and per generator piece) instead
            # of per whole cycle.
            if c.pdb_duration_sec > 0 or is_ongoing_piece:
                total_secs_sum += piece_secs

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
    from monitor.daily_summary import get_generator_segments, fmt_duration
    from django.utils import timezone as dj_timezone
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

        # This is a whole-cycle row (one row = one outage), so if more than
        # one generator was actually responsible (a changeover happened
        # mid-outage — see get_generator_segments()), show that plainly
        # instead of silently attributing the whole thing to whichever
        # generator merely happened to be active at the start.
        cycle_end_for_gen = end_dt or dj_timezone.now()

        # Explicit stored generator assignment has priority.
        # This includes historical generator assignments backfilled
        # from verified operational records.
        if c.manual_generator:
            gen = c.manual_generator
        else:
            pieces = get_generator_segments(
                c,
                c.outage_start,
                cycle_end_for_gen,
                mode_logs_all
            )
            distinct_gens = list(
                dict.fromkeys(
                    p[2]
                    for p in pieces
                    if (p[1] - p[0]).total_seconds() > 0
                )
            )
            gen = ' → '.join(distinct_gens) if distinct_gens else 'UNASSIGNED'

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

    # The mirror snapshot intentionally contains blank gateway secrets. A Save
    # forwarded from the remote must therefore treat a blank secret as "keep the
    # master's existing value". A direct Save on the master keeps the old behaviour
    # and may intentionally clear a secret by submitting a blank value.
    from_mirror = bool(getattr(request, '_mirror_forwarded', False))

    if channel == 'whatsapp':
        gw.wa_phone_number_id = data.get('wa_phone_number_id', '').strip()
        token = data.get('wa_access_token', '').strip()
        if token or not from_mirror:
            gw.wa_access_token = token
        gw.wa_from_number = data.get('wa_from_number', '').strip()
    elif channel == 'telegram':
        token = data.get('tg_bot_token', '').strip()
        if token or not from_mirror:
            gw.tg_bot_token = token
    elif channel == 'email':
        gw.email_host     = data.get('email_host',     'smtp.gmail.com').strip()
        gw.email_port     = int(data.get('email_port', 587))
        gw.email_username = data.get('email_username', '').strip()
        password = data.get('email_password', '').strip()
        if password or not from_mirror:
            gw.email_password = password
        gw.email_from = data.get('email_from', '').strip()

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
        alert_pac_status = d.get('alert_pac_status', False),
        daily_summary  = d.get('daily_summary',  False),
        monthly_report = d.get('monthly_report', False),
        colocation_data = d.get('colocation_data', False),
        colocation_alarm = d.get('colocation_alarm', False),
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
        r.alert_pac_status = d.get('alert_pac_status', r.alert_pac_status)
        r.monthly_report = d.get('monthly_report', r.monthly_report)
        r.colocation_data = d.get('colocation_data', r.colocation_data)
        r.colocation_alarm = d.get('colocation_alarm', r.colocation_alarm)
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
# MESSAGE TEMPLATES (admin-editable notification wording)
# ══════════════════════════════════════════════════════════════════════════════

from monitor.models import MessageTemplate
from monitor.notifications import TEMPLATE_PLACEHOLDERS, build_message as _preview_build_message

@role_required('admin')
def notif_message_templates(request):
    """
    Admin page to view/edit the wording used for each outgoing
    notification type. Shows the currently-active text (custom override if
    one is saved, otherwise the built-in default) plus which {placeholders}
    are available for that event type.
    """
    rows = []
    for event_type, label in MessageTemplate.EVENT_CHOICES:
        tpl = MessageTemplate.objects.filter(event_type=event_type).first()
        custom_text = tpl.template_text if tpl else ''
        rows.append({
            'event_type':   event_type,
            'label':        label,
            'custom_text':  custom_text,
            'has_override': bool(custom_text.strip()),
            'default_text': _preview_build_message(event_type, ignore_override=True),
            'placeholders': TEMPLATE_PLACEHOLDERS.get(event_type, []),
        })
    return render(request, 'monitor/notif_templates.html', {
        'rows': rows,
        'role': 'admin',
    })


@role_required('admin')
def notif_message_template_save(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    import json as _json
    d = _json.loads(request.body)
    event_type = d.get('event_type', '')
    text       = d.get('template_text', '')

    valid_types = dict(MessageTemplate.EVENT_CHOICES)
    if event_type not in valid_types:
        return JsonResponse({'ok': False, 'error': 'Unknown event type'})

    tpl, _ = MessageTemplate.objects.get_or_create(event_type=event_type)
    tpl.template_text = text
    tpl.updated_by = request.user.username
    tpl.save()

    log_activity(request.user, 'USER_EDITED',
                 f'Updated "{valid_types[event_type]}" message template.',
                 ip=get_ip(request))
    return JsonResponse({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# MONTHLY LOADSHEDDING REPORT
# ══════════════════════════════════════════════════════════════════════════════

@role_required('admin')
def notif_send_monthly_report_now(request):
    """
    Manual trigger — lets an admin send the monthly report on demand
    (testing, or a re-send) instead of waiting for the 1st-of-month cron.
    POST body may include {"year": 2026, "month": 9} to pick a specific
    month; omitted = the month that just ended (same as the cron default).
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})

    import json as _json
    from monitor.monthly_report import send_monthly_report, fmt_duration

    d = _json.loads(request.body) if request.body else {}
    year  = d.get('year')
    month = d.get('month')

    try:
        summary, sent_count, failed = send_monthly_report(year=year, month=month)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})

    log_activity(request.user, 'USER_EDITED',
                 f'Manually sent monthly loadshedding report for {summary["label"]} '
                 f'to {sent_count} recipient(s).', ip=get_ip(request))

    return JsonResponse({
        'ok': True,
        'label': summary['label'],
        'outage_count': summary['outage_count'],
        'total_downtime': fmt_duration(summary['grand_total']),
        'sent_count': sent_count,
        'failed': failed,
    })



# ══════════════════════════════════════════════════════════════════════════════
# GENERATOR RUNTIME REPORT
# ══════════════════════════════════════════════════════════════════════════════

@role_required('user', 'admin', 'viewer')
def generator_runtime_report(request):
    """Generator Runtime Report — currently under construction."""
    return render(
        request,
        "monitor/generator_runtime_report.html",
        {
            "role": get_role(request.user),
            "user": request.user,
        }
    )


@role_required('user', 'admin')
def generator_fuel(request):
    """
    Generator Fuel register and consumption calculation.

    Fuel Loaded:
        current AFTER - current BEFORE

    Fuel Used:
        previous AFTER - current BEFORE

    Generator runtime for a fuel interval:
        actual PDB outage duration assigned to that generator.
    """
    from decimal import Decimal, InvalidOperation
    from datetime import datetime as dt
    import calendar
    import pytz

    from monitor.models import (
        GeneratorFuelLog,
        GeneratorModeLog,
        OutageCycle,
    )

    bdt = pytz.timezone('Asia/Dhaka')
    role = get_role(request.user)

    # --------------------------------------------------------
    # ADD NEW FUEL READING
    # --------------------------------------------------------
    if request.method == 'POST':

        if role not in ('admin', 'user'):
            messages.error(request, 'You do not have permission to add fuel records.')
            return redirect('generator_fuel')

        generator = request.POST.get('generator', '').strip()
        date_str = request.POST.get('date', '').strip()
        time_str = request.POST.get('time', '').strip()
        before_raw = request.POST.get('fuel_before_l', '').strip()
        after_raw = request.POST.get('fuel_after_l', '').strip()
        note = request.POST.get('note', '').strip()

        if generator not in ('Gen-01', 'Gen-02'):
            messages.error(request, 'Please select a valid generator.')
            return redirect('generator_fuel')

        try:
            naive = dt.strptime(
                f'{date_str} {time_str}',
                '%Y-%m-%d %H:%M'
            )
            reading_at = bdt.localize(naive)
        except Exception:
            messages.error(request, 'Valid date and time are required.')
            return redirect('generator_fuel')

        try:
            fuel_before = Decimal(before_raw)
            fuel_after = Decimal(after_raw)
        except (InvalidOperation, TypeError):
            messages.error(request, 'Fuel readings must be valid numbers.')
            return redirect('generator_fuel')

        if fuel_before < 0 or fuel_after < 0:
            messages.error(request, 'Fuel reading cannot be negative.')
            return redirect('generator_fuel')

        if fuel_after < fuel_before:
            messages.error(
                request,
                'After Fuel cannot be lower than Before Fuel.'
            )
            return redirect('generator_fuel')

        duplicate = GeneratorFuelLog.objects.filter(
            generator=generator,
            reading_at=reading_at
        ).first()

        if duplicate:
            messages.error(
                request,
                f'{generator} already has a fuel record at this exact time.'
            )
            return redirect('generator_fuel')

        GeneratorFuelLog.objects.create(
            generator=generator,
            reading_at=reading_at,
            fuel_before_l=fuel_before,
            fuel_after_l=fuel_after,
            note=note,
            added_by=request.user.username,
        )

        log_activity(
            request.user,
            'USER_EDITED',
            (
                f'Added generator fuel record: {generator}, '
                f'before={fuel_before}L, after={fuel_after}L.'
            ),
            ip=get_ip(request)
        )

        messages.success(
            request,
            f'{generator} fuel record added successfully.'
        )

        return redirect('generator_fuel')

    # --------------------------------------------------------
    # GENERATOR ASSIGNMENT FOR AN OUTAGE CYCLE
    # --------------------------------------------------------
    mode_logs = list(
        GeneratorModeLog.objects
        .all()
        .order_by('switched_at')
    )

    def cycle_generator(cycle):
        if cycle.manual_generator:
            return cycle.manual_generator

        chosen = None

        for entry in mode_logs:
            if entry.switched_at <= cycle.outage_start:
                chosen = entry.generator
            else:
                break

        return chosen

    # --------------------------------------------------------
    # RUNTIME BETWEEN TWO DATES
    #
    # Per agreed rule:
    # Generator Runtime = actual power-outage duration.
    # Therefore pdb_duration_sec is used here.
    # --------------------------------------------------------
    def runtime_for_generator(generator, start_dt, end_dt):

        if not start_dt or not end_dt or end_dt <= start_dt:
            return 0

        cycles = (
            OutageCycle.objects
            .filter(
                outage_start__gte=start_dt,
                outage_start__lt=end_dt,
                pdb_duration_sec__gt=0,
            )
            .order_by('outage_start')
        )

        total = 0

        for cycle in cycles:
            if cycle_generator(cycle) == generator:
                total += cycle.pdb_duration_sec or 0

        return total

    def fmt_runtime(seconds):
        seconds = int(seconds or 0)

        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)

        if h:
            return f'{h}h {m:02d}m'
        if m:
            return f'{m}m {s:02d}s'
        if s:
            return f'{s}s'

        return '—'

    # --------------------------------------------------------
    # SELECTED MONTH
    # --------------------------------------------------------
    now_bdt = dt.now(bdt)

    month_value = request.GET.get('month', '').strip()

    try:
        if month_value:
            month_start_naive = dt.strptime(
                month_value + '-01',
                '%Y-%m-%d'
            )
        else:
            month_start_naive = dt(
                now_bdt.year,
                now_bdt.month,
                1
            )
            month_value = month_start_naive.strftime('%Y-%m')

        month_start = bdt.localize(month_start_naive)

    except ValueError:
        month_start = bdt.localize(
            dt(now_bdt.year, now_bdt.month, 1)
        )
        month_value = month_start.strftime('%Y-%m')

    if month_start.month == 12:
        next_month = bdt.localize(
            dt(month_start.year + 1, 1, 1)
        )
    else:
        next_month = bdt.localize(
            dt(month_start.year, month_start.month + 1, 1)
        )

    month_label = month_start.strftime('%B %Y')

    # --------------------------------------------------------
    # BUILD RECORD CALCULATIONS
    # --------------------------------------------------------
    all_logs_asc = list(
        GeneratorFuelLog.objects
        .all()
        .order_by('generator', 'reading_at', 'id')
    )

    previous_by_generator = {}
    calculated_asc = []

    for entry in all_logs_asc:

        previous = previous_by_generator.get(entry.generator)

        loaded = entry.fuel_after_l - entry.fuel_before_l

        fuel_used = None
        runtime_sec = 0
        consumption_lph = None
        status = 'BASELINE'
        status_label = 'Baseline'

        if previous:

            fuel_used = (
                previous.fuel_after_l -
                entry.fuel_before_l
            )

            runtime_sec = runtime_for_generator(
                entry.generator,
                previous.reading_at,
                entry.reading_at
            )

            if fuel_used < 0:
                status = 'CHECK'
                status_label = 'Check Reading'

            elif runtime_sec <= 0:
                status = 'PARTIAL'
                status_label = 'Partial'

            else:
                runtime_hours = Decimal(runtime_sec) / Decimal('3600')

                if runtime_hours > 0:
                    consumption_lph = fuel_used / runtime_hours

                status = 'COMPLETE'
                status_label = 'Complete'

        calculated_asc.append({
            'obj': entry,
            'previous': previous,
            'fuel_loaded': loaded,
            'fuel_used': fuel_used,
            'runtime_sec': runtime_sec,
            'runtime_fmt': fmt_runtime(runtime_sec),
            'consumption_lph': consumption_lph,
            'status': status,
            'status_label': status_label,
        })

        previous_by_generator[entry.generator] = entry

    records = list(reversed(calculated_asc))

    # --------------------------------------------------------
    # MONTHLY SUMMARY
    # --------------------------------------------------------
    monthly = {}

    for gen in ('Gen-01', 'Gen-02'):

        gen_rows = [
            row for row in calculated_asc
            if (
                row['obj'].generator == gen
                and month_start <= row['obj'].reading_at < next_month
            )
        ]

        loaded_total = sum(
            (row['fuel_loaded'] for row in gen_rows),
            Decimal('0')
        )

        used_values = [
            row['fuel_used']
            for row in gen_rows
            if row['fuel_used'] is not None and row['fuel_used'] >= 0
        ]

        used_total = sum(
            used_values,
            Decimal('0')
        )

        runtime_sec = runtime_for_generator(
            gen,
            month_start,
            next_month
        )

        monthly_lph = None

        if runtime_sec > 0 and used_values:
            monthly_lph = (
                used_total /
                (Decimal(runtime_sec) / Decimal('3600'))
            )

        complete_count = sum(
            1 for row in gen_rows
            if row['status'] == 'COMPLETE'
        )

        monthly[gen] = {
            'loaded': loaded_total,
            'used': used_total if used_values else None,
            'runtime_sec': runtime_sec,
            'runtime_fmt': fmt_runtime(runtime_sec),
            'consumption_lph': monthly_lph,
            'record_count': len(gen_rows),
            'complete_count': complete_count,
        }

    # --------------------------------------------------------
    # TILL-DATE SUMMARY FROM 26 MAY 2026
    # --------------------------------------------------------
    report_start = bdt.localize(dt(2026, 5, 26, 0, 0))

    till_date = {}

    for gen in ('Gen-01', 'Gen-02'):

        gen_rows = [
            row for row in calculated_asc
            if (
                row['obj'].generator == gen
                and row['obj'].reading_at >= report_start
            )
        ]

        loaded_total = sum(
            (row['fuel_loaded'] for row in gen_rows),
            Decimal('0')
        )

        used_values = [
            row['fuel_used']
            for row in gen_rows
            if row['fuel_used'] is not None and row['fuel_used'] >= 0
        ]

        used_total = sum(
            used_values,
            Decimal('0')
        )

        runtime_sec = runtime_for_generator(
            gen,
            report_start,
            now_bdt
        )

        lph = None

        if runtime_sec > 0 and used_values:
            lph = (
                used_total /
                (Decimal(runtime_sec) / Decimal('3600'))
            )

        latest = (
            GeneratorFuelLog.objects
            .filter(generator=gen)
            .order_by('-reading_at')
            .first()
        )

        till_date[gen] = {
            'loaded': loaded_total,
            'used': used_total if used_values else None,
            'runtime_sec': runtime_sec,
            'runtime_fmt': fmt_runtime(runtime_sec),
            'consumption_lph': lph,
            'latest_level': latest.fuel_after_l if latest else None,
            'record_count': len(gen_rows),
        }

    return render(
        request,
        'monitor/generator_fuel.html',
        {
            'role': role,
            'user': request.user,
            'records': records,
            'monthly': monthly,
            'till_date': till_date,
            'month_value': month_value,
            'month_label': month_label,
            'report_start': report_start,
            'now_bdt': now_bdt,
        }
    )


@role_required('user', 'admin', 'viewer')
def generator_cycle_audit(request):
    """Generator Cycle Audit — currently under maintenance."""
    return render(
        request,
        "monitor/generator_cycle_audit.html",
        {
            "role": get_role(request.user),
            "user": request.user,
        }
    )


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
@transaction.atomic
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

    entry = GeneratorModeLog.objects.create(
        generator=generator,
        switched_at=switched_at,
        note=note,
        added_by=request.user.username,
    )

    detail = (
        f'Generator shift added: ID:{entry.id}, {generator}, '
        f'time={switched_at}, by={request.user.username}'
    )
    log_activity(request.user, 'GEN_SHIFT_ADD', detail, get_ip(request))
    Event.objects.create(device=None, level='NOTICE', message=detail)
    return JsonResponse({'ok': True})


@role_required('admin')
@transaction.atomic
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

    previous_generator = e.generator
    previous_time = e.switched_at

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

    detail = (
        f'Generator shift edited: ID:{e.id}, '
        f'{previous_generator} at {previous_time} -> '
        f'{e.generator} at {e.switched_at}, '
        f'by={request.user.username}'
    )
    log_activity(request.user, 'GEN_SHIFT_EDIT', detail, get_ip(request))
    Event.objects.create(device=None, level='NOTICE', message=detail)
    return JsonResponse({'ok': True})


@role_required('admin')
@transaction.atomic
def generator_log_delete(request, eid):
    """Delete an entry. Admin only."""
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    entry = GeneratorModeLog.objects.filter(id=eid).first()
    if not entry:
        return JsonResponse({'ok': False, 'error': 'Entry not found'})

    detail = (
        f'Generator shift deleted: ID:{entry.id}, '
        f'{entry.generator} at {entry.switched_at}, '
        f'by={request.user.username}'
    )

    entry.delete()
    log_activity(request.user, 'GEN_SHIFT_DELETE', detail, get_ip(request))
    Event.objects.create(device=None, level='NOTICE', message=detail)
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
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if profile.must_change_password:
            profile.must_change_password = False
            profile.temp_password_expires_at = None
            profile.save()
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
            'id':        c.id,
            'start':     c.outage_start.astimezone(bdt).strftime('%d/%m %I:%M:%S %p') if c.outage_start else '—',
            'end':       end_dt.astimezone(bdt).strftime('%d/%m %I:%M:%S %p') if end_dt else '—',
            'duration':  dur,
            'type':      c.cycle_type,
            'complete':  c.is_complete,
            'is_manual': c.is_manual,
            'added_by':  c.added_by or '—',
            'note':      c.alarm_reason or '',
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
    # The remote mirror cannot reach the local Uptime Kuma server.
    monitors = [] if settings.IS_MIRROR else get_kuma_monitors()
    return render(request, "monitor/uptime_status.html", {
        "monitors": monitors,
        "role": get_role(request.user),
    })

def uptime_status_log(request, monitor_id):
    logs = [] if settings.IS_MIRROR else get_monitor_log(monitor_id)
    return JsonResponse({"logs": logs})


@role_required('user', 'admin', 'viewer')
def pac_status_view(request):
    import pytz
    from datetime import datetime as dt_class
    bdt = pytz.timezone('Asia/Dhaka')
    units = get_all_pac_status()
    return render(request, "monitor/pac_status.html", {
        "units": units,
        "role": get_role(request.user),
        "user": request.user,
        "last_updated": dt_class.now(bdt).strftime('%d/%m/%Y %I:%M:%S %p'),
    })


# ══════════════════════════════════════════════════════════════════════════════
# TUYA TEMPERATURE / HUMIDITY SENSOR (colocation room)
# ══════════════════════════════════════════════════════════════════════════════

@role_required('user', 'admin', 'viewer')
def sensor_status_view(request):
    import pytz
    from datetime import datetime as dt_class
    bdt = pytz.timezone('Asia/Dhaka')
    from monitor.models import SensorReading
    from monitor.tuya_client import tuya_configured

    latest = SensorReading.objects.first()
    recent = list(SensorReading.objects.all()[:100])

    return render(request, "monitor/sensor_status.html", {
        "latest": latest,
        "recent": recent,
        "configured": tuya_configured(),
        "role": get_role(request.user),
        "user": request.user,
        "last_updated": dt_class.now(bdt).strftime('%d/%m/%Y %I:%M:%S %p'),
    })


def api_sensor_status(request):
    """Latest colocation-room temp/humidity reading, for the dashboard
    widget and the sensor page's auto-refresh. No role check — same
    openness as api_status/api_daily_summary elsewhere in this file."""
    import pytz
    bdt = pytz.timezone('Asia/Dhaka')
    from monitor.models import SensorReading
    from monitor.tuya_client import tuya_configured

    latest = SensorReading.objects.first()
    if not latest:
        return JsonResponse({
            'has_data': False,
            'configured': tuya_configured(),
        })

    return JsonResponse({
        'has_data': True,
        'configured': tuya_configured(),
        'device_name': latest.device_name,
        'temperature_c': latest.temperature_c,
        'humidity_pct': latest.humidity_pct,
        'battery_pct': latest.battery_pct,
        'battery_state': latest.battery_state,
        'is_online': latest.is_online,
        'recorded_at': latest.recorded_at.astimezone(bdt).strftime('%d/%m/%Y %I:%M:%S %p'),
    })


def _sensor_range_cutoff(range_key):
    """Translate a ?range= query value into a timedelta, or None for 'all'."""
    from datetime import timedelta

    mapping = {
        '1h':  timedelta(hours=1),
        '6h':  timedelta(hours=6),
        '24h': timedelta(hours=24),
        '7d':  timedelta(days=7),
        '30d': timedelta(days=30),
    }
    return mapping.get(range_key)


def _sensor_filtered_queryset(request):
    """
    Build the sensor queryset using either:
      ?range=1h/6h/24h/7d/30d/all
    or:
      ?from=YYYY-MM-DD&to=YYYY-MM-DD

    Custom dates are interpreted as Bangladesh (Asia/Dhaka) dates.
    """
    import pytz
    from datetime import datetime, time, timedelta
    from django.utils import timezone as dj_tz
    from monitor.models import SensorReading

    bdt = pytz.timezone('Asia/Dhaka')

    range_key = request.GET.get('range', '24h')
    from_date = request.GET.get('from', '').strip()
    to_date = request.GET.get('to', '').strip()

    qs = SensorReading.objects.all().order_by('recorded_at')

    # Custom From/To date has priority over the relative range buttons.
    if from_date or to_date:
        try:
            if from_date:
                start_date = datetime.strptime(from_date, '%Y-%m-%d').date()
                start_dt = bdt.localize(datetime.combine(start_date, time.min))
                qs = qs.filter(recorded_at__gte=start_dt)

            if to_date:
                end_date = datetime.strptime(to_date, '%Y-%m-%d').date()
                end_dt = bdt.localize(
                    datetime.combine(end_date + timedelta(days=1), time.min)
                )
                qs = qs.filter(recorded_at__lt=end_dt)

        except ValueError:
            # If an invalid custom date is supplied, safely fall back to range.
            delta = _sensor_range_cutoff(range_key)
            if delta:
                qs = qs.filter(recorded_at__gte=dj_tz.now() - delta)
    else:
        delta = _sensor_range_cutoff(range_key)
        if delta:
            qs = qs.filter(recorded_at__gte=dj_tz.now() - delta)

    return qs, range_key, from_date, to_date


def _sensor_report_stats(qs, bdt):
    """Calculate report statistics for the selected sensor period."""
    from django.db.models import Min, Max, Avg, Count

    total = qs.count()

    temp_qs = qs.filter(temperature_c__isnull=False)
    humidity_qs = qs.filter(humidity_pct__isnull=False)

    temp_stats = temp_qs.aggregate(
        minimum=Min('temperature_c'),
        maximum=Max('temperature_c'),
        average=Avg('temperature_c'),
    )

    humidity_stats = humidity_qs.aggregate(
        minimum=Min('humidity_pct'),
        maximum=Max('humidity_pct'),
        average=Avg('humidity_pct'),
    )

    t_high = temp_qs.order_by('-temperature_c', 'recorded_at').first()
    t_low = temp_qs.order_by('temperature_c', 'recorded_at').first()

    h_high = humidity_qs.order_by('-humidity_pct', 'recorded_at').first()
    h_low = humidity_qs.order_by('humidity_pct', 'recorded_at').first()

    first = qs.order_by('recorded_at').first()
    last = qs.order_by('-recorded_at').first()

    def fmt_dt(obj):
        if not obj:
            return None
        return obj.recorded_at.astimezone(bdt).strftime(
            '%d/%m/%Y %I:%M:%S %p'
        )

    duration_seconds = None
    if first and last:
        duration_seconds = int(
            (last.recorded_at - first.recorded_at).total_seconds()
        )

    return {
        'total_readings': total,

        'temperature': {
            'min': temp_stats['minimum'],
            'max': temp_stats['maximum'],
            'avg': temp_stats['average'],
            'min_time': fmt_dt(t_low),
            'max_time': fmt_dt(t_high),
        },

        'humidity': {
            'min': humidity_stats['minimum'],
            'max': humidity_stats['maximum'],
            'avg': humidity_stats['average'],
            'min_time': fmt_dt(h_low),
            'max_time': fmt_dt(h_high),
        },

        'online_count': qs.filter(is_online=True).count(),
        'offline_count': qs.filter(is_online=False).count(),

        'first_reading': fmt_dt(first),
        'last_reading': fmt_dt(last),
        'duration_seconds': duration_seconds,
    }


def api_sensor_history(request):
    """
    Time-series points + report statistics.

    Supports:
      ?range=1h/6h/24h/7d/30d/all
      ?from=YYYY-MM-DD&to=YYYY-MM-DD

    Custom From/To dates take priority over the range buttons.
    """
    import pytz
    from monitor.models import SensorReading

    bdt = pytz.timezone('Asia/Dhaka')

    qs, range_key, from_date, to_date = _sensor_filtered_queryset(request)

    # Keep the browser payload reasonably small for long periods.
    rows = list(qs)
    max_points = 500

    if len(rows) > max_points:
        step = len(rows) // max_points + 1
        rows = rows[::step]

    points = [{
        't': r.recorded_at.astimezone(bdt).strftime('%Y-%m-%d %H:%M:%S'),
        'temperature_c': r.temperature_c,
        'humidity_pct': r.humidity_pct,
        'battery_pct': r.battery_pct,
        'battery_state': r.battery_state,
        'is_online': r.is_online,
    } for r in rows]

    # Statistics use the complete filtered queryset, not the
    # 500-point chart sample.
    stats = _sensor_report_stats(qs, bdt)

    return JsonResponse({
        'range': range_key,
        'from': from_date,
        'to': to_date,
        'points': points,
        'stats': stats,
    })


@role_required('user', 'admin', 'viewer')
def sensor_export_csv(request):
    """
    Download sensor history as CSV.

    Supports:
      ?range=1h/6h/24h/7d/30d/all
      ?from=YYYY-MM-DD&to=YYYY-MM-DD
    """
    import csv
    import pytz
    from django.http import HttpResponse

    bdt = pytz.timezone('Asia/Dhaka')

    qs, range_key, from_date, to_date = _sensor_filtered_queryset(request)

    if from_date or to_date:
        filename_suffix = f"{from_date or 'start'}_to_{to_date or 'end'}"
    else:
        filename_suffix = range_key

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = (
        f'attachment; filename="colocation_sensor_{filename_suffix}.csv"'
    )

    writer = csv.writer(response)

    writer.writerow([
        'Recorded At (BDT)',
        'Device',
        'Temperature (C)',
        'Humidity (%)',
        'Battery (%)',
        'Battery State',
        'Online',
    ])

    for r in qs:
        writer.writerow([
            r.recorded_at.astimezone(bdt).strftime(
                '%d/%m/%Y %I:%M:%S %p'
            ),
            r.device_name,
            r.temperature_c if r.temperature_c is not None else '',
            r.humidity_pct if r.humidity_pct is not None else '',
            r.battery_pct if r.battery_pct is not None else '',
            r.battery_state or '',
            'Yes' if r.is_online else 'No',
        ])

    return response



# ══════════════════════════════════════════════════════════════════════════════
# GENERATOR FUEL REPORT
# ══════════════════════════════════════════════════════════════════════════════

@role_required('user', 'admin', 'viewer')
def generator_fuel_report(request):
    """
    Generator Fuel Report.

    Accessible by:
        Admin
        User
        Viewer

    Fuel Loaded:
        After Fuel - Before Fuel

    Fuel Used:
        Previous After Fuel - Current Before Fuel

    Runtime:
        PDB outage duration assigned to the selected generator.
    """
    from decimal import Decimal
    from datetime import datetime as dt, timedelta
    import json
    import pytz

    from monitor.models import (
        GeneratorFuelLog,
        GeneratorModeLog,
        OutageCycle,
    )

    bdt = pytz.timezone('Asia/Dhaka')
    role = get_role(request.user)

    report_floor = bdt.localize(
        dt(2026, 4, 1, 0, 0, 0)
    )

    now_bdt = dt.now(bdt)

    # --------------------------------------------------------
    # GENERATOR ASSIGNMENT
    # --------------------------------------------------------
    mode_logs = list(
        GeneratorModeLog.objects
        .all()
        .order_by('switched_at')
    )

    def cycle_generator(cycle):
        if cycle.manual_generator:
            return cycle.manual_generator

        chosen = None

        for entry in mode_logs:
            if entry.switched_at <= cycle.outage_start:
                chosen = entry.generator
            else:
                break

        return chosen

    # --------------------------------------------------------
    # RUNTIME
    # --------------------------------------------------------
    def runtime_for_generator(generator, start_dt, end_dt):

        if not start_dt or not end_dt or end_dt <= start_dt:
            return 0

        cycles = (
            OutageCycle.objects
            .filter(
                outage_start__gte=start_dt,
                outage_start__lt=end_dt,
                pdb_duration_sec__gt=0,
            )
            .order_by('outage_start')
        )

        total = 0

        for cycle in cycles:
            if cycle_generator(cycle) == generator:
                total += cycle.pdb_duration_sec or 0

        return total

    def fmt_runtime(seconds):
        seconds = int(seconds or 0)

        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)

        if h:
            return f'{h}h {m:02d}m'
        if m:
            return f'{m}m {s:02d}s'
        if s:
            return f'{s}s'

        return '—'

    # --------------------------------------------------------
    # FILTER MODE
    #
    # Default:
    #   Monthly report for the PREVIOUS calendar month.
    #
    # Modes:
    #   month = selected calendar month
    #   range = custom date range
    #   all   = 01-Apr-2026 through current time
    # --------------------------------------------------------

    current_month_start = bdt.localize(
        dt(now_bdt.year, now_bdt.month, 1)
    )

    if current_month_start.month == 1:
        previous_month_start = bdt.localize(
            dt(current_month_start.year - 1, 12, 1)
        )
    else:
        previous_month_start = bdt.localize(
            dt(
                current_month_start.year,
                current_month_start.month - 1,
                1
            )
        )

    if previous_month_start < report_floor:
        previous_month_start = report_floor

    default_month_value = previous_month_start.strftime('%Y-%m')

    mode = request.GET.get('mode', 'month').strip().lower()

    month_value = request.GET.get(
        'month',
        default_month_value
    ).strip()

    from_value = request.GET.get('from', '').strip()
    to_value = request.GET.get('to', '').strip()

    # --------------------------------------------------------
    # ALL TIME
    # --------------------------------------------------------
    if mode == 'all':

        range_start = report_floor
        range_end = now_bdt + timedelta(seconds=1)

        report_label = (
            f"All Time · "
            f"{report_floor.strftime('%d %b %Y')} – "
            f"{now_bdt.strftime('%d %b %Y')}"
        )

    # --------------------------------------------------------
    # CUSTOM DATE RANGE
    # --------------------------------------------------------
    elif mode == 'range':

        if not from_value:
            from_value = report_floor.strftime('%Y-%m-%d')

        if not to_value:
            to_value = now_bdt.strftime('%Y-%m-%d')

        try:
            range_start = bdt.localize(
                dt.strptime(from_value, '%Y-%m-%d')
            )

            range_end_day = bdt.localize(
                dt.strptime(to_value, '%Y-%m-%d')
            )

        except ValueError:
            range_start = report_floor

            range_end_day = bdt.localize(
                dt(
                    now_bdt.year,
                    now_bdt.month,
                    now_bdt.day
                )
            )

            from_value = report_floor.strftime('%Y-%m-%d')
            to_value = now_bdt.strftime('%Y-%m-%d')

        if range_start < report_floor:
            range_start = report_floor
            from_value = report_floor.strftime('%Y-%m-%d')

        today_start = bdt.localize(
            dt(
                now_bdt.year,
                now_bdt.month,
                now_bdt.day
            )
        )

        if range_end_day > today_start:
            range_end_day = today_start
            to_value = today_start.strftime('%Y-%m-%d')

        if range_end_day < range_start:
            range_end_day = range_start
            to_value = range_start.strftime('%Y-%m-%d')

        range_end = range_end_day + timedelta(days=1)

        report_label = (
            f"{range_start.strftime('%d %b %Y')} – "
            f"{range_end_day.strftime('%d %b %Y')}"
        )

    # --------------------------------------------------------
    # MONTHLY
    # --------------------------------------------------------
    else:
        mode = 'month'

        try:
            month_naive = dt.strptime(
                month_value + '-01',
                '%Y-%m-%d'
            )

        except ValueError:
            month_naive = dt(
                previous_month_start.year,
                previous_month_start.month,
                1
            )

            month_value = default_month_value

        range_start = bdt.localize(month_naive)

        if range_start < report_floor:
            range_start = report_floor
            month_value = report_floor.strftime('%Y-%m')

        if range_start.month == 12:
            range_end = bdt.localize(
                dt(range_start.year + 1, 1, 1)
            )
        else:
            range_end = bdt.localize(
                dt(
                    range_start.year,
                    range_start.month + 1,
                    1
                )
            )

        report_label = range_start.strftime('%B %Y')

    # --------------------------------------------------------
    # BUILD CALCULATED FUEL INTERVALS
    # --------------------------------------------------------
    all_logs = list(
        GeneratorFuelLog.objects
        .all()
        .order_by('generator', 'reading_at', 'id')
    )

    previous_by_generator = {}
    calculated = []

    for entry in all_logs:

        previous = previous_by_generator.get(entry.generator)

        loaded = (
            entry.fuel_after_l -
            entry.fuel_before_l
        )

        fuel_used = None
        level_increase_l = None
        runtime_sec = 0
        consumption_lph = None
        status = 'BASELINE'

        if previous:

            raw_fuel_used = (
                previous.fuel_after_l -
                entry.fuel_before_l
            )

            runtime_sec = runtime_for_generator(
                entry.generator,
                previous.reading_at,
                entry.reading_at
            )

            # Negative calculated fuel usage is not treated as
            # consumption. Keep the record as a partial interval.
            if raw_fuel_used < 0:
                fuel_used = None
                consumption_lph = None
                status = 'PARTIAL'

            else:
                fuel_used = raw_fuel_used

                if runtime_sec <= 0:
                    status = 'PARTIAL'

                else:
                    runtime_hours = (
                        Decimal(runtime_sec) /
                        Decimal('3600')
                    )

                    if runtime_hours > 0:
                        consumption_lph = (
                            fuel_used /
                            runtime_hours
                        )

                    status = 'COMPLETE'

        calculated.append({
            'obj': entry,
            'fuel_loaded': loaded,
            'fuel_used': fuel_used,
            'level_increase_l': level_increase_l,
            'runtime_sec': runtime_sec,
            'runtime_fmt': fmt_runtime(runtime_sec),
            'consumption_lph': consumption_lph,
            'status': status,
        })

        previous_by_generator[entry.generator] = entry

    # --------------------------------------------------------
    # SELECTED REPORT ROWS
    # --------------------------------------------------------
    selected_rows = [
        row for row in calculated
        if (
            range_start
            <= row['obj'].reading_at
            < range_end
        )
    ]

    # --------------------------------------------------------
    # GENERATOR SUMMARY
    # --------------------------------------------------------
    summary = {}

    for gen in ('Gen-01', 'Gen-02'):

        rows = [
            row for row in selected_rows
            if row['obj'].generator == gen
        ]

        loaded = sum(
            (
                row['fuel_loaded']
                for row in rows
            ),
            Decimal('0')
        )

        used_values = [
            row['fuel_used']
            for row in rows
            if (
                row['fuel_used'] is not None
                and row['fuel_used'] >= 0
            )
        ]

        used = sum(
            used_values,
            Decimal('0')
        )

        runtime_sec = runtime_for_generator(
            gen,
            range_start,
            range_end
        )

        lph = None

        if runtime_sec > 0 and used_values:
            lph = (
                used /
                (
                    Decimal(runtime_sec) /
                    Decimal('3600')
                )
            )

        latest = (
            GeneratorFuelLog.objects
            .filter(
                generator=gen,
                reading_at__lt=range_end
            )
            .order_by('-reading_at')
            .first()
        )

        summary[gen] = {
            'loaded': loaded,
            'used': used if used_values else None,
            'runtime_sec': runtime_sec,
            'runtime_fmt': fmt_runtime(runtime_sec),
            'consumption_lph': lph,
            'entries': len(rows),
            'latest_level': (
                latest.fuel_after_l
                if latest else None
            ),
        }

    # --------------------------------------------------------
    # COMBINED TOTALS
    # --------------------------------------------------------
    total_loaded = (
        summary['Gen-01']['loaded'] +
        summary['Gen-02']['loaded']
    )

    used_parts = [
        s['used']
        for s in summary.values()
        if s['used'] is not None
    ]

    total_used = (
        sum(used_parts, Decimal('0'))
        if used_parts else None
    )

    total_runtime_sec = (
        summary['Gen-01']['runtime_sec'] +
        summary['Gen-02']['runtime_sec']
    )

    overall_lph = None

    if total_used is not None and total_runtime_sec > 0:
        overall_lph = (
            total_used /
            (
                Decimal(total_runtime_sec) /
                Decimal('3600')
            )
        )

    totals = {
        'loaded': total_loaded,
        'used': total_used,
        'runtime_fmt': fmt_runtime(total_runtime_sec),
        'runtime_sec': total_runtime_sec,
        'consumption_lph': overall_lph,
        'entries': len(selected_rows),
    }

    # --------------------------------------------------------
    # CHART DATA
    # --------------------------------------------------------
    chart_labels = []
    chart_gen1 = []
    chart_gen2 = []

    for row in sorted(
        selected_rows,
        key=lambda x: x['obj'].reading_at
    ):
        if row['consumption_lph'] is None:
            continue

        label = row['obj'].reading_at.astimezone(
            bdt
        ).strftime('%d %b %H:%M')

        chart_labels.append(label)

        if row['obj'].generator == 'Gen-01':
            chart_gen1.append(
                float(row['consumption_lph'])
            )
            chart_gen2.append(None)
        else:
            chart_gen1.append(None)
            chart_gen2.append(
                float(row['consumption_lph'])
            )

    runtime_chart = {
        'labels': ['Generator 01', 'Generator 02'],
        'values': [
            round(
                summary['Gen-01']['runtime_sec'] / 3600,
                2
            ),
            round(
                summary['Gen-02']['runtime_sec'] / 3600,
                2
            ),
        ],
    }

    fuel_chart = {
        'labels': ['Generator 01', 'Generator 02'],
        'loaded': [
            float(summary['Gen-01']['loaded']),
            float(summary['Gen-02']['loaded']),
        ],
        'used': [
            float(summary['Gen-01']['used'] or 0),
            float(summary['Gen-02']['used'] or 0),
        ],
    }

    return render(
        request,
        'monitor/generator_fuel_report.html',
        {
            'role': role,
            'user': request.user,

            'report_floor': report_floor,
            'report_label': report_label,

            'mode': mode,
            'month_value': month_value,
            'from_value': from_value,
            'to_value': to_value,

            'rows': list(reversed(selected_rows)),
            'summary': summary,
            'totals': totals,

            'chart_labels_json': json.dumps(chart_labels),
            'chart_gen1_json': json.dumps(chart_gen1),
            'chart_gen2_json': json.dumps(chart_gen2),
            'runtime_chart_json': json.dumps(runtime_chart),
            'fuel_chart_json': json.dumps(fuel_chart),
        }
    )
