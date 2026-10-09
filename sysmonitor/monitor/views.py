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

    from .system_revision import bump_for_activity
    bump_for_activity(user, action, detail)


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



def mirror_version(request):
    """
    Tiny REMOTE sync-version endpoint.

    The browser polls this endpoint and refreshes only page data when a new
    mirror snapshot arrives. It never reloads the whole page.
    """
    import time as _time
    from django.conf import settings as _settings
    from .mirror import _read_meta

    if not getattr(_settings, 'IS_MIRROR', False):
        return JsonResponse({
            'ok': True,
            'is_mirror': False,
            'pushed_at': None,
            'age_seconds': 0,
        })

    meta = _read_meta()
    pushed = meta.get('pushed_at')

    try:
        pushed = float(pushed) if pushed is not None else None
    except (TypeError, ValueError):
        pushed = None

    age = (
        max(0, int(_time.time() - pushed))
        if pushed
        else None
    )

    return JsonResponse({
        'ok': True,
        'is_mirror': True,
        'pushed_at': pushed,
        'age_seconds': age,
    })


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



# ─── Signed-in — Operations Manual ────────────────────────────────────────────

@role_required('viewer', 'user', 'admin')
def manual_view(request):
    """Safe in-app operating manual for Viewer, User, and Admin roles."""
    from .manual_content import (
        ARCHITECTURE_STEPS,
        MANUAL_INTRO,
        OPERATING_WORKFLOWS,
        PAGE_GUIDES,
        ROLE_GUIDE,
        SECURITY_RULES,
        STATUS_GLOSSARY,
        TROUBLESHOOTING,
    )

    return render(request, 'monitor/manual.html', {
        'role': get_role(request.user),
        'manual_intro': MANUAL_INTRO,
        'role_guide': ROLE_GUIDE,
        'architecture_steps': ARCHITECTURE_STEPS,
        'status_glossary': STATUS_GLOSSARY,
        'page_guides': PAGE_GUIDES,
        'operating_workflows': OPERATING_WORKFLOWS,
        'security_rules': SECURITY_RULES,
        'troubleshooting': TROUBLESHOOTING,
    })


@role_required('viewer', 'user', 'admin')
def manual_pdf(request):
    """Download the safe operations manual as a version-matched PDF."""
    from django.http import HttpResponse

    from .manual_pdf import build_manual_pdf
    from .system_revision import sync_git_revision

    revision = sync_git_revision()
    version = revision.version if revision is not None else '1.1.1234'
    pdf_bytes = build_manual_pdf(version)

    response = HttpResponse(
        pdf_bytes,
        content_type='application/pdf',
    )
    response['Content-Disposition'] = (
        f'attachment; filename="SysMonitor_Manual_v{version}.pdf"'
    )
    return response


# ─── Public — About / Documentation ────────────────────────────────────────────
# Intentionally has NO @login_required / @role_required decorator — this page
# is meant to be publicly readable (purpose, how it works, contact, app
# download) without needing an account. It must never contain login
# credentials, access URLs, tokens, or any other secret.

def about_view(request):
    """
    Public About / Documentation page.

    KPI values shown here are read-only summaries calculated from existing
    SysMonitor data. No monitoring state is changed by visiting this page.
    """
    from decimal import Decimal

    from django.db.models import Sum
    from django.utils import timezone

    from .models import (
        ActivityLog,
        GeneratorFuelLog,
        NotificationLog,
        SensorReading,
    )

    # ------------------------------------------------------------------
    # Operational summary
    # ------------------------------------------------------------------
    device_count = Device.objects.filter(is_active=True).count()
    total_events = Event.objects.count()
    total_cycles = OutageCycle.objects.filter(is_complete=True).count()

    first_event = Event.objects.order_by('created_at').first()
    if first_event:
        days_monitoring = max(
            (timezone.now() - first_event.created_at).days,
            0,
        )
    else:
        days_monitoring = 0

    total_outage_seconds = (
        OutageCycle.objects
        .filter(is_complete=True)
        .aggregate(total=Sum('pdb_duration_sec'))
        .get('total')
        or 0
    )
    total_outage_hours = total_outage_seconds / 3600

    # ------------------------------------------------------------------
    # Platform activity
    # ------------------------------------------------------------------
    total_usage_seconds = (
        UserProfile.objects
        .aggregate(total=Sum('total_usage_seconds'))
        .get('total')
        or 0
    )
    total_usage_hours = total_usage_seconds / 3600

    notifications_sent = NotificationLog.objects.filter(
        status__iexact='SENT'
    ).count()

    activity_count = ActivityLog.objects.count()

    # ------------------------------------------------------------------
    # Environmental monitoring
    # ------------------------------------------------------------------
    sensor_count = SensorReading.objects.count()
    first_sensor = SensorReading.objects.order_by('recorded_at').first()
    last_sensor = SensorReading.objects.order_by('-recorded_at').first()

    if first_sensor and last_sensor:
        environmental_seconds = max(
            int(
                (
                    last_sensor.recorded_at -
                    first_sensor.recorded_at
                ).total_seconds()
            ),
            0,
        )
        environmental_days = environmental_seconds / 86400
    else:
        environmental_days = 0

    # ------------------------------------------------------------------
    # Generator fuel consumption
    #
    # Same register logic used by the Generator Fuel page:
    # for each generator, fuel consumed between two fuel entries is:
    #
    # previous AFTER reading - current BEFORE reading
    #
    # Negative intervals are ignored because they represent incomplete /
    # inconsistent historical intervals rather than valid consumption.
    # ------------------------------------------------------------------
    total_fuel_used = Decimal('0.00')

    for generator in ('Gen-01', 'Gen-02'):
        fuel_entries = list(
            GeneratorFuelLog.objects
            .filter(generator=generator)
            .order_by('reading_at', 'id')
        )

        previous = None

        for entry in fuel_entries:
            if previous is not None:
                used = (
                    previous.fuel_after_l -
                    entry.fuel_before_l
                )

                if used >= 0:
                    total_fuel_used += used

            previous = entry

    return render(request, 'monitor/about.html', {
        'device_count': device_count,
        'total_events': total_events,
        'total_cycles': total_cycles,
        'days_monitoring': days_monitoring,

        'total_outage_hours': total_outage_hours,
        'total_usage_hours': total_usage_hours,
        'notifications_sent': notifications_sent,
        'activity_count': activity_count,

        'environmental_days': environmental_days,
        'sensor_count': sensor_count,
        'total_fuel_used': total_fuel_used,

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

            confirmation = int(request.POST.get('alarm_confirmation_readings', '2').strip())
            if not 1 <= confirmation <= 10:
                raise ValueError('Confirmation must be between 1 and 10 readings.')
            if temperature_low is not None and temperature_high is not None and temperature_low >= temperature_high:
                raise ValueError('Temperature low must be below high.')
            if humidity_low is not None and humidity_high is not None and humidity_low >= humidity_high:
                raise ValueError('Humidity low must be below high.')
            if any(x is not None and not 0 <= x <= 100 for x in (humidity_low, humidity_high)):
                raise ValueError('Humidity setpoints must be between 0 and 100%.')

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
            config.alarm_confirmation_readings = confirmation
            config.save()

            log_activity(
                request.user,
                'COLO_SETPOINTS',
                (
                    'Colocation alarm setpoints updated: '
                    f'temperature={temperature_low}..{temperature_high} C; '
                    f'humidity={humidity_low}..{humidity_high} %; '
                    f'cooldown={cooldown} min.'
                )[:300],
                get_ip(request),
            )

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



def _common_log_range(request):
    """
    Resolve common log quick-range controls in Asia/Dhaka.

    Returns:
        range_key, start_dt, end_dt, human_label

    end_dt is exclusive.
    """
    import pytz
    from datetime import datetime as dt, timedelta

    bdt = pytz.timezone('Asia/Dhaka')
    now = dt.now(bdt)

    range_key = (request.GET.get('range') or 'this_month').strip()

    valid = {
        'this_week',
        'last_week',
        'this_month',
        'last_month',
        'this_year',
        'all',
    }

    if range_key not in valid:
        range_key = 'this_month'

    today = now.date()

    if range_key == 'all':
        return 'all', None, None, 'All Time'

    if range_key == 'this_week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
        label = 'This Week'

    elif range_key == 'last_week':
        this_week = today - timedelta(days=today.weekday())
        start_date = this_week - timedelta(days=7)
        end_date = this_week
        label = 'Last Week'

    elif range_key == 'this_month':
        start_date = today.replace(day=1)

        if start_date.month == 12:
            end_date = start_date.replace(
                year=start_date.year + 1,
                month=1,
                day=1,
            )
        else:
            end_date = start_date.replace(
                month=start_date.month + 1,
                day=1,
            )

        label = start_date.strftime('%B %Y')

    elif range_key == 'last_month':
        this_month = today.replace(day=1)
        end_date = this_month

        if this_month.month == 1:
            start_date = this_month.replace(
                year=this_month.year - 1,
                month=12,
            )
        else:
            start_date = this_month.replace(
                month=this_month.month - 1,
            )

        label = start_date.strftime('%B %Y')

    else:  # this_year
        start_date = today.replace(month=1, day=1)
        end_date = start_date.replace(year=start_date.year + 1)
        label = str(start_date.year)

    start_dt = bdt.localize(
        dt(
            start_date.year,
            start_date.month,
            start_date.day,
            0, 0, 0,
        )
    )

    end_dt = bdt.localize(
        dt(
            end_date.year,
            end_date.month,
            end_date.day,
            0, 0, 0,
        )
    )

    return range_key, start_dt, end_dt, label


def _export_table_response(title, range_label, headers, rows, fmt, filename_base):
    """Shared CSV/PDF exporter for simple log tables."""

    from django.http import HttpResponse

    fmt = (fmt or '').lower()

    if fmt == 'csv':
        import csv

        response = HttpResponse(
            content_type='text/csv; charset=utf-8'
        )
        response['Content-Disposition'] = (
            f'attachment; filename="{filename_base}.csv"'
        )

        response.write('\ufeff')

        writer = csv.writer(response)
        writer.writerow([title])
        writer.writerow(['Period', range_label])
        writer.writerow([])
        writer.writerow(headers)

        for row in rows:
            writer.writerow(row)

        return response

    if fmt == 'pdf':
        from io import BytesIO
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )

        buffer = BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=9 * mm,
            rightMargin=9 * mm,
            topMargin=9 * mm,
            bottomMargin=9 * mm,
            title=title,
        )

        styles = getSampleStyleSheet()

        story = [
            Paragraph(title, styles['Title']),
            Paragraph(f'Period: {range_label}', styles['Normal']),
            Spacer(1, 5 * mm),
        ]

        data = [headers] + [
            [str(value if value is not None else '') for value in row]
            for row in rows
        ]

        col_width = 270 * mm / max(len(headers), 1)

        table = Table(
            data,
            repeatRows=1,
            colWidths=[col_width] * len(headers),
        )

        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#D1D5DB')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), .3, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [
                colors.white,
                colors.HexColor('#F8FAFC'),
            ]),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))

        story.append(table)

        doc.build(story)

        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/pdf',
        )

        response['Content-Disposition'] = (
            f'attachment; filename="{filename_base}.pdf"'
        )

        buffer.close()

        return response

    return None


# ─── Event Log ────────────────────────────────────────────────────────────────

