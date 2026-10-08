from django.db import models


class Device(models.Model):
    name        = models.CharField(max_length=100)
    ip_address  = models.GenericIPAddressField(unique=True)
    description = models.CharField(max_length=200, blank=True)
    is_active   = models.BooleanField(default=True)
    added_at    = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.ip_address})"


class DeviceStatus(models.Model):
    STATUS_CHOICES = [
        ('UP',      'Up'),
        ('DOWN',    'Down'),
        ('UNKNOWN', 'Unknown'),
    ]
    device      = models.ForeignKey(Device, on_delete=models.CASCADE)
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default='UNKNOWN')
    checked_at  = models.DateTimeField(auto_now_add=True)
    response_ms = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-checked_at']

    def __str__(self):
        return f"{self.device.name} — {self.status} at {self.checked_at}"


class Event(models.Model):
    LEVEL_CHOICES = [
        ('INFO',     'Info'),
        ('NOTICE',   'Notice'),
        ('OUTAGE',   'Outage'),
        ('GEN-UP',   'Generator Up'),
        ('ATS',      'ATS Switching'),
        ('NORMAL',   'Normal'),
        ('CRITICAL', 'Critical'),
    ]
    device     = models.ForeignKey(Device, on_delete=models.CASCADE, null=True, blank=True)
    level      = models.CharField(max_length=20, choices=LEVEL_CHOICES)
    message    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.level}] {self.message[:60]}"


class SystemStatus(models.Model):
    STATUS_CHOICES = [
        ('NORMAL',       '🟢 All Systems Normal'),
        ('OUTAGE',       '🔴 Power Outage In Progress'),
        ('GENERATOR',    '🟡 Generator Running'),
        ('DEVICE_DOWN',  '🟠 Device Down / Unreachable'),
        ('ATS',          '🔵 ATS Switching'),
    ]
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NORMAL')
    updated_at = models.DateTimeField(auto_now=True)
    note       = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name_plural = "System Status"

    def __str__(self):
        return f"{self.status} — {self.updated_at}"


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('admin',  'Admin'),
        ('user',   'User'),
        ('viewer', 'Viewer'),
        ('guest',  'Guest'),
    ]
    user        = models.OneToOneField('auth.User', on_delete=models.CASCADE)
    role        = models.CharField(max_length=10, choices=ROLE_CHOICES, default='viewer')

    # Profile fields — self-service editable
    designation     = models.CharField(max_length=100, blank=True)
    mobile_number   = models.CharField(max_length=30, blank=True)
    whatsapp_number = models.CharField(max_length=30, blank=True)
    telegram_handle = models.CharField(max_length=100, blank=True)  # @username or chat link/number
    profile_picture = models.ImageField(upload_to='profile_pics/', blank=True, null=True)

    # Usage tracking — accumulated in real time by UsageTrackingMiddleware.
    # total_usage_seconds only counts gaps between requests under
    # USAGE_IDLE_TIMEOUT (see middleware), so idle browser tabs don't
    # inflate it. Starts at 0 from whenever this was deployed — no
    # retroactive history is possible.
    total_usage_seconds = models.BigIntegerField(default=0)
    last_activity_at    = models.DateTimeField(blank=True, null=True)

    # ── Forced password change (used after an admin-issued temporary
    # password from the "Forgot password" flow). While must_change_password
    # is True, ForcePasswordChangeMiddleware redirects every request to the
    # change-password page. temp_password_expires_at is the deadline (30 min
    # after issue) after which the temporary password itself stops working.
    must_change_password    = models.BooleanField(default=False)
    temp_password_expires_at = models.DateTimeField(blank=True, null=True)

    # Per-user restrictions applied on top of the role's normal permissions.
    # This can only REMOVE access; it can never grant pages outside the role.
    hidden_pages = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"{self.user.username} — {self.role}"


