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

class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('LOGIN',         'Login'),
        ('LOGOUT',        'Logout'),
        ('LOGIN_FAILED',  'Login Failed'),
        ('USER_CREATED',  'User Created'),
        ('USER_EDITED',   'User Edited'),
        ('USER_DELETED',  'User Deleted'),
        ('DEVICE_ADDED',  'Device Added'),
        ('DEVICE_EDITED', 'Device Edited'),
        ('DEVICE_DELETED','Device Deleted'),
    ]
    user       = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                   null=True, blank=True)
    action     = models.CharField(max_length=20, choices=ACTION_CHOICES)
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
    added_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Notification Recipient'

    def __str__(self):
        return f"{self.name} ({self.channel}: {self.contact})"


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