@role_required('user', 'admin', 'viewer')
def event_log(request):
    import pytz

    role = get_role(request.user)
    bdt = pytz.timezone('Asia/Dhaka')

    filter_device = request.GET.get('device', '')
    filter_level = request.GET.get('level', '')
    filter_date = request.GET.get('date', '')
    export_format = request.GET.get('export', '').strip().lower()

    filter_range, range_start, range_end, range_label = (
        _common_log_range(request)
    )

    events = Event.objects.all()

    if filter_device:
        events = events.filter(device__id=filter_device)

    if filter_level:
        events = events.filter(level=filter_level)

    if filter_date:
        # Existing exact-date filter remains supported.
        events = events.filter(created_at__date=filter_date)
        range_label = filter_date

    elif range_start and range_end:
        events = events.filter(
            created_at__gte=range_start,
            created_at__lt=range_end,
        )

    events = events.order_by('-created_at')

    range_count = events.count()

    if export_format in ('csv', 'pdf'):
        export_rows = []

        for e in events:
            export_rows.append([
                e.created_at.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M:%S %p'
                ),
                e.device.name if e.device else '',
                e.level,
                e.message,
            ])

        response = _export_table_response(
            'SysMonitor Event Log',
            range_label,
            ['Time', 'Device', 'Level', 'Message'],
            export_rows,
            export_format,
            f'event_log_{filter_range}',
        )

        if response:
            return response

    events = list(events[:200])

    outage_count = sum(
        1 for e in events if e.level == 'OUTAGE'
    )

    devices = Device.objects.all()

    context = {
        'events': events,
        'devices': devices,
        'filter_device': filter_device,
        'filter_level': filter_level,
        'filter_date': filter_date,
        'filter_range': filter_range,
        'range_label': range_label,
        'range_count': range_count,
        'role': role,
        'user': request.user,
        'level_choices': [
            'INFO',
            'NOTICE',
            'OUTAGE',
            'GEN-UP',
            'ATS',
            'NORMAL',
            'CRITICAL',
        ],
        'outage_count': outage_count,
    }

    return render(
        request,
        'monitor/event_log.html',
        context,
    )


# ─── Activity Log ─────────────────────────────────────────────────────────────

@role_required('admin')
def activity_log(request):
    import pytz

    bdt = pytz.timezone('Asia/Dhaka')

    filter_user = request.GET.get('user', '')
    filter_action = request.GET.get('action', '')
    filter_date = request.GET.get('date', '')
    export_format = request.GET.get('export', '').strip().lower()

    filter_range, range_start, range_end, range_label = (
        _common_log_range(request)
    )

    logs = ActivityLog.objects.all()

    if filter_user:
        logs = logs.filter(user__id=filter_user)

    if filter_action:
        logs = logs.filter(action=filter_action)

    if filter_date:
        logs = logs.filter(timestamp__date=filter_date)
        range_label = filter_date

    elif range_start and range_end:
        logs = logs.filter(
            timestamp__gte=range_start,
            timestamp__lt=range_end,
        )

    logs = logs.order_by('-timestamp')

    range_count = logs.count()

    if export_format in ('csv', 'pdf'):
        export_rows = []

        for item in logs:
            export_rows.append([
                item.timestamp.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M:%S %p'
                ),
                item.user.username if item.user else '',
                item.action,
                item.detail,
                item.ip_address or '',
            ])

        response = _export_table_response(
            'SysMonitor Activity Log',
            range_label,
            ['Time', 'User', 'Action', 'Detail', 'IP Address'],
            export_rows,
            export_format,
            f'activity_log_{filter_range}',
        )

        if response:
            return response

    logs = logs[:300]

    return render(
        request,
        'monitor/activity_log.html',
        {
            'logs': logs,
            'all_users': User.objects.all(),
            'filter_user': filter_user,
            'filter_action': filter_action,
            'filter_date': filter_date,
            'filter_range': filter_range,
            'range_label': range_label,
            'range_count': range_count,
            'action_choices': ActivityLog.ACTION_CHOICES,
            'role': get_role(request.user),
            'user': request.user,
        }
    )


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

    # Generator runtime totals for the currently selected report range.
    # These are calculated from the same segmented outage windows used
    # elsewhere in SysMonitor, so generator changeovers inside one outage
    # are attributed to the correct generator.
    gen1_total_secs = 0
    gen2_total_secs = 0
    cycle_gen_seconds = {}

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

        # Preserve explicit/manual historical generator assignments.
        # If no explicit assignment exists, split the outage at actual
        # GeneratorModeLog changeovers.
        if c.manual_generator:
            pieces = [
                (c.outage_start, cycle_end_for_gen, c.manual_generator)
            ]
        else:
            pieces = get_generator_segments(
                c,
                c.outage_start,
                cycle_end_for_gen,
                mode_logs_all
            )

        gen_secs = {
            'Gen-01': 0,
            'Gen-02': 0,
            'UNASSIGNED': 0,
        }

        for p_start, p_end, p_gen in pieces:
            secs = int((p_end - p_start).total_seconds())
            if secs <= 0:
                continue
            gen_secs[p_gen] = gen_secs.get(p_gen, 0) + secs

        gen1_total_secs += gen_secs.get('Gen-01', 0)
        gen2_total_secs += gen_secs.get('Gen-02', 0)

        cycle_gen_seconds[c.id] = {
            'Gen-01': gen_secs.get('Gen-01', 0),
            'Gen-02': gen_secs.get('Gen-02', 0),
        }

        distinct_gens = list(
            dict.fromkeys(
                p_gen
                for p_start, p_end, p_gen in pieces
                if (p_end - p_start).total_seconds() > 0
            )
        )
        gen = ' → '.join(distinct_gens) if distinct_gens else 'UNASSIGNED'

        cycle_rows.append({
            'date':         local_start.strftime('%Y-%m-%d'),
            'start':        local_start.strftime('%I:%M:%S %p'),
            'end':          local_end.strftime('%I:%M:%S %p') if local_end else '—',
            'duration':     dur_str,
            'generator':    gen,
            'gen1_runtime': fmt_duration(gen_secs.get('Gen-01', 0)),
            'gen2_runtime': fmt_duration(gen_secs.get('Gen-02', 0)),
            'gen1_secs':    gen_secs.get('Gen-01', 0),
            'gen2_secs':    gen_secs.get('Gen-02', 0),
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
                'month': m_label,
                'count': 0,
                'total_mins': 0,
                'gen1_secs': 0,
                'gen2_secs': 0,
                'max_mins': 0,
                'days': set(),
            }
            monthly_order.append(m_key)
        monthly_map[m_key]['count']      += 1
        monthly_map[m_key]['total_mins'] += mins
        monthly_map[m_key]['gen1_secs'] += cycle_gen_seconds.get(c.id, {}).get('Gen-01', 0)
        monthly_map[m_key]['gen2_secs'] += cycle_gen_seconds.get(c.id, {}).get('Gen-02', 0)
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
            'gen1_runtime': fmt_duration(m['gen1_secs']),
            'gen2_runtime': fmt_duration(m['gen2_secs']),
            'gen1_secs':    m['gen1_secs'],
            'gen2_secs':    m['gen2_secs'],
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
            'gen1_runtime':       fmt_duration(gen1_total_secs),
            'gen2_runtime':       fmt_duration(gen2_total_secs),
            'gen1_secs':          gen1_total_secs,
            'gen2_secs':          gen2_total_secs,
        },
    })



@login_required(login_url='login')
def report_export_csv(request):
    """Export the currently selected PDB outage report range as CSV."""
    import csv
    import json
    from django.http import HttpResponse

    report_response = api_report(request)
    if report_response.status_code != 200:
        return report_response

    data = json.loads(report_response.content.decode('utf-8'))

    from_str = request.GET.get('from', '').strip()
    to_str = request.GET.get('to', '').strip()

    if from_str or to_str:
        filename_range = f"{from_str or 'start'}_to_{to_str or 'end'}"
    else:
        filename_range = 'all_time'

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="pdb_outage_report_{filename_range}.csv"'
    )

    # UTF-8 BOM helps Excel display text cleanly.
    response.write('\ufeff')

    writer = csv.writer(response)

    writer.writerow(['SysMonitor - PDB Outage Report'])
    writer.writerow(['Selected Period', data.get('date_range', 'All time')])
    writer.writerow([
        'Total Outages',
        data.get('summary', {}).get('count', 0),
    ])
    writer.writerow([
        'Total Outage Duration',
        data.get('summary', {}).get('total_mins', 0),
        'minutes',
    ])
    writer.writerow([
        'GEN-1 Runtime',
        data.get('summary', {}).get('gen1_runtime', '0m 00s'),
    ])
    writer.writerow([
        'GEN-2 Runtime',
        data.get('summary', {}).get('gen2_runtime', '0m 00s'),
    ])
    writer.writerow([])

    writer.writerow([
        'Date',
        'Start Time',
        'PDB Restored',
        'PDB Outage Duration',
        'GEN-1 Runtime',
        'GEN-2 Runtime',
        'Generator',
        'Type',
    ])

    for row in data.get('cycles', []):
        writer.writerow([
            row.get('date', ''),
            row.get('start', ''),
            row.get('end', ''),
            row.get('duration', ''),
            row.get('gen1_runtime', ''),
            row.get('gen2_runtime', ''),
            row.get('generator', ''),
            row.get('cycle_type', ''),
        ])

    return response