class ProfileChangeRequest(models.Model):
    """
    Sensitive profile fields (email, mobile number) require admin approval
    before taking effect. A request sits here as PENDING until an admin
    approves or rejects it.
    """
    FIELD_CHOICES = [
        ('email',  'Email'),
        ('mobile', 'Mobile Number'),
    ]
    STATUS_CHOICES = [
        ('PENDING',  'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    user        = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='change_requests')
    field       = models.CharField(max_length=10, choices=FIELD_CHOICES)
    old_value   = models.CharField(max_length=200, blank=True)
    new_value   = models.CharField(max_length=200)
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    reviewed_by  = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.user.username} — {self.field} → {self.new_value} ({self.status})"

class SystemRevision(models.Model):
    """
    MASTER-owned monotonically increasing SysMonitor revision.

    One singleton row (pk=1) is mirrored to REMOTE with the normal
    database snapshot. Page views and ordinary login activity do not
    change this value.
    """
    major = models.PositiveIntegerField(default=1)
    minor = models.PositiveIntegerField(default=1)
    revision = models.PositiveBigIntegerField(default=1234)

    modified_at = models.DateTimeField(auto_now=True)
    modified_by = models.CharField(max_length=100, blank=True)
    last_action = models.CharField(max_length=200, blank=True)
    git_commit = models.CharField(max_length=40, blank=True)

    class Meta:
        verbose_name = 'System Revision'
        verbose_name_plural = 'System Revision'

    @property
    def version(self):
        return f'{self.major}.{self.minor}.{self.revision:04d}'

    def __str__(self):
        return f'v{self.version}'


class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('LOGIN',                  'Login'),
        ('LOGOUT',                 'Logout'),
        ('LOGIN_FAILED',           'Login Failed'),

        ('USER_CREATED',           'User Created'),
        ('USER_EDITED',            'User Edited'),
        ('USER_DELETED',           'User Deleted'),

        ('DEVICE_ADDED',           'Device Added'),
        ('DEVICE_EDITED',          'Device Edited'),
        ('DEVICE_DELETED',         'Device Deleted'),

        ('PROFILE_UPDATE',         'Profile Updated'),
        ('PROFILE_CHANGE_REQ',     'Profile Change Requested'),
        ('PROFILE_CHANGE_APPROVE', 'Profile Change Approved'),
        ('PROFILE_CHANGE_REJECT',  'Profile Change Rejected'),
        ('PASSWORD_CHANGE',        'Password Changed'),
        ('PASSWORD_RESET_REQUESTED', 'Password Reset Requested'),

        ('NOTIF_REQUEST_SUBMIT',   'Notification Request Submitted'),
        ('NOTIF_REQUEST_CHANGE',   'Notification Change Requested'),
        ('NOTIF_REQUEST_APPROVE',  'Notification Request Approved'),
        ('NOTIF_REQUEST_DELETE',   'Notification Request Deleted'),
        ('NOTIF_TELEGRAM_PAIR',    'Telegram Notification Paired'),

        ('NOTIF_GATEWAY_EDIT',     'Notification Gateway Updated'),
        ('NOTIF_GATEWAY_TEST',     'Notification Gateway Test Sent'),
        ('NOTIF_RECIPIENT_ADD',    'Notification Recipient Added'),
        ('NOTIF_RECIPIENT_EDIT',   'Notification Recipient Edited'),
        ('NOTIF_RECIPIENT_DELETE', 'Notification Recipient Deleted'),
        ('NOTIF_RECIPIENT_TOGGLE', 'Notification Recipient Toggled'),
        ('NOTIF_RECIPIENT_TEST',   'Notification Recipient Test Sent'),

        ('GEN_SHIFT_ADD',          'Generator Shift Added'),
        ('GEN_SHIFT_EDIT',         'Generator Shift Edited'),
        ('GEN_SHIFT_DELETE',       'Generator Shift Deleted'),
        ('GENERATOR_FUEL_ADD',     'Generator Fuel Entry Added'),

        ('CYCLE_CLOSE',            'Cycle Force Closed'),
        ('CYCLE_DELETE',           'Cycle Deleted'),
        ('CYCLE_MANUAL_ADD',       'Manual Cycle Added'),
        ('CYCLE_MANUAL_EDIT',      'Manual Cycle Edited'),
        ('CYCLE_MANUAL_DELETE',    'Manual Cycle Deleted'),

        ('COLO_SETPOINTS',         'Colocation Setpoints Updated'),
        ('MAINT_START',            'Maintenance Mode Started'),
        ('MAINT_STOP',             'Maintenance Mode Stopped'),

        ('SERVICE_RESTART',        'Service Restarted'),
        ('SERVICE_RESTART_DENIED', 'Service Restart Denied'),

        ('CCTV_CONFIG',            'CCTV Configuration Changed'),
        ('MESSAGE_TEMPLATE_EDIT',  'Message Template Updated'),
        ('MONTHLY_REPORT_SEND',    'Monthly Report Sent'),

        ('SHIFT_REPORT_DRAFT',     'Shift Report Draft Saved'),
        ('SHIFT_REPORT_SEND',      'Shift Report Sent'),
        ('SHIFT_REPORT_CONFIG',    'Shift Report Configuration Changed'),
        ('SHIFT_ISSUE_ADD',        'Shift Important Issue Added'),
        ('SHIFT_ISSUE_EDIT',       'Shift Important Issue Updated'),
    ]

    user       = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                   null=True, blank=True)
    action     = models.CharField(max_length=40, choices=ACTION_CHOICES)
    detail     = models.CharField(max_length=300, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user} — {self.action} at {self.timestamp}"

class OutageCycle(models.Model):
    CYCLE_CHOICES = [
        ('NORMAL',     'Normal Cycle'),
        ('ATS_ONLY',   'ATS Switchover Only'),
        ('ALARM',      'Alarm — Abnormal'),
        ('INCOMPLETE', 'Incomplete — In Progress'),
        ('CRITICAL',   'Critical — Total Failure'),
        ('MANUAL',     'Manual / Audited Entry'),
    ]
    GENERATOR_CHOICES = [
        ('Gen-01', 'Generator 01'),
        ('Gen-02', 'Generator 02'),
    ]

    outage_start  = models.DateTimeField(null=True, blank=True)
    gen_start     = models.DateTimeField(null=True, blank=True)
    pdb_restored  = models.DateTimeField(null=True, blank=True)
    cycle_end     = models.DateTimeField(null=True, blank=True)

    pdb_duration_sec  = models.IntegerField(default=0)
    gen_runtime_sec   = models.IntegerField(default=0)

    cycle_type    = models.CharField(max_length=15, choices=CYCLE_CHOICES, default='INCOMPLETE')
    is_complete   = models.BooleanField(default=False)
    alarm_reason  = models.CharField(max_length=300, blank=True)

    # ── Manual / audited entry fields ──────────────────────────────────────
    # Set only when this cycle was entered by hand via the System Tools page
    # (e.g. an outage the automatic ping monitor missed, or a corrected
    # record). manual_generator lets an admin state directly which generator
    # was in use, bypassing the usual GeneratorModeLog-based inference.
    is_manual         = models.BooleanField(default=False)
    manual_generator  = models.CharField(max_length=20, choices=GENERATOR_CHOICES, blank=True)
    added_by          = models.CharField(max_length=100, blank=True)

    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-outage_start']
        constraints = [
            models.UniqueConstraint(
                fields=['outage_start', 'cycle_end'],
                condition=models.Q(is_manual=True),
                name='unique_manual_cycle_start_end',
            ),
        ]

    def pdb_duration_fmt(self):
        s = self.pdb_duration_sec
        if not s:
            return '—'
        h, r = divmod(s, 3600)
        m, s = divmod(r, 60)
        if h:
            return f'{h}h {m:02d}m {s:02d}s'
        if m:
            return f'{m}m {s:02d}s'
        return f'{s}s'

    def gen_runtime_fmt(self):
        s = self.gen_runtime_sec
        if not s:
            return '—'
        h, r = divmod(s, 3600)
        m, s = divmod(r, 60)
        if h:
            return f'{h}h {m:02d}m {s:02d}s'
        if m:
            return f'{m}m {s:02d}s'
        return f'{s}s'

    def __str__(self):
        return f"{self.cycle_type} — {self.outage_start}"


# ── Notification Models ────────────────────────────────────────────────────────

CHANNEL_CHOICES = [
    ('whatsapp', 'WhatsApp (Meta Cloud API)'),
    ('telegram', 'Telegram Bot'),
    ('email',    'Email (Gmail SMTP)'),
]