@login_required(login_url='login')
def report_export_pdf(request):
    """Export the currently selected PDB outage report range as PDF."""
    import json
    from io import BytesIO
    from django.http import HttpResponse

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    report_response = api_report(request)
    if report_response.status_code != 200:
        return report_response

    data = json.loads(report_response.content.decode('utf-8'))
    summary = data.get('summary', {})

    from_str = request.GET.get('from', '').strip()
    to_str = request.GET.get('to', '').strip()

    if from_str or to_str:
        filename_range = f"{from_str or 'start'}_to_{to_str or 'end'}"
    else:
        filename_range = 'all_time'

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title='SysMonitor PDB Outage Report',
    )

    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph('SysMonitor - PDB Outage Report', styles['Title']))
    story.append(
        Paragraph(
            f"Selected Period: {data.get('date_range', 'All time')}",
            styles['Normal']
        )
    )
    story.append(Spacer(1, 5 * mm))

    summary_table = Table(
        [
            ['Total Outages', 'Total Duration', 'GEN-1 Runtime', 'GEN-2 Runtime'],
            [
                str(summary.get('count', 0)),
                f"{summary.get('total_mins', 0)} min",
                summary.get('gen1_runtime', '0m 00s'),
                summary.get('gen2_runtime', '0m 00s'),
            ],
        ],
        colWidths=[65 * mm, 65 * mm, 65 * mm, 65 * mm],
    )

    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E5E7EB')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))

    story.append(summary_table)
    story.append(Spacer(1, 6 * mm))

    rows = [[
        'Date',
        'Start',
        'PDB Restored',
        'Outage',
        'GEN-1 Runtime',
        'GEN-2 Runtime',
        'Generator',
        'Type',
    ]]

    for row in data.get('cycles', []):
        rows.append([
            row.get('date', ''),
            row.get('start', ''),
            row.get('end', ''),
            row.get('duration', ''),
            row.get('gen1_runtime', ''),
            row.get('gen2_runtime', ''),
            row.get('generator', ''),
            row.get('cycle_type', ''),
        ])

    table = Table(
        rows,
        repeatRows=1,
        colWidths=[
            27 * mm,
            31 * mm,
            31 * mm,
            30 * mm,
            30 * mm,
            30 * mm,
            35 * mm,
            25 * mm,
        ],
    )

    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#D1D5DB')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [
            colors.white,
            colors.HexColor('#F9FAFB'),
        ]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))

    story.append(table)
    doc.build(story)

    pdf_bytes = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="pdb_outage_report_{filename_range}.pdf"'
    )
    return response



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

    log_activity(
        request.user,
        'NOTIF_GATEWAY_EDIT',
        (
            f'Admin "{request.user.username}" updated '
            f'{channel} notification gateway; '
            f'enabled={gw.is_enabled}.'
        )[:300],
        get_ip(request),
    )

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

    log_activity(
        request.user,
        'NOTIF_GATEWAY_TEST',
        (
            f'Admin "{request.user.username}" sent a '
            f'{channel} gateway test to "{contact}"; '
            f'result={"success" if ok else "failed"}.'
        )[:300],
        get_ip(request),
    )

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

    log_activity(
        request.user,
        'NOTIF_RECIPIENT_ADD',
        (
            f'Admin "{request.user.username}" added notification recipient '
            f'"{r.name}" ({r.channel}: {r.contact}).'
        )[:300],
        get_ip(request),
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

        log_activity(
            request.user,
            'NOTIF_RECIPIENT_EDIT',
            (
                f'Admin "{request.user.username}" updated notification '
                f'recipient "{r.name}" ({r.channel}: {r.contact}).'
            )[:300],
            get_ip(request),
        )

        return JsonResponse({'ok': True})
    except NotificationRecipient.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Not found'})


@role_required('admin')
def notif_recipient_delete(request, rid):
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    recipient = NotificationRecipient.objects.filter(id=rid).first()

    if recipient is None:
        return JsonResponse({'ok': False, 'error': 'Not found'})

    name = recipient.name
    channel = recipient.channel
    contact = recipient.contact

    recipient.delete()

    log_activity(
        request.user,
        'NOTIF_RECIPIENT_DELETE',
        (
            f'Admin "{request.user.username}" deleted notification recipient '
            f'"{name}" ({channel}: {contact}).'
        )[:300],
        get_ip(request),
    )

    return JsonResponse({'ok': True})


@role_required('admin')
def notif_recipient_test(request, rid):
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    try:
        r  = NotificationRecipient.objects.get(id=rid)
        gw = NotificationGateway.objects.get(channel=r.channel, is_enabled=True)
        ok, err = send_test(r.channel, r.contact, gw)

        log_activity(
            request.user,
            'NOTIF_RECIPIENT_TEST',
            (
                f'Admin "{request.user.username}" sent test notification '
                f'to recipient "{r.name}" ({r.channel}: {r.contact}); '
                f'result={"success" if ok else "failed"}.'
            )[:300],
            get_ip(request),
        )

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

        log_activity(
            request.user,
            'NOTIF_RECIPIENT_TOGGLE',
            (
                f'Admin "{request.user.username}" '
                f'{"enabled" if r.is_active else "disabled"} '
                f'notification recipient "{r.name}" '
                f'({r.channel}: {r.contact}).'
            )[:300],
            get_ip(request),
        )

        return JsonResponse({'ok': True, 'is_active': r.is_active})
    except NotificationRecipient.DoesNotExist:
        return JsonResponse({'ok': False})


@role_required('admin')
def notif_log(request):
    import pytz

    bdt = pytz.timezone('Asia/Dhaka')

    export_format = request.GET.get('export', '').strip().lower()

    filter_range, range_start, range_end, range_label = (
        _common_log_range(request)
    )

    logs = NotificationLog.objects.all().order_by('-sent_at')

    if range_start and range_end:
        logs = logs.filter(
            sent_at__gte=range_start,
            sent_at__lt=range_end,
        )

    range_count = logs.count()

    if export_format in ('csv', 'pdf'):
        rows = []

        for l in logs:
            rows.append([
                l.sent_at.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M:%S %p'
                ),
                l.event_type,
                l.channel,
                l.recipient,
                l.status,
                l.error or '',
            ])

        response = _export_table_response(
            'SysMonitor Notification Log',
            range_label,
            [
                'Time',
                'Event',
                'Channel',
                'Recipient',
                'Status',
                'Error',
            ],
            rows,
            export_format,
            f'notification_log_{filter_range}',
        )

        if response:
            return response

    data = [{
        'sent_at': l.sent_at.astimezone(bdt).strftime(
            '%d/%m %I:%M:%S %p'
        ),
        'event_type': l.event_type,
        'channel': l.channel,
        'recipient': l.recipient,
        'status': l.status,
        'error': l.error,
    } for l in logs[:500]]

    return JsonResponse({
        'logs': data,
        'filter_range': filter_range,
        'range_label': range_label,
        'range_count': range_count,
    })


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

    log_activity(
        request.user,
        'MESSAGE_TEMPLATE_EDIT',
        (
            f'Admin "{request.user.username}" updated '
            f'"{valid_types[event_type]}" notification message template.'
        )[:300],
        ip=get_ip(request),
    )
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

    log_activity(
        request.user,
        'MONTHLY_REPORT_SEND',
        (
            f'Admin "{request.user.username}" manually sent monthly '
            f'loadshedding report for {summary["label"]} to '
            f'{sent_count} recipient(s); failed={len(failed)}.'
        )[:300],
        ip=get_ip(request),
    )

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
            'GENERATOR_FUEL_ADD',
            (
                f'User "{request.user.username}" added generator fuel '
                f'record: {generator}, before={fuel_before}L, '
                f'after={fuel_after}L.'
            )[:300],
            ip=get_ip(request),
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
    # FUEL ENTRY HISTORY RANGE / EXPORT
    # --------------------------------------------------------
    export_format = request.GET.get('export', '').strip().lower()

    filter_range, range_start, range_end, range_label = (
        _common_log_range(request)
    )

    history_records = records

    if range_start and range_end:
        history_records = [
            row
            for row in records
            if range_start <= row['obj'].reading_at < range_end
        ]

    range_count = len(history_records)

    if export_format in ('csv', 'pdf'):
        export_rows = []

        for row in history_records:
            obj = row['obj']

            export_rows.append([
                obj.reading_at.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M:%S %p'
                ),
                obj.generator,
                f'{obj.fuel_before_l:.2f}',
                f'{obj.fuel_after_l:.2f}',
                f'{row["fuel_loaded"]:.2f}',
                obj.added_by or '',
                obj.note or '',
            ])

        response = _export_table_response(
            'SysMonitor Generator Fuel Entry History',
            range_label,
            [
                'Date & Time',
                'Generator',
                'Before Fuel (L)',
                'After Fuel (L)',
                'Fuel Loaded (L)',
                'Added By',
                'Note',
            ],
            export_rows,
            export_format,
            f'generator_fuel_history_{filter_range}',
        )

        if response:
            return response

    records = history_records

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
            'filter_range': filter_range,
            'range_label': range_label,
            'range_count': range_count,
            'monthly': monthly,
            'till_date': till_date,
            'month_value': month_value,
            'month_label': month_label,
            'report_start': report_start,
            'now_bdt': now_bdt,
        }
    )