class NotificationGateway(models.Model):
    channel             = models.CharField(max_length=20, unique=True, choices=CHANNEL_CHOICES)
    is_enabled          = models.BooleanField(default=False)
    # WhatsApp
    wa_phone_number_id  = models.CharField(max_length=100, blank=True)
    wa_access_token     = models.CharField(max_length=500, blank=True)
    wa_from_number      = models.CharField(max_length=30,  blank=True)
    # Telegram
    tg_bot_token        = models.CharField(max_length=200, blank=True)
    # Email
    email_host          = models.CharField(max_length=100, blank=True, default='smtp.gmail.com')
    email_port          = models.IntegerField(default=587)
    email_username      = models.CharField(max_length=200, blank=True)
    email_password      = models.CharField(max_length=200, blank=True)
    email_from          = models.CharField(max_length=200, blank=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Notification Gateway'

    def __str__(self):
        return f"{self.get_channel_display()} ({'enabled' if self.is_enabled else 'disabled'})"


class NotificationRecipient(models.Model):
    CHANNEL_CHOICES_R = [
        ('whatsapp', 'WhatsApp'),
        ('telegram', 'Telegram'),
        ('email',    'Email'),
    ]
    user            = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notification_recipients',
        help_text='Optional SysMonitor account linked to this recipient.'
    )
    name            = models.CharField(max_length=100)
    channel         = models.CharField(max_length=20, choices=CHANNEL_CHOICES_R)
    contact         = models.CharField(max_length=200)
    is_active       = models.BooleanField(default=True)
    alert_outage    = models.BooleanField(default=True)
    alert_critical  = models.BooleanField(default=True)
    alert_alarm     = models.BooleanField(default=True)
    alert_complete  = models.BooleanField(default=True)
    daily_summary   = models.BooleanField(default=False)
    alert_pac_status = models.BooleanField(default=False)
    monthly_report  = models.BooleanField(default=False)
    colocation_data = models.BooleanField(default=False)
    colocation_alarm = models.BooleanField(default=False)
    added_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Notification Recipient'

    def __str__(self):
        return f"{self.name} ({self.channel}: {self.contact})"



class NotificationRequest(models.Model):
    """
    Self-service notification subscription request.

    Viewer/user/admin may request Email and/or Telegram notifications.
    Nothing becomes an active NotificationRecipient until an admin approves.
    """

    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    user = models.ForeignKey(
        'auth.User',
        on_delete=models.CASCADE,
        related_name='notification_requests'
    )

    # Email defaults to the account email, but the requester may choose
    # another delivery address without changing their login/profile email.
    email_contact = models.EmailField(blank=True)
    email_alerts = models.JSONField(default=list, blank=True)

    # Telegram is populated only after matching a one-time pairing code
    # against a private message received by the configured SysMonitor bot.
    telegram_chat_id = models.CharField(max_length=100, blank=True)
    telegram_display_name = models.CharField(max_length=200, blank=True)
    telegram_verified = models.BooleanField(default=False)
    telegram_alerts = models.JSONField(default=list, blank=True)

    pairing_code = models.CharField(max_length=20, blank=True)
    pairing_created_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='DRAFT'
    )

    requested_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.CharField(max_length=100, blank=True)
    admin_note = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Notification Request'
        verbose_name_plural = 'Notification Requests'

    def __str__(self):
        return (
            f"{self.user.username} — "
            f"{self.status} — "
            f"{self.updated_at:%Y-%m-%d %H:%M}"
        )


class NotificationLog(models.Model):
    cycle_id    = models.IntegerField(null=True, blank=True)
    event_type  = models.CharField(max_length=20)
    channel     = models.CharField(max_length=20)
    recipient   = models.CharField(max_length=200)
    status      = models.CharField(max_length=10)
    error       = models.TextField(blank=True)
    sent_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']
        verbose_name = 'Notification Log'

    def __str__(self):
        return f"[{self.status}] {self.channel} → {self.recipient} ({self.event_type})"


# ── Generator Mode Log ──────────────────────────────────────────────────────────

class GeneratorModeLog(models.Model):
    """
    Manual log entries recording which generator was switched to auto mode,
    and when. Used to determine which generator was responsible for each
    outage cycle in the daily summary report.

    Example: "09:52 AM, 04-06-2026: Generator-02 is in auto mode."
    """
    GEN_CHOICES = [
        ('Gen-01', 'Generator 01'),
        ('Gen-02', 'Generator 02'),
    ]
    generator   = models.CharField(max_length=20, choices=GEN_CHOICES)
    switched_at = models.DateTimeField()  # when this generator went to auto mode
    note        = models.CharField(max_length=200, blank=True)
    added_by    = models.CharField(max_length=100, blank=True)  # username who logged it
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-switched_at']
        verbose_name = 'Generator Mode Log'

    def __str__(self):
        return f"{self.generator} auto @ {self.switched_at}"


# ── Maintenance Mode ──────────────────────────────────────────────────────────

class MaintenanceMode(models.Model):
    """
    When active, ping_monitor.py suppresses outage cycle creation and
    notifications — used to avoid false-positive outages during planned
    network maintenance (e.g. restarting the Mikrotik router).

    Only one row should ever exist meaningfully active at a time; we use
    get_or_create(id=1) pattern to keep a single row.
    """
    is_active     = models.BooleanField(default=False)
    started_at    = models.DateTimeField(null=True, blank=True)
    expires_at    = models.DateTimeField(null=True, blank=True)
    started_by    = models.CharField(max_length=100, blank=True)
    reason        = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = 'Maintenance Mode'
        verbose_name_plural = 'Maintenance Mode'

    def __str__(self):
        return f"Maintenance {'ACTIVE' if self.is_active else 'inactive'}"


class PageViewCounter(models.Model):
    """
    Single-row counter, incremented atomically on every page view.
    DB-backed (not a flat file) so a power cut mid-write can't corrupt it —
    SQLite either commits the increment or rolls it back, nothing in between.
    """
    count = models.PositiveIntegerField(default=789)

    def __str__(self):
        return f"PageViewCounter: {self.count}"


# ── Message Templates (admin-editable email/text formats) ──────────────────────

class MessageTemplate(models.Model):
    """
    Lets an admin override the wording of an outgoing notification without
    touching code. If no row exists for an event_type (or template_text is
    blank), notifications.build_message() falls back to its built-in default
    text — so this is purely optional customization.

    template_text supports {placeholder} tokens; the set available depends
    on event_type (see notifications.TEMPLATE_PLACEHOLDERS for the exact
    list shown to the admin on the edit page).
    """
    EVENT_CHOICES = [
        ('OUTAGE_START', 'Power Outage Started'),
        ('CRITICAL',      'Critical — Both Devices Down'),
        ('ALARM',         'Alarm — Abnormal Condition'),
        ('COMPLETE',      'Outage Cycle Complete'),
        ('PAC_STATUS_CHANGE', 'SMW6PAC Status Change'),
        ('SENSOR_ALERT',  'Colocation Temp/Humidity Alert'),
        ('TEST',          'Test Message'),
    ]
    event_type    = models.CharField(max_length=25, choices=EVENT_CHOICES, unique=True)
    template_text = models.TextField(blank=True,
        help_text='Leave blank to use the built-in default wording.')
    updated_at    = models.DateTimeField(auto_now=True)
    updated_by    = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = 'Message Template'

    def __str__(self):
        return f"Template: {self.get_event_type_display()}"


# ── Environmental Sensor (Tuya temperature/humidity) ────────────────────────────

class SensorReading(models.Model):
    """
    Polled reading from a Tuya-based temperature/humidity sensor placed in
    the colocation room. device_id lets more than one sensor share this
    table (front page shows the latest reading per device).
    """
    device_id    = models.CharField(max_length=64)
    device_name  = models.CharField(max_length=100, blank=True)
    temperature_c = models.FloatField(null=True, blank=True)
    humidity_pct  = models.FloatField(null=True, blank=True)
    battery_pct   = models.FloatField(null=True, blank=True)
    battery_state = models.CharField(max_length=20, blank=True)  # 'low'/'middle'/'high' — this device reports an enum, not a %
    is_online     = models.BooleanField(default=True)
    raw_status    = models.JSONField(null=True, blank=True)
    recorded_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']
        indexes = [models.Index(fields=['device_id', '-recorded_at'])]

    def __str__(self):
        return f"{self.device_name or self.device_id}: {self.temperature_c}°C / {self.humidity_pct}% @ {self.recorded_at}"


class SensorAlarmConfig(models.Model):
    """
    Admin-configurable alarm setpoints for the colocation room's
    temperature and humidity sensor.

    This is intended as a single configuration row. Blank values mean
    that the corresponding alarm limit is not configured yet.
    """
    temperature_low = models.FloatField(
        null=True,
        blank=True,
        help_text='Low temperature alarm setpoint in °C.'
    )
    temperature_high = models.FloatField(
        null=True,
        blank=True,
        help_text='High temperature alarm setpoint in °C.'
    )
    humidity_low = models.FloatField(
        null=True,
        blank=True,
        help_text='Low humidity alarm setpoint in %.'
    )
    humidity_high = models.FloatField(
        null=True,
        blank=True,
        help_text='High humidity alarm setpoint in %.'
    )
    alarm_cooldown_minutes = models.PositiveIntegerField(
        default=30,
        help_text='Minimum minutes between repeated sensor alarm notifications.'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Colocation Sensor Alarm Configuration'
        verbose_name_plural = 'Colocation Sensor Alarm Configuration'

    def __str__(self):
        return 'Colocation Sensor Alarm Setpoints'


class PacRunState(models.Model):
    """
    Tracks the last known ON/STANDBY/OFF run-state per SMW6PAC controller
    IP, so pac_monitor.py can detect a transition (and only notify on
    actual change, not on every poll).
    """
    ip           = models.CharField(max_length=20, unique=True)
    label        = models.CharField(max_length=20)  # ON / STANDBY / OFF
    changed_at   = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.ip}: {self.label}"