@role_required('user', 'admin')
def generator_cycle_audit(request):
    """Manual/audited generator cycle entry and audit history."""
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

    export_format = request.GET.get('export', '').strip().lower()

    filter_range, range_start, range_end, range_label = (
        _common_log_range(request)
    )

    entries = GeneratorModeLog.objects.all()

    if range_start and range_end:
        entries = entries.filter(
            switched_at__gte=range_start,
            switched_at__lt=range_end,
        )

    entries = entries.order_by('-switched_at', '-id')

    range_count = entries.count()

    if export_format in ('csv', 'pdf'):
        rows = []

        for e in entries:
            rows.append([
                e.id,
                e.switched_at.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M:%S %p'
                ),
                e.generator,
                e.added_by or '',
                e.note or '',
            ])

        response = _export_table_response(
            'SysMonitor Generator Mode Log',
            range_label,
            ['ID', 'Switch Time', 'Generator', 'Added By', 'Note'],
            rows,
            export_format,
            f'generator_mode_log_{filter_range}',
        )

        if response:
            return response

    entries_display = [{
        'id': e.id,
        'generator': e.generator,
        'switched_at': e.switched_at.astimezone(bdt).strftime(
            '%d/%m/%Y %I:%M:%S %p'
        ),
        'switched_date_raw': e.switched_at.astimezone(bdt).strftime(
            '%Y-%m-%d'
        ),
        'switched_time_raw': e.switched_at.astimezone(bdt).strftime(
            '%H:%M'
        ),
        'note': e.note,
        'added_by': e.added_by,
    } for e in entries]

    return render(
        request,
        'monitor/generator_log.html',
        {
            'entries': entries_display,
            'filter_range': filter_range,
            'range_label': range_label,
            'range_count': range_count,
            'role': get_role(request.user),
            'user': request.user,
        }
    )


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

    log_activity(
        request.user,
        'MAINT_START',
        (
            f'Maintenance mode started by "{request.user.username}" '
            f'for {minutes} minute(s); reason="{reason or "not provided"}".'
        )[:300],
        get_ip(request),
    )

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

    log_activity(
        request.user,
        'MAINT_STOP',
        f'Maintenance mode stopped by "{request.user.username}".',
        get_ip(request),
    )

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

    changed_parts = [
        'name/designation/contact profile fields saved'
    ]
    if 'profile_picture' in request.FILES:
        changed_parts.append('profile picture updated')
    if new_email and new_email != user.email:
        changed_parts.append('email change requested')
    if new_mobile and new_mobile != profile.mobile_number:
        changed_parts.append('mobile change requested')

    log_activity(
        user,
        'PROFILE_UPDATE',
        (
            f'User "{user.username}" updated own profile: '
            + '; '.join(changed_parts)
            + '.'
        )[:300],
        get_ip(request),
    )

    if new_email and new_email != user.email:
        log_activity(
            user,
            'PROFILE_CHANGE_REQ',
            f'User "{user.username}" requested an email address change.',
            get_ip(request),
        )

    if new_mobile and new_mobile != profile.mobile_number:
        log_activity(
            user,
            'PROFILE_CHANGE_REQ',
            f'User "{user.username}" requested a mobile number change.',
            get_ip(request),
        )

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

    log_activity(
        request.user,
        'PROFILE_CHANGE_APPROVE',
        (
            f'Admin "{request.user.username}" approved {req.field} change '
            f'for user "{user.username}".'
        ),
        get_ip(request),
    )

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

    log_activity(
        request.user,
        'PROFILE_CHANGE_REJECT',
        (
            f'Admin "{request.user.username}" rejected {req.field} change '
            f'for user "{req.user.username}".'
        ),
        get_ip(request),
    )

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
    """Fetch/filter/export sysmonitor-ping journal output."""
    import subprocess
    import pytz

    bdt = pytz.timezone('Asia/Dhaka')

    export_format = request.GET.get('export', '').strip().lower()

    filter_range, range_start, range_end, range_label = (
        _common_log_range(request)
    )

    raw_lines = request.GET.get('lines', '200')

    try:
        lines = int(raw_lines)
    except (TypeError, ValueError):
        lines = 200

    lines = max(10, min(lines, 5000))

    cmd = [
        'sudo',
        'journalctl',
        '-u',
        'sysmonitor-ping',
        '--no-pager',
        '--output=short-iso',
    ]

    if range_start:
        cmd.extend([
            '--since',
            range_start.strftime('%Y-%m-%d %H:%M:%S'),
        ])

    if range_end:
        cmd.extend([
            '--until',
            range_end.strftime('%Y-%m-%d %H:%M:%S'),
        ])

    # All Time can be large, but normal on-screen loading remains capped.
    if export_format not in ('csv', 'pdf'):
        cmd.extend(['-n', str(lines)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )

        if result.returncode != 0:
            return JsonResponse({
                'ok': False,
                'error': result.stderr.strip() or 'journalctl failed',
            })

        log_text = result.stdout or ''

        loaded_count = len([
            line
            for line in log_text.splitlines()
            if line.strip()
        ])

        if export_format in ('csv', 'pdf'):
            journal_lines = [
                line
                for line in log_text.splitlines()
                if line.strip()
            ]

            rows = [[line] for line in journal_lines]

            response = _export_table_response(
                'SysMonitor sysmonitor-ping Journal Log',
                range_label,
                ['Journal Entry'],
                rows,
                export_format,
                f'sysmonitor_ping_journal_{filter_range}',
            )

            if response:
                return response

        return JsonResponse({
            'ok': True,
            'log': log_text,
            'filter_range': filter_range,
            'range_label': range_label,
            'loaded_count': loaded_count,
        })

    except Exception as e:
        return JsonResponse({
            'ok': False,
            'error': str(e),
        })


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


@role_required('user', 'admin')
@transaction.atomic
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

    if generator not in ('Gen-01', 'Gen-02'):
        return JsonResponse({
            'ok': False,
            'error': 'Generator is required. Please select Gen-01 or Gen-02.'
        })

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

    existing = OutageCycle.objects.filter(
        is_manual=True,
        outage_start=start_dt,
        cycle_end=end_dt,
    ).first()

    if existing:
        return JsonResponse({
            'ok': False,
            'duplicate': True,
            'cycle_id': existing.id,
            'error': (
                f'Manual cycle ID:{existing.id} already exists '
                'with the same start and end time.'
            ),
        })

    from django.db import IntegrityError

    try:
        # Nested atomic block gives this INSERT its own savepoint. If two
        # submissions race, the unique constraint can fail safely without
        # breaking the outer request transaction.
        with transaction.atomic():
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
    except IntegrityError:
        existing = OutageCycle.objects.filter(
            is_manual=True,
            outage_start=start_dt,
            cycle_end=end_dt,
        ).first()
        return JsonResponse({
            'ok': False,
            'duplicate': True,
            'cycle_id': existing.id if existing else None,
            'error': (
                f'Manual cycle ID:{existing.id} already exists '
                'with the same start and end time.'
                if existing
                else 'This manual cycle already exists.'
            ),
        })

    detail = (
        f'Manual cycle added: ID:{cycle.id}, '
        f'{generator}, '
        f'{start_dt.strftime("%d/%m/%Y %I:%M:%S %p")} -> '
        f'{end_dt.strftime("%d/%m/%Y %I:%M:%S %p")}, '
        f'by={request.user.username}'
    )

    log_activity(
        request.user,
        'CYCLE_MANUAL_ADD',
        detail,
        get_ip(request)
    )

    Event.objects.create(
        device=None,
        level='NOTICE',
        message=detail
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



@role_required('user', 'admin')
def generator_cycle_audit_data(request):
    """Manual-cycle summary for the Cycle Audit page."""
    if request.method != 'GET':
        return JsonResponse({'ok': False, 'error': 'GET required'})

    import pytz
    bdt = pytz.timezone('Asia/Dhaka')

    export_format = request.GET.get('export', '').strip().lower()

    filter_range, range_start, range_end, range_label = (
        _common_log_range(request)
    )

    raw_limit = (request.GET.get('limit') or '10').strip().lower()

    cycles = (
        OutageCycle.objects
        .filter(is_manual=True)
        .order_by('-outage_start', '-id')
    )

    if range_start and range_end:
        cycles = cycles.filter(
            outage_start__gte=range_start,
            outage_start__lt=range_end,
        )

    range_count = cycles.count()

    if export_format in ('csv', 'pdf'):
        rows = []

        for c in cycles:
            end_dt = c.cycle_end or c.pdb_restored
            seconds = c.pdb_duration_sec or 0

            h, rem = divmod(seconds, 3600)
            m, s = divmod(rem, 60)

            if h:
                duration = f'{h}h {m:02d}m'
            elif m:
                duration = f'{m}m {s:02d}s'
            else:
                duration = f'{s}s'

            rows.append([
                c.id,
                c.outage_start.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M %p'
                ) if c.outage_start else '',
                end_dt.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M %p'
                ) if end_dt else '',
                duration,
                c.manual_generator or '',
                c.added_by or '',
                c.alarm_reason or '',
            ])

        response = _export_table_response(
            'SysMonitor Generator Cycle Audit',
            range_label,
            [
                'Cycle ID',
                'Start',
                'End',
                'Duration',
                'Generator',
                'Added By',
                'Note',
            ],
            rows,
            export_format,
            f'generator_cycle_audit_{filter_range}',
        )

        if response:
            return response

    # Keep existing row-limit selector working.
    if raw_limit != 'all':
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 10

        if limit not in (5, 10, 50, 100):
            limit = 10

        cycles = cycles[:limit]

    data = []

    for c in cycles:
        start_dt = c.outage_start
        end_dt = c.cycle_end or c.pdb_restored
        seconds = c.pdb_duration_sec or 0

        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)

        if h:
            duration = f'{h}h {m:02d}m'
        elif m:
            duration = f'{m}m {s:02d}s'
        else:
            duration = f'{s}s'

        data.append({
            'id': c.id,
            'start': (
                start_dt.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M %p'
                )
                if start_dt else '—'
            ),
            'end': (
                end_dt.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M %p'
                )
                if end_dt else '—'
            ),
            'start_date': (
                start_dt.astimezone(bdt).strftime('%Y-%m-%d')
                if start_dt else ''
            ),
            'start_time': (
                start_dt.astimezone(bdt).strftime('%H:%M')
                if start_dt else ''
            ),
            'end_date': (
                end_dt.astimezone(bdt).strftime('%Y-%m-%d')
                if end_dt else ''
            ),
            'end_time': (
                end_dt.astimezone(bdt).strftime('%H:%M')
                if end_dt else ''
            ),
            'duration': duration,
            'generator': c.manual_generator,
            'added_by': c.added_by or '—',
            'note': c.alarm_reason or '',
            'created_at': (
                c.created_at.astimezone(bdt).strftime(
                    '%d/%m/%Y %I:%M %p'
                )
                if c.created_at else '—'
            ),
        })

    return JsonResponse({
        'ok': True,
        'cycles': data,
        'role': get_role(request.user),
        'filter_range': filter_range,
        'range_label': range_label,
        'range_count': range_count,
    })