# ── Generator Fuel Log ────────────────────────────────────────────────────────
class GeneratorFuelLog(models.Model):
    GENERATOR_CHOICES = [
        ('Gen-01', 'Generator 01'),
        ('Gen-02', 'Generator 02'),
    ]

    generator = models.CharField(
        max_length=20,
        choices=GENERATOR_CHOICES
    )

    reading_at = models.DateTimeField()

    # Tank reading immediately BEFORE fuel is loaded.
    fuel_before_l = models.DecimalField(
        max_digits=8,
        decimal_places=2
    )

    # Tank reading immediately AFTER fuel is loaded.
    fuel_after_l = models.DecimalField(
        max_digits=8,
        decimal_places=2
    )

    note = models.CharField(
        max_length=300,
        blank=True
    )

    added_by = models.CharField(
        max_length=100,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-reading_at']

    @property
    def fuel_loaded_l(self):
        return self.fuel_after_l - self.fuel_before_l

    def __str__(self):
        return (
            f"{self.generator} — "
            f"{self.reading_at} — "
            f"{self.fuel_before_l}L → {self.fuel_after_l}L"
        )

# ── NOC CCTV / NVR Configuration ──────────────────────────────────────────────

class CCTVNVR(models.Model):
    """
    Metadata only. SysMonitor never proxies or stores CCTV video.

    The browser connects directly to the configured NVR address. The MASTER
    uses local_host while the REMOTE site uses remote_host.
    """

    name = models.CharField(
        max_length=100,
        default='NOC NVR'
    )

    local_host = models.CharField(
        max_length=255,
        help_text='Local/LAN NVR IP or hostname, e.g. 192.168.1.108'
    )

    remote_host = models.CharField(
        max_length=255,
        blank=True,
        help_text='Public/remote NVR IP or hostname. Leave blank if remote viewing is unavailable.'
    )

    WEB_SCHEME_CHOICES = [
        ('http', 'HTTP'),
        ('https', 'HTTPS'),
    ]

    web_scheme = models.CharField(
        max_length=8,
        choices=WEB_SCHEME_CHOICES,
        default='https'
    )

    web_port = models.PositiveIntegerField(
        default=443,
        help_text='NVR browser/web port.'
    )

    rtsp_port = models.PositiveIntegerField(
        default=554,
        help_text='Dahua RTSP port. Default is normally 554.'
    )

    enabled = models.BooleanField(default=True)

    note = models.CharField(
        max_length=300,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'CCTV NVR'
        verbose_name_plural = 'CCTV NVRs'

    def __str__(self):
        return self.name


class CCTVCamera(models.Model):
    """
    One selected camera/channel exposed on the NOC CCTV page.

    No credentials are stored here and no video passes through Django.
    """

    STREAM_CHOICES = [
        (0, 'Main Stream'),
        (1, 'Sub Stream'),
    ]

    nvr = models.ForeignKey(
        CCTVNVR,
        on_delete=models.CASCADE,
        related_name='cameras'
    )

    name = models.CharField(max_length=100)

    channel = models.PositiveSmallIntegerField(
        help_text='Dahua channel number. Channel numbering starts at 1.'
    )

    stream_type = models.PositiveSmallIntegerField(
        choices=STREAM_CHOICES,
        default=1,
        help_text='Sub Stream is recommended for multi-camera NOC viewing.'
    )

    location = models.CharField(
        max_length=150,
        blank=True
    )

    display_order = models.PositiveSmallIntegerField(
        default=1
    )

    enabled = models.BooleanField(default=True)

    # Optional model-specific direct browser URLs.
    # These are useful if the NVR provides HTML5/WebRTC/HLS access.
    browser_url_local = models.URLField(
        max_length=500,
        blank=True,
        help_text='Optional direct browser-compatible local live-view URL.'
    )

    browser_url_remote = models.URLField(
        max_length=500,
        blank=True,
        help_text='Optional direct browser-compatible remote live-view URL.'
    )

    note = models.CharField(
        max_length=300,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['nvr', 'channel'],
                name='unique_cctv_nvr_channel'
            ),
        ]
        verbose_name = 'CCTV Camera'
        verbose_name_plural = 'CCTV Cameras'

    def __str__(self):
        return f"{self.name} — CH{self.channel}"