@role_required('admin')
@transaction.atomic
def generator_cycle_audit_edit(request, cid):
    """Admin-only edit of a manual/audited cycle."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})

    import json as _json
    import pytz
    from datetime import datetime as dt_class

    try:
        cycle = OutageCycle.objects.get(id=cid, is_manual=True)
    except OutageCycle.DoesNotExist:
        return JsonResponse({
            'ok': False,
            'error': 'Manual cycle not found.'
        })

    try:
        d = _json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid request body'})

    start_date = (d.get('start_date') or '').strip()
    start_time = (d.get('start_time') or '').strip()
    end_date = (d.get('end_date') or '').strip()
    end_time = (d.get('end_time') or '').strip()
    generator = (d.get('generator') or '').strip()
    note = (d.get('note') or '').strip()

    if not (start_date and start_time and end_date and end_time):
        return JsonResponse({
            'ok': False,
            'error': 'Start and end date/time are required.'
        })

    if generator not in ('Gen-01', 'Gen-02'):
        return JsonResponse({
            'ok': False,
            'error': 'Generator is required. Please select Gen-01 or Gen-02.'
        })

    bdt = pytz.timezone('Asia/Dhaka')

    try:
        naive_start = dt_class.strptime(
            f'{start_date} {start_time}',
            '%Y-%m-%d %H:%M'
        )
        naive_end = dt_class.strptime(
            f'{end_date} {end_time}',
            '%Y-%m-%d %H:%M'
        )
    except ValueError:
        return JsonResponse({
            'ok': False,
            'error': 'Invalid date/time format.'
        })

    start_dt = bdt.localize(naive_start)
    end_dt = bdt.localize(naive_end)

    if end_dt <= start_dt:
        return JsonResponse({
            'ok': False,
            'error': 'End time must be after start time.'
        })

    duplicate = (
        OutageCycle.objects
        .filter(
            is_manual=True,
            outage_start=start_dt,
            cycle_end=end_dt,
        )
        .exclude(id=cycle.id)
        .first()
    )

    if duplicate:
        return JsonResponse({
            'ok': False,
            'duplicate': True,
            'cycle_id': duplicate.id,
            'error': (
                f'Manual cycle ID:{duplicate.id} already exists '
                'with the same start and end time.'
            ),
        })

    old_generator = cycle.manual_generator
    old_start = cycle.outage_start
    old_end = cycle.cycle_end or cycle.pdb_restored

    cycle.outage_start = start_dt
    cycle.pdb_restored = end_dt
    cycle.cycle_end = end_dt
    cycle.pdb_duration_sec = int((end_dt - start_dt).total_seconds())
    cycle.cycle_type = 'MANUAL'
    cycle.is_complete = True
    cycle.is_manual = True
    cycle.manual_generator = generator
    cycle.alarm_reason = note

    from django.db import IntegrityError

    try:
        with transaction.atomic():
            cycle.save()
    except IntegrityError:
        duplicate = (
            OutageCycle.objects
            .filter(
                is_manual=True,
                outage_start=start_dt,
                cycle_end=end_dt,
            )
            .exclude(id=cycle.id)
            .first()
        )
        return JsonResponse({
            'ok': False,
            'duplicate': True,
            'cycle_id': duplicate.id if duplicate else None,
            'error': (
                f'Manual cycle ID:{duplicate.id} already exists '
                'with the same start and end time.'
                if duplicate
                else 'An identical manual cycle already exists.'
            ),
        })

    detail = (
        f'Manual cycle edited: ID:{cycle.id}, '
        f'{old_generator} {old_start} -> {old_end}; '
        f'now {generator} {start_dt} -> {end_dt}; '
        f'by={request.user.username}'
    )

    log_activity(
        request.user,
        'CYCLE_MANUAL_EDIT',
        detail[:300],
        get_ip(request)
    )

    Event.objects.create(
        device=None,
        level='NOTICE',
        message=detail
    )

    return JsonResponse({
        'ok': True,
        'cycle_id': cycle.id,
    })


@role_required('admin')
@transaction.atomic
def generator_cycle_audit_delete(request, cid):
    """Admin-only deletion of a manual/audited cycle."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})

    try:
        cycle = OutageCycle.objects.get(id=cid, is_manual=True)
    except OutageCycle.DoesNotExist:
        return JsonResponse({
            'ok': False,
            'error': 'Manual cycle not found.'
        })

    detail = (
        f'Manual cycle deleted: ID:{cycle.id}, '
        f'{cycle.manual_generator}, '
        f'{cycle.outage_start} -> {cycle.cycle_end or cycle.pdb_restored}, '
        f'originally added by={cycle.added_by or "unknown"}, '
        f'deleted by={request.user.username}'
    )

    cycle.delete()

    log_activity(
        request.user,
        'CYCLE_MANUAL_DELETE',
        detail[:300],
        get_ip(request)
    )

    Event.objects.create(
        device=None,
        level='NOTICE',
        message=detail
    )

    return JsonResponse({'ok': True})


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
    """
    Restart sysmonitor-ping on the MASTER.

    Any user whose effective SysMonitor role is ``admin`` may perform
    this action, but must confirm using their own current Django password.

    No Linux/server password is accepted by the web application.
    """

    if settings.IS_MIRROR:
        return JsonResponse({
            'ok': False,
            'error': (
                'Ping service restart is available on the MASTER '
                'SysMonitor only.'
            ),
        }, status=403)

    if request.method != 'POST':
        return JsonResponse({
            'ok': False,
            'error': 'POST required.',
        }, status=405)

    import json as _json
    import subprocess
    import time

    from datetime import datetime, timezone as dt_timezone
    from django.contrib.auth.models import User as AuthUser
    from django.contrib.auth.hashers import (
        check_password as check_pw,
    )

    try:
        data = _json.loads(
            request.body.decode('utf-8')
        )
    except Exception:
        return JsonResponse({
            'ok': False,
            'error': 'Invalid request.',
        }, status=400)

    password = (
        data.get('password')
        or ''
    ).strip()

    confirmed = bool(
        data.get('confirmed')
    )

    if not confirmed:
        return JsonResponse({
            'ok': False,
            'error': (
                'You must confirm that outage monitoring will '
                'briefly be interrupted.'
            ),
        }, status=400)

    if not password:
        return JsonResponse({
            'ok': False,
            'error': (
                'Your current SysMonitor password is required.'
            ),
        }, status=400)

    try:
        fresh_user = AuthUser.objects.get(
            pk=request.user.pk
        )
    except AuthUser.DoesNotExist:
        return JsonResponse({
            'ok': False,
            'error': 'User not found.',
        }, status=403)

    # role_required('admin') already guarantees effective role=admin.
    # Here we additionally verify the current user's own password.
    if not check_pw(
        password,
        fresh_user.password,
    ):
        log_activity(
            request.user,
            'SERVICE_RESTART_DENIED',
            (
                'Failed password confirmation for ping restart '
                f'(user: {request.user.username})'
            ),
            get_ip(request),
        )

        return JsonResponse({
            'ok': False,
            'error': (
                f'Incorrect password for user '
                f'"{request.user.username}". Action denied.'
            ),
        }, status=403)

    restart_started = datetime.now(
        dt_timezone.utc
    )

    try:
        result = subprocess.run(
            [
                'sudo',
                '-n',
                '/bin/systemctl',
                'restart',
                'sysmonitor-ping',
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return JsonResponse({
            'ok': False,
            'error': (
                'Unable to restart sysmonitor-ping: '
                f'{exc}'
            ),
        }, status=500)

    if result.returncode != 0:
        return JsonResponse({
            'ok': False,
            'error': (
                result.stderr.strip()
                or 'systemctl restart failed.'
            ),
        }, status=500)

    # Allow ping_monitor startup confirmation to complete so the browser
    # can show meaningful initialization output, not just systemctl success.
    state = 'unknown'
    startup_complete = False

    for _ in range(14):
        try:
            active = subprocess.run(
                [
                    '/bin/systemctl',
                    'is-active',
                    'sysmonitor-ping.service',
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )

            state = (
                active.stdout.strip()
                or 'unknown'
            )

        except Exception:
            state = 'unknown'

        try:
            since = restart_started.strftime(
                '%Y-%m-%d %H:%M:%S UTC'
            )

            journal_probe = subprocess.run(
                [
                    'sudo',
                    '-n',
                    '/usr/bin/journalctl',
                    '-u',
                    'sysmonitor-ping.service',
                    '--since',
                    since,
                    '--no-pager',
                    '-o',
                    'short-iso',
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if (
                'Monitoring started.'
                in journal_probe.stdout
            ):
                startup_complete = True
                break

        except Exception:
            pass

        time.sleep(1)

    try:
        since = restart_started.strftime(
            '%Y-%m-%d %H:%M:%S UTC'
        )

        journal = subprocess.run(
            [
                'sudo',
                '-n',
                '/usr/bin/journalctl',
                '-u',
                'sysmonitor-ping.service',
                '--since',
                since,
                '--no-pager',
                '-o',
                'short-iso',
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )

        journal_text = (
            journal.stdout.strip()
            if journal.returncode == 0
            else journal.stderr.strip()
        )

    except Exception as exc:
        journal_text = (
            'Journal output unavailable: '
            f'{exc}'
        )

    healthy = (
        state == 'active'
    )

    log_activity(
        request.user,
        'SERVICE_RESTART',
        (
            'Admin restarted sysmonitor-ping service '
            f'(user={request.user.username}, '
            f'state={state}, '
            f'startup_complete={startup_complete}, '
            'password confirmed)'
        ),
        get_ip(request),
    )

    return JsonResponse({
        'ok': healthy,
        'service': 'sysmonitor-ping.service',
        'action': 'restarted',
        'state': state,
        'startup_complete': startup_complete,
        'started_by': request.user.username,
        'journal': journal_text,
        'message': (
            'Ping service restarted successfully.'
            if healthy
            else 'Ping restart completed but service is not active.'
        ),
    })


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

    # Tuya shadow properties include their real device update time in epoch
    # milliseconds. recorded_at is only when SysMonitor performed the poll.
    source_times = []

    for item in latest.raw_status or []:
        if not isinstance(item, dict):
            continue

        try:
            raw_time = item.get('time')

            if raw_time:
                source_times.append(
                    int(raw_time) / 1000.0
                )

        except (TypeError, ValueError):
            pass

    source_updated_at = None

    if source_times:
        from datetime import datetime, timezone as dt_timezone

        source_dt = datetime.fromtimestamp(
            max(source_times),
            tz=dt_timezone.utc,
        )

        source_updated_at = source_dt.astimezone(
            bdt
        ).strftime(
            '%d/%m/%Y %I:%M:%S %p'
        )

    return JsonResponse({
        'has_data': True,
        'configured': tuya_configured(),
        'device_name': latest.device_name,
        'temperature_c': (
            latest.temperature_c
            if latest.is_online
            else None
        ),
        'humidity_pct': (
            latest.humidity_pct
            if latest.is_online
            else None
        ),
        'battery_pct': (
            latest.battery_pct
            if latest.is_online
            else None
        ),
        'battery_state': (
            latest.battery_state
            if latest.is_online
            else ''
        ),
        'battery_display': (
            (
                f'{latest.battery_pct:.0f}%'
                if latest.battery_pct is not None
                else latest.battery_state
            )
            if latest.is_online
            else 'Out of battery / unavailable'
        ),
        'is_online': latest.is_online,
        'recorded_at': latest.recorded_at.astimezone(bdt).strftime(
            '%d/%m/%Y %I:%M:%S %p'
        ),
        'source_updated_at': source_updated_at,
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

# ─── NOC CCTV ─────────────────────────────────────────────────────────────────

def _cctv_clean_host(value):
    """
    CCTV host fields should normally contain only an IP/hostname.

    Be tolerant if an admin pastes http:// or https:// and strip scheme/path
    rather than generating a broken URL.
    """
    from urllib.parse import urlsplit

    value = (value or '').strip()

    if not value:
        return ''

    if '://' in value:
        parts = urlsplit(value)
        return parts.hostname or ''

    return value.split('/')[0].split(':')[0].strip()


def _cctv_web_url(nvr, remote=False):
    host = _cctv_clean_host(
        nvr.remote_host if remote else nvr.local_host
    )

    if not host:
        return ''

    scheme = nvr.web_scheme or 'https'
    port = nvr.web_port

    default_port = (
        (scheme == 'https' and port == 443) or
        (scheme == 'http' and port == 80)
    )

    port_part = '' if default_port else f':{port}'

    return f'{scheme}://{host}{port_part}/'


def _cctv_rtsp_url(camera, remote=False):
    host = _cctv_clean_host(
        camera.nvr.remote_host if remote else camera.nvr.local_host
    )

    if not host:
        return ''

    return (
        f'rtsp://{host}:{camera.nvr.rtsp_port}'
        f'/cam/realmonitor?channel={camera.channel}'
        f'&subtype={camera.stream_type}'
    )


@role_required('user', 'admin', 'viewer')
def cctv_view(request):
    """
    NOC CCTV live view.

    Django serves only configuration/layout. Video is delivered by the local
    MediaMTX WebRTC gateway and is never stored by SysMonitor.
    """
    from django.conf import settings

    from .cctv_gateway import (
        credential_info,
        player_url,
    )
    from .models import CCTVCamera

    is_remote = bool(settings.IS_MIRROR)

    rows = []

    cameras = (
        CCTVCamera.objects
        .select_related('nvr')
        .filter(
            enabled=True,
            nvr__enabled=True,
        )
        .order_by(
            'display_order',
            'name',
        )
    )

    for camera in cameras:
        secret = credential_info(
            camera.nvr_id
        )

        live_url = ''

        if is_remote:
            # Secure REMOTE HLS.
            # All configured cameras use the authenticated tunnel.
            if camera.id in (1, 2, 3, 4, 5):
                live_url = (
                    f'/sysmonitor/cctv-stream/'
                    f'camera-{camera.id}/'
                )
        elif secret['ready']:
            live_url = player_url(camera)

        rows.append({
            'obj': camera,
            'live_url': live_url,
            'credentials_ready': secret['ready'],
            'web_url': _cctv_web_url(
                camera.nvr,
                remote=is_remote,
            ),
            'rtsp_url': _cctv_rtsp_url(
                camera,
                remote=is_remote,
            ),
            'route_type': (
                'REMOTE'
                if is_remote
                else 'LOCAL'
            ),
        })

    return render(
        request,
        'monitor/cctv.html',
        {
            'cameras': rows,
            'role': get_role(request.user),
            'is_remote_cctv': is_remote,
        },
    )



def cctv_stream_auth(request):
    """
    Authorization endpoint used internally by REMOTE nginx auth_request.

    It does not serve video. It only confirms that the request carries a
    valid SysMonitor session belonging to a CCTV-authorized user.
    """
    from django.conf import settings
    from django.http import HttpResponse

    if not getattr(settings, 'IS_MIRROR', False):
        return HttpResponse(status=404)

    if not request.user.is_authenticated:
        return HttpResponse(status=401)

    if not request.user.is_active:
        return HttpResponse(status=403)

    if get_role(request.user) not in (
        'viewer',
        'user',
        'admin',
    ):
        return HttpResponse(status=403)

    response = HttpResponse(status=204)
    response['Cache-Control'] = 'no-store'
    return response



@role_required('user', 'admin', 'viewer')
def cctv_live_status(request):
    """
    Lightweight MASTER status for the local CCTV page.

    Returns cumulative network counters. The browser calculates current
    Mbps from the difference between samples, avoiding server-side delays.
    """
    import os

    interface = 'enp2s0'

    rx_bytes = 0
    tx_bytes = 0

    try:
        with open('/proc/net/dev', 'r') as fh:
            for line in fh:
                if ':' not in line:
                    continue

                name, values = line.split(':', 1)

                if name.strip() != interface:
                    continue

                fields = values.split()

                rx_bytes = int(fields[0])
                tx_bytes = int(fields[8])
                break
    except (OSError, ValueError, IndexError):
        pass

    mem_total_kb = 0
    mem_available_kb = 0

    try:
        with open('/proc/meminfo', 'r') as fh:
            for line in fh:
                if line.startswith('MemTotal:'):
                    mem_total_kb = int(
                        line.split()[1]
                    )
                elif line.startswith('MemAvailable:'):
                    mem_available_kb = int(
                        line.split()[1]
                    )
    except (OSError, ValueError, IndexError):
        pass

    ram_percent = 0.0

    if mem_total_kb > 0:
        ram_used_kb = (
            mem_total_kb -
            mem_available_kb
        )

        ram_percent = (
            ram_used_kb /
            mem_total_kb *
            100.0
        )

    try:
        load_1m = os.getloadavg()[0]
    except (OSError, AttributeError):
        load_1m = 0.0

    gateway_rss_kb = 0

    try:
        for entry in os.scandir('/proc'):
            if not entry.name.isdigit():
                continue

            try:
                comm_path = (
                    f'/proc/{entry.name}/comm'
                )

                with open(comm_path, 'r') as fh:
                    process_name = fh.read().strip()

                if process_name != 'mediamtx':
                    continue

                status_path = (
                    f'/proc/{entry.name}/status'
                )

                with open(status_path, 'r') as fh:
                    for line in fh:
                        if line.startswith('VmRSS:'):
                            gateway_rss_kb += int(
                                line.split()[1]
                            )
                            break

            except (
                OSError,
                ValueError,
                IndexError,
                PermissionError,
            ):
                continue
    except OSError:
        pass

    return JsonResponse({
        'ok': True,
        'interface': interface,
        'rx_bytes': rx_bytes,
        'tx_bytes': tx_bytes,
        'ram_percent': round(
            ram_percent,
            1,
        ),
        'load_1m': round(
            load_1m,
            2,
        ),
        'gateway_mb': round(
            gateway_rss_kb / 1024.0,
            1,
        ),
    })


@role_required('admin')
def cctv_setup(request):
    from .cctv_gateway import credential_info
    from .models import CCTVNVR, CCTVCamera

    edit_nvr = None
    edit_camera = None

    edit_nvr_id = request.GET.get('edit_nvr')
    edit_camera_id = request.GET.get('edit_camera')

    if edit_nvr_id:
        edit_nvr = CCTVNVR.objects.filter(
            id=edit_nvr_id
        ).first()

    if edit_camera_id:
        edit_camera = (
            CCTVCamera.objects
            .select_related('nvr')
            .filter(id=edit_camera_id)
            .first()
        )

    nvrs = list(
        CCTVNVR.objects
        .all()
        .order_by('name')
    )

    for nvr in nvrs:
        info = credential_info(nvr.id)

        nvr.gateway_username = (
            info['username']
        )

        nvr.gateway_credentials_ready = (
            info['ready']
        )

    edit_secret = {
        'username': '',
        'has_password': False,
        'ready': False,
    }

    if edit_nvr:
        edit_secret = credential_info(
            edit_nvr.id
        )

    return render(
        request,
        'monitor/cctv_setup.html',
        {
            'nvrs': nvrs,
            'cameras': (
                CCTVCamera.objects
                .select_related('nvr')
                .all()
                .order_by(
                    'display_order',
                    'name',
                )
            ),
            'edit_nvr': edit_nvr,
            'edit_camera': edit_camera,
            'edit_nvr_username': (
                edit_secret['username']
            ),
            'edit_nvr_has_password': (
                edit_secret['has_password']
            ),
            'role': get_role(request.user),
        },
    )


@role_required('admin')
def cctv_nvr_save(request):
    from django.contrib import messages
    from django.shortcuts import redirect

    from .cctv_gateway import (
        rebuild_gateway_config,
        save_credentials,
    )
    from .models import CCTVNVR

    if request.method != 'POST':
        return redirect('cctv_setup')

    raw_id = (
        request.POST.get('id') or ''
    ).strip()

    obj = (
        CCTVNVR.objects
        .filter(id=raw_id)
        .first()
        if raw_id
        else None
    )

    creating = obj is None

    if creating:
        obj = CCTVNVR()

    name = (
        request.POST.get('name') or ''
    ).strip()

    local_host = _cctv_clean_host(
        request.POST.get('local_host')
    )

    remote_host = _cctv_clean_host(
        request.POST.get('remote_host')
    )

    try:
        web_port = int(
            request.POST.get(
                'web_port'
            ) or 443
        )

        rtsp_port = int(
            request.POST.get(
                'rtsp_port'
            ) or 554
        )

    except ValueError:
        messages.error(
            request,
            'Web and RTSP ports must be numbers.'
        )
        return redirect('cctv_setup')

    if not name or not local_host:
        messages.error(
            request,
            (
                'NVR name and local IP/hostname '
                'are required.'
            )
        )
        return redirect('cctv_setup')

    if not (
        1 <= web_port <= 65535 and
        1 <= rtsp_port <= 65535
    ):
        messages.error(
            request,
            'Ports must be between 1 and 65535.'
        )
        return redirect('cctv_setup')

    scheme = (
        request.POST.get(
            'web_scheme'
        ) or 'https'
    )

    if scheme not in (
        'http',
        'https',
    ):
        scheme = 'https'

    obj.name = name
    obj.local_host = local_host
    obj.remote_host = remote_host
    obj.web_scheme = scheme
    obj.web_port = web_port
    obj.rtsp_port = rtsp_port
    obj.enabled = (
        request.POST.get(
            'enabled'
        ) == 'on'
    )
    obj.note = (
        request.POST.get('note') or ''
    ).strip()[:300]

    obj.save()

    # Credentials are intentionally NOT written to db.sqlite3.
    # Blank username/password when editing means keep the existing value.
    username = (
        request.POST.get(
            'nvr_username'
        ) or ''
    ).strip()

    password = (
        request.POST.get(
            'nvr_password'
        ) or ''
    )

    try:
        info = save_credentials(
            obj.id,
            username=username,
            password=password,
        )

        rebuild_gateway_config()

    except Exception as exc:
        logger.exception(
            'CCTV gateway configuration update failed'
        )

        messages.warning(
            request,
            (
                'NVR metadata was saved, but the '
                f'live gateway could not be updated: {exc}'
            )
        )

        info = {
            'ready': False,
        }

    action_word = (
        'created'
        if creating
        else 'updated'
    )

    log_activity(
        request.user,
        'CCTV_CONFIG',
        (
            f'CCTV NVR {action_word}: '
            f'{obj.name} (ID:{obj.id}); '
            f'local={obj.local_host}; '
            f'remote={obj.remote_host or "not set"}; '
            f'gateway_credentials='
            f'{"configured" if info["ready"] else "not configured"}'
        ),
        get_ip(request)
    )

    messages.success(
        request,
        (
            f'NVR "{obj.name}" {action_word}. '
            'Live gateway configuration refreshed.'
        )
    )

    return redirect('cctv_setup')


@role_required('admin')
def cctv_nvr_delete(request, nid):
    from django.contrib import messages
    from django.shortcuts import (
        get_object_or_404,
        redirect,
    )

    from .cctv_gateway import (
        delete_credentials,
        rebuild_gateway_config,
    )
    from .models import CCTVNVR

    obj = get_object_or_404(
        CCTVNVR,
        id=nid,
    )

    if request.method != 'POST':
        return redirect('cctv_setup')

    name = obj.name
    nvr_id = obj.id
    camera_count = obj.cameras.count()

    obj.delete()

    try:
        delete_credentials(nvr_id)
        rebuild_gateway_config()
    except Exception:
        logger.exception(
            'CCTV gateway rebuild after NVR delete failed'
        )

    log_activity(
        request.user,
        'CCTV_CONFIG',
        (
            f'CCTV NVR deleted: {name}; '
            f'{camera_count} linked camera(s) removed.'
        ),
        get_ip(request)
    )

    messages.success(
        request,
        f'NVR "{name}" deleted.'
    )

    return redirect('cctv_setup')


@role_required('admin')
def cctv_camera_save(request):
    from django.contrib import messages
    from django.shortcuts import redirect
    from django.db import IntegrityError
    from .models import CCTVNVR, CCTVCamera

    if request.method != 'POST':
        return redirect('cctv_setup')

    raw_id = (request.POST.get('id') or '').strip()

    obj = (
        CCTVCamera.objects.filter(id=raw_id).first()
        if raw_id
        else None
    )

    creating = obj is None

    if creating:
        obj = CCTVCamera()

    try:
        nvr_id = int(request.POST.get('nvr_id') or 0)
        channel = int(request.POST.get('channel') or 0)
        stream_type = int(
            request.POST.get('stream_type') or 1
        )
        display_order = int(
            request.POST.get('display_order') or 1
        )
    except ValueError:
        messages.error(
            request,
            'NVR, channel and display order must be valid numbers.'
        )
        return redirect('cctv_setup')

    nvr = CCTVNVR.objects.filter(id=nvr_id).first()

    if not nvr:
        messages.error(request, 'Select a valid NVR.')
        return redirect('cctv_setup')

    name = (request.POST.get('name') or '').strip()

    if not name:
        messages.error(request, 'Camera name is required.')
        return redirect('cctv_setup')

    if not (1 <= channel <= 256):
        messages.error(
            request,
            'Camera channel must be between 1 and 256.'
        )
        return redirect('cctv_setup')

    if stream_type not in (0, 1):
        stream_type = 1

    if display_order < 1:
        display_order = 1

    obj.nvr = nvr
    obj.name = name[:100]
    obj.channel = channel
    obj.stream_type = stream_type
    obj.location = (
        request.POST.get('location') or ''
    ).strip()[:150]
    obj.display_order = display_order
    obj.enabled = request.POST.get('enabled') == 'on'

    obj.browser_url_local = (
        request.POST.get('browser_url_local') or ''
    ).strip()[:500]

    obj.browser_url_remote = (
        request.POST.get('browser_url_remote') or ''
    ).strip()[:500]

    obj.note = (
        request.POST.get('note') or ''
    ).strip()[:300]

    try:
        obj.save()
    except IntegrityError:
        messages.error(
            request,
            (
                f'Channel {channel} is already configured '
                f'for NVR "{nvr.name}".'
            )
        )
        return redirect('cctv_setup')

    try:
        from .cctv_gateway import rebuild_gateway_config
        rebuild_gateway_config()
    except Exception:
        logger.exception(
            'CCTV gateway rebuild after camera save failed'
        )

    action_word = 'created' if creating else 'updated'

    log_activity(
        request.user,
        'CCTV_CONFIG',
        (
            f'CCTV camera {action_word}: '
            f'{obj.name} (ID:{obj.id}), '
            f'NVR={nvr.name}, CH={obj.channel}, '
            f'stream={obj.stream_type}'
        ),
        get_ip(request)
    )

    messages.success(
        request,
        f'Camera "{obj.name}" {action_word}.'
    )

    return redirect('cctv_setup')


@role_required('admin')
def cctv_camera_delete(request, cid):
    from django.contrib import messages
    from django.shortcuts import redirect, get_object_or_404
    from .models import CCTVCamera

    obj = get_object_or_404(CCTVCamera, id=cid)

    if request.method != 'POST':
        return redirect('cctv_setup')

    detail = (
        f'CCTV camera deleted: {obj.name} '
        f'(ID:{obj.id}), NVR={obj.nvr.name}, CH={obj.channel}'
    )

    name = obj.name
    obj.delete()

    try:
        from .cctv_gateway import rebuild_gateway_config
        rebuild_gateway_config()
    except Exception:
        logger.exception(
            'CCTV gateway rebuild after camera delete failed'
        )

    log_activity(
        request.user,
        'CCTV_CONFIG',
        detail,
        get_ip(request)
    )

    messages.success(
        request,
        f'Camera "{name}" deleted.'
    )

    return redirect('cctv_setup')


# ══════════════════════════════════════════════════════════════════════════════
# SELF-SERVICE NOTIFICATION REQUEST
# ══════════════════════════════════════════════════════════════════════════════

_NOTIFICATION_REQUEST_ALERTS = [
    ('alert_outage',     '⚡ Power Outage Started'),
    ('alert_critical',   '🚨 Critical — Both Devices Down'),
    ('alert_alarm',      '🟠 Alarm / Abnormal Condition'),
    ('alert_complete',   '✅ Outage Cycle Complete'),
    ('alert_pac_status', '❄️ SMW6PAC Status Change'),
    ('daily_summary',    '📋 Daily Generator Summary'),
    ('monthly_report',   '📊 Monthly Report'),
    ('colocation_data',  '🌡️ Colocation Data Update'),
    ('colocation_alarm', '🚨 Colocation Temperature/Humidity Alarm'),
]

_NOTIFICATION_REQUEST_KEYS = {
    key for key, _label in _NOTIFICATION_REQUEST_ALERTS
}


def _notification_request_allowed(request):
    return get_role(request.user) in ('viewer', 'user', 'admin')


def _notification_request_draft(user):
    from monitor.models import NotificationRequest

    draft = (
        NotificationRequest.objects
        .filter(user=user, status='DRAFT')
        .order_by('-updated_at')
        .first()
    )

    if draft is None:
        draft = NotificationRequest.objects.create(
            user=user,
            email_contact=user.email or '',
        )

    return draft


def _clean_requested_alerts(values):
    return [
        value
        for value in values
        if value in _NOTIFICATION_REQUEST_KEYS
    ]


@login_required(login_url='login')
def notification_request_page(request):
    if not _notification_request_allowed(request):
        return render(request, 'monitor/denied.html', status=403)

    from monitor.models import (
        NotificationGateway,
        NotificationRequest,
        NotificationRecipient,
    )
    from monitor.notifications import get_telegram_bot_identity

    latest = (
        NotificationRequest.objects
        .filter(user=request.user)
        .order_by('-updated_at')
        .first()
    )

    draft = (
        NotificationRequest.objects
        .filter(user=request.user, status='DRAFT')
        .order_by('-updated_at')
        .first()
    )

    pending = (
        NotificationRequest.objects
        .filter(user=request.user, status='PENDING')
        .order_by('-requested_at')
        .first()
    )

    approved = (
        NotificationRequest.objects
        .filter(user=request.user, status='APPROVED')
        .order_by('-reviewed_at', '-updated_at')
        .first()
    )

    approved_recipients = list(
        NotificationRecipient.objects
        .filter(user=request.user)
        .order_by('channel')
    )

    bot_username = ''
    bot_name = 'SysMonitor Bot'

    gateway = (
        NotificationGateway.objects
        .filter(channel='telegram', is_enabled=True)
        .first()
    )

    if gateway and gateway.tg_bot_token:
        ok, bot = get_telegram_bot_identity(gateway.tg_bot_token)
        if ok:
            bot_username = bot.get('username', '')
            bot_name = bot.get('name', 'SysMonitor Bot')

    admin_pending = []
    if get_role(request.user) == 'admin':
        admin_pending = list(
            NotificationRequest.objects
            .filter(status='PENDING')
            .select_related('user')
            .order_by('requested_at')
        )

    return render(request, 'monitor/notification_request.html', {
        'role': get_role(request.user),
        'alerts': _NOTIFICATION_REQUEST_ALERTS,
        'latest_request': latest,
        'approved_request': approved,
        'draft': draft,
        'pending_request': pending,
        'approved_recipients': approved_recipients,
        'admin_pending_count': len(admin_pending),
        'bot_username': bot_username,
        'bot_name': bot_name,
        'admin_pending': admin_pending,
        'is_mirror': settings.IS_MIRROR,
    })


@login_required(login_url='login')
def notification_request_pair_start(request):
    if not _notification_request_allowed(request):
        return render(request, 'monitor/denied.html', status=403)

    if request.method != 'POST':
        return redirect('notification_request')

    if settings.IS_MIRROR:
        messages.error(
            request,
            'Telegram pairing from the REMOTE site will be enabled after '
            'MASTER testing is completed.'
        )
        return redirect('notification_request')

    import secrets
    import string
    from django.utils import timezone

    draft = _notification_request_draft(request.user)

    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    draft.pairing_code = ''.join(
        secrets.choice(alphabet)
        for _ in range(6)
    )
    draft.pairing_created_at = timezone.now()

    # Starting a new pairing invalidates the previous Telegram verification
    # for this draft.
    draft.telegram_chat_id = ''
    draft.telegram_display_name = ''
    draft.telegram_verified = False

    draft.save()

    messages.success(
        request,
        f'Telegram pairing code created: {draft.pairing_code}'
    )

    return redirect('notification_request')


@login_required(login_url='login')
def notification_request_pair_verify(request):
    if not _notification_request_allowed(request):
        return render(request, 'monitor/denied.html', status=403)

    if request.method != 'POST':
        return redirect('notification_request')

    if settings.IS_MIRROR:
        messages.error(
            request,
            'Telegram pairing from the REMOTE site will be enabled after '
            'MASTER testing is completed.'
        )
        return redirect('notification_request')

    from datetime import timedelta
    from django.utils import timezone
    from monitor.models import NotificationGateway, NotificationRequest
    from monitor.notifications import find_telegram_pairing_message

    draft = (
        NotificationRequest.objects
        .filter(user=request.user, status='DRAFT')
        .order_by('-updated_at')
        .first()
    )

    if not draft or not draft.pairing_code or not draft.pairing_created_at:
        messages.error(request, 'Create a Telegram pairing code first.')
        return redirect('notification_request')

    if timezone.now() - draft.pairing_created_at > timedelta(minutes=15):
        messages.error(
            request,
            'That Telegram pairing code expired. Create a new code.'
        )
        return redirect('notification_request')

    gateway = (
        NotificationGateway.objects
        .filter(channel='telegram', is_enabled=True)
        .first()
    )

    if not gateway or not gateway.tg_bot_token:
        messages.error(request, 'Telegram gateway is not enabled/configured.')
        return redirect('notification_request')

    ok, result = find_telegram_pairing_message(
        gateway.tg_bot_token,
        draft.pairing_code,
        not_before=draft.pairing_created_at,
    )

    if not ok:
        messages.error(request, result)
        return redirect('notification_request')

    draft.telegram_chat_id = result['chat_id']
    draft.telegram_display_name = result['display_name']
    draft.telegram_verified = True
    draft.save()

    log_activity(
        request.user,
        'NOTIF_TELEGRAM_PAIR',
        f'Telegram notification account verified as "{draft.telegram_display_name}".',
        get_ip(request),
    )

    messages.success(
        request,
        f"Telegram verified: {draft.telegram_display_name}"
    )

    return redirect('notification_request')


@login_required(login_url='login')
def notification_request_submit(request):
    if not _notification_request_allowed(request):
        return render(request, 'monitor/denied.html', status=403)

    if request.method != 'POST':
        return redirect('notification_request')

    if settings.IS_MIRROR:
        messages.error(
            request,
            'Notification requests from the REMOTE site will be enabled '
            'after MASTER testing is completed.'
        )
        return redirect('notification_request')

    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    from django.utils import timezone
    from monitor.models import NotificationRequest

    # Don't create two simultaneous pending requests for one account.
    if NotificationRequest.objects.filter(
        user=request.user,
        status='PENDING',
    ).exists():
        messages.error(
            request,
            'You already have a pending notification request. '
            'Please wait for administrator review.'
        )
        return redirect('notification_request')

    draft = _notification_request_draft(request.user)

    email_contact = request.POST.get('email_contact', '').strip()
    email_alerts = _clean_requested_alerts(
        request.POST.getlist('email_alerts')
    )
    telegram_alerts = _clean_requested_alerts(
        request.POST.getlist('telegram_alerts')
    )

    if email_alerts:
        if not email_contact:
            messages.error(
                request,
                'Enter an email address for the selected Email notifications.'
            )
            return redirect('notification_request')

        try:
            validate_email(email_contact)
        except ValidationError:
            messages.error(request, 'Enter a valid notification email address.')
            return redirect('notification_request')

    if telegram_alerts and not draft.telegram_verified:
        messages.error(
            request,
            'Verify your Telegram account before selecting Telegram notifications.'
        )
        return redirect('notification_request')

    if not email_alerts and not telegram_alerts:
        messages.error(
            request,
            'Select at least one Email or Telegram notification.'
        )
        return redirect('notification_request')

    draft.email_contact = email_contact
    draft.email_alerts = email_alerts
    draft.telegram_alerts = telegram_alerts
    draft.status = 'PENDING'
    draft.requested_at = timezone.now()
    draft.reviewed_at = None
    draft.reviewed_by = ''
    draft.admin_note = ''
    draft.save()

    channel_names = []
    if email_alerts:
        channel_names.append('Email')
    if telegram_alerts:
        channel_names.append('Telegram')

    log_activity(
        request.user,
        'NOTIF_REQUEST_SUBMIT',
        (
            f'Notification request submitted by "{request.user.username}"; '
            f'channels={", ".join(channel_names)}; '
            f'email alerts={len(email_alerts)}; '
            f'telegram alerts={len(telegram_alerts)}.'
        )[:300],
        get_ip(request),
    )

    messages.success(
        request,
        'Notification request submitted for administrator approval.'
    )

    return redirect('notification_request')



@login_required(login_url='login')
def notification_request_change(request):
    if not _notification_request_allowed(request):
        return render(request, 'monitor/denied.html', status=403)

    if request.method != 'POST':
        return redirect('notification_request')

    if settings.IS_MIRROR:
        messages.error(
            request,
            'Notification changes from the REMOTE site are not writable here. '
            'Please use the MASTER site.'
        )
        return redirect('notification_request')

    from monitor.models import NotificationRequest

    if NotificationRequest.objects.filter(
        user=request.user,
        status='PENDING',
    ).exists():
        messages.error(
            request,
            'You already have a pending notification request.'
        )
        return redirect('notification_request')

    approved = (
        NotificationRequest.objects
        .filter(user=request.user, status='APPROVED')
        .order_by('-reviewed_at', '-updated_at')
        .first()
    )

    if approved is None:
        messages.error(request, 'No approved notification settings were found.')
        return redirect('notification_request')

    draft = (
        NotificationRequest.objects
        .filter(user=request.user, status='DRAFT')
        .order_by('-updated_at')
        .first()
    )

    if draft is None:
        draft = NotificationRequest(user=request.user)

    draft.email_contact = approved.email_contact
    draft.email_alerts = list(approved.email_alerts or [])
    draft.telegram_chat_id = approved.telegram_chat_id
    draft.telegram_display_name = approved.telegram_display_name
    draft.telegram_verified = approved.telegram_verified
    draft.telegram_alerts = list(approved.telegram_alerts or [])
    draft.pairing_code = ''
    draft.pairing_created_at = None
    draft.status = 'DRAFT'
    draft.requested_at = None
    draft.reviewed_at = None
    draft.reviewed_by = ''
    draft.admin_note = ''
    draft.save()

    log_activity(
        request.user,
        'NOTIF_REQUEST_CHANGE',
        (
            f'User "{request.user.username}" opened a notification '
            f'change request from current approved settings.'
        ),
        get_ip(request),
    )

    messages.success(
        request,
        'Your current approved settings were copied into a new change request. '
        'Existing approved notifications remain active until a new request is approved.'
    )

    return redirect('notification_request')


@role_required('admin')
def notification_request_approve(request, rid):
    if request.method != 'POST':
        return redirect('notification_request')

    if settings.IS_MIRROR:
        messages.error(
            request,
            'Administrator approval must run on MASTER.'
        )
        return redirect('notification_request')

    from django.utils import timezone
    from monitor.models import NotificationRequest, NotificationRecipient

    req = get_object_or_404(
        NotificationRequest,
        id=rid,
        status='PENDING',
    )

    email_contact = request.POST.get(
        'email_contact',
        req.email_contact,
    ).strip()

    email_alerts = _clean_requested_alerts(
        request.POST.getlist('email_alerts')
    )
    telegram_alerts = _clean_requested_alerts(
        request.POST.getlist('telegram_alerts')
    )

    if email_alerts:
        if not email_contact:
            messages.error(
                request,
                'A notification email address is required when Email alerts are selected.'
            )
            return redirect('notification_request')

        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError

        try:
            validate_email(email_contact)
        except ValidationError:
            messages.error(
                request,
                'Enter a valid notification email address before approval.'
            )
            return redirect('notification_request')

    if telegram_alerts and not req.telegram_verified:
        messages.error(
            request,
            'Telegram cannot be approved because it has not been verified.'
        )
        return redirect('notification_request')

    if not email_alerts and not telegram_alerts:
        messages.error(
            request,
            'Select at least one Email or Telegram notification before approval.'
        )
        return redirect('notification_request')

    if email_alerts:
        conflict = (
            NotificationRecipient.objects
            .filter(channel='email', contact=email_contact)
            .exclude(user=req.user)
            .first()
        )

        if conflict:
            messages.error(
                request,
                (
                    f'The email "{email_contact}" is already used by '
                    f'notification recipient "{conflict.name}". '
                    'Edit that existing recipient first to avoid duplicate delivery.'
                )
            )
            return redirect('notification_request')

    if telegram_alerts:
        conflict = (
            NotificationRecipient.objects
            .filter(channel='telegram', contact=req.telegram_chat_id)
            .exclude(user=req.user)
            .first()
        )

        if conflict:
            messages.error(
                request,
                (
                    'That Telegram account is already linked to another '
                    f'notification recipient "{conflict.name}".'
                )
            )
            return redirect('notification_request')

    display_name = (
        req.user.get_full_name().strip()
        or req.user.username
    )

    def apply_recipient(channel, contact, selected):
        existing = (
            NotificationRecipient.objects
            .filter(user=req.user, channel=channel)
            .order_by('id')
            .first()
        )

        if not selected:
            if existing:
                existing.is_active = False
                existing.save(update_fields=['is_active'])
            return

        if existing is None:
            existing = NotificationRecipient(
                user=req.user,
                channel=channel,
            )

        existing.name = display_name
        existing.contact = contact
        existing.is_active = True

        selected_set = set(selected)

        for field, _label in _NOTIFICATION_REQUEST_ALERTS:
            setattr(existing, field, field in selected_set)

        existing.save()

    apply_recipient('email', email_contact, email_alerts)

    apply_recipient(
        'telegram',
        req.telegram_chat_id,
        telegram_alerts,
    )

    req.email_contact = email_contact
    req.email_alerts = email_alerts
    req.telegram_alerts = telegram_alerts
    req.status = 'APPROVED'
    req.reviewed_at = timezone.now()
    req.reviewed_by = request.user.username
    req.admin_note = request.POST.get('admin_note', '').strip()[:500]
    req.save()

    log_activity(
        request.user,
        'NOTIF_REQUEST_APPROVE',
        (
            f'Admin "{request.user.username}" approved notification request '
            f'for "{req.user.username}"; '
            f'email={email_contact if email_alerts else "disabled"} '
            f'({len(email_alerts)} alerts); '
            f'telegram={"enabled" if telegram_alerts else "disabled"} '
            f'({len(telegram_alerts)} alerts).'
        )[:300],
        get_ip(request),
    )

    messages.success(
        request,
        f'Notification request for {display_name} approved.'
    )

    return redirect('notification_request')


@role_required('admin')
def notification_request_delete(request, rid):
    if request.method != 'POST':
        return redirect('notification_request')

    if settings.IS_MIRROR:
        messages.error(request, 'Delete must run on MASTER.')
        return redirect('notification_request')

    from monitor.models import NotificationRequest

    req = get_object_or_404(
        NotificationRequest,
        id=rid,
        status='PENDING',
    )

    username = req.user.username
    req.delete()

    log_activity(
        request.user,
        'NOTIF_REQUEST_DELETE',
        (
            f'Admin "{request.user.username}" deleted pending notification '
            f'request for "{username}".'
        ),
        get_ip(request),
    )

    messages.success(
        request,
        f'Pending notification request for {username} deleted.'
    )

    return redirect('notification_request')


# ─── Per-user Page Access ─────────────────────────────────────────────────────

@role_required('admin')
def page_access_manage(request):
    """
    Restrict pages for one user without ever exceeding that user's role.

    Role permission remains the hard maximum. This screen can only subtract
    pages that are already part of that role.
    """
    from collections import OrderedDict

    from .page_access import pages_for_role

    users = (
        User.objects
        .filter(is_active=True)
        .order_by('username')
    )

    raw_uid = (
        request.POST.get('user_id')
        if request.method == 'POST'
        else request.GET.get('user')
    )

    target = None

    if raw_uid:
        target = get_object_or_404(User, id=raw_uid)
    else:
        target = users.first()

    selected_role = None
    grouped_pages = OrderedDict()
    current_hidden = set()

    if target is not None:
        profile, _ = UserProfile.objects.get_or_create(user=target)

        selected_role = get_role(target)
        allowed = pages_for_role(selected_role)

        current_hidden = set(profile.hidden_pages or [])

        if request.method == 'POST':
            # Browser submits ONLY pages currently allowed by the role.
            visible = set(request.POST.getlist('visible_pages'))
            allowed_keys = set(allowed.keys())

            # The administrator can only hide pages inside the role.
            hidden = sorted(allowed_keys - visible)

            # Preserve no stale/out-of-role values. Role is authoritative.
            profile.hidden_pages = hidden
            profile.save(update_fields=['hidden_pages'])

            log_activity(
                request.user,
                'USER_EDITED',
                (
                    f'Updated page access for "{target.username}" '
                    f'(role={selected_role}); '
                    f'hidden={", ".join(hidden) if hidden else "none"}.'
                )[:300],
                ip=get_ip(request),
            )

            messages.success(
                request,
                f'Page access updated for "{target.username}".'
            )

            return redirect(
                f'/page-access/?user={target.id}'
            )

        current_hidden &= set(allowed.keys())

        for key, cfg in allowed.items():
            group = cfg['group']
            grouped_pages.setdefault(group, [])
            grouped_pages[group].append({
                'key': key,
                'label': cfg['label'],
                'visible': key not in current_hidden,
            })

    return render(request, 'monitor/page_access.html', {
        'users': users,
        'target_user': target,
        'target_role': selected_role,
        'grouped_pages': grouped_pages,
        'hidden_count': len(current_hidden),
        'role': get_role(request.user),
        'user': request.user,
    })


# ============================================================================
# SYSMONITOR — RESTART ALL MASTER SERVICES
# ============================================================================

SYSMONITOR_RESTART_ALL_RESULT = (
    '/run/sysmonitor-restart-all-result.json'
)


@role_required('admin')
def system_restart_all(request):
    """
    Launch the fixed MASTER service-restart helper.

    Security:
      - Admin role required by decorator.
      - Django superuser additionally required.
      - Current user's own Django password must be confirmed.
      - No Linux password is accepted or handled here.
      - Only the fixed root-owned helper may be executed.
    """
    if settings.IS_MIRROR:
        return JsonResponse({
            'ok': False,
            'error': (
                'Restart All is available on the MASTER '
                'SysMonitor only.'
            ),
        }, status=403)

    if not request.user.is_superuser:
        return JsonResponse({
            'ok': False,
            'error': (
                'Only the system superuser (admin) can restart '
                'all SysMonitor services.'
            ),
        }, status=403)

    if request.method != 'POST':
        return JsonResponse({
            'ok': False,
            'error': 'POST required',
        }, status=405)

    import json as _json
    import os
    import subprocess

    from django.contrib.auth.models import User as AuthUser
    from django.contrib.auth.hashers import (
        check_password as check_pw,
    )

    try:
        data = _json.loads(
            request.body.decode('utf-8')
        )
    except Exception:
        return JsonResponse({
            'ok': False,
            'error': 'Invalid request.',
        }, status=400)

    password = (
        data.get('password')
        or ''
    ).strip()

    confirmed = bool(
        data.get('confirmed')
    )

    if not confirmed:
        return JsonResponse({
            'ok': False,
            'error': (
                'You must confirm that you understand '
                'monitoring will be briefly interrupted.'
            ),
        }, status=400)

    if not password:
        return JsonResponse({
            'ok': False,
            'error': (
                'Your SysMonitor admin password is required.'
            ),
        }, status=400)

    try:
        fresh_user = AuthUser.objects.get(
            pk=request.user.pk
        )
    except AuthUser.DoesNotExist:
        return JsonResponse({
            'ok': False,
            'error': 'User not found.',
        }, status=403)

    if (
        not fresh_user.is_superuser
        or not check_pw(
            password,
            fresh_user.password,
        )
    ):
        log_activity(
            request.user,
            'SERVICE_RESTART_DENIED',
            (
                'Failed password confirmation for '
                'restart-all SysMonitor services '
                f'(user: {request.user.username})'
            ),
            get_ip(request),
        )

        return JsonResponse({
            'ok': False,
            'error': (
                f'Incorrect password for user '
                f'"{request.user.username}". Action denied.'
            ),
        }, status=403)

    # Remove the result from a previous run so browser polling cannot
    # accidentally display stale success information.
    try:
        if os.path.exists(
            SYSMONITOR_RESTART_ALL_RESULT
        ):
            os.remove(
                SYSMONITOR_RESTART_ALL_RESULT
            )
    except OSError:
        # A root-owned result may not be removable by Django.
        # The helper overwrites it atomically anyway.
        pass

    try:
        process = subprocess.Popen(
            [
                'sudo',
                '-n',
                '/bin/systemctl',
                'start',
                'sysmonitor-restart-all.service',
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    except Exception as exc:
        return JsonResponse({
            'ok': False,
            'error': (
                'Unable to launch service restart helper: '
                f'{exc}'
            ),
        }, status=500)

    log_activity(
        request.user,
        'SERVICE_RESTART',
        (
            'Admin initiated restart of all SysMonitor '
            'MASTER services and timers '
            '(SysMonitor password confirmed).'
        ),
        get_ip(request),
    )

    return JsonResponse({
        'ok': True,
        'started': True,
        'pid': process.pid,
        'message': (
            'Restart started. SysMonitor will briefly disconnect '
            'while the web service is restarted.'
        ),
    })


@role_required('admin')
def system_restart_all_status(request):
    """
    Return summarized status generated by the root-owned restart helper.
    Read-only and superuser-only.
    """
    if settings.IS_MIRROR:
        return JsonResponse({
            'ok': False,
            'error': (
                'Restart All status is available on the MASTER '
                'SysMonitor only.'
            ),
        }, status=403)

    if not request.user.is_superuser:
        return JsonResponse({
            'ok': False,
            'error': (
                'Only the system superuser (admin) can view '
                'restart-all results.'
            ),
        }, status=403)

    if request.method != 'GET':
        return JsonResponse({
            'ok': False,
            'error': 'GET required',
        }, status=405)

    import json as _json
    import os

    path = SYSMONITOR_RESTART_ALL_RESULT

    if not os.path.exists(path):
        return JsonResponse({
            'ok': True,
            'complete': False,
            'message': 'Restart is still in progress.',
        })

    try:
        with open(
            path,
            'r',
            encoding='utf-8',
        ) as handle:
            data = _json.load(handle)

    except Exception as exc:
        return JsonResponse({
            'ok': False,
            'complete': False,
            'error': (
                'Unable to read restart result: '
                f'{exc}'
            ),
        }, status=500)

    if data.get('overall') == 'running':
        return JsonResponse({
            'ok': True,
            'complete': False,
            'message': 'Restart is still in progress.',
        })

    return JsonResponse({
        'ok': True,
        'complete': True,
        'result': data,
    })
