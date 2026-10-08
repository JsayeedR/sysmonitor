from django.conf import settings
from django.db import models


class ShiftReportConfig(models.Model):
    """
    MASTER-owned Shift Report configuration.

    One row (pk=1) is expected. Email addresses are configurable instead of
    hard-coded so NOC staff changes do not require a code deployment.
    """

    # Shared NOC Microsoft mailbox used by every Shift Engineer.
    noc_from_name = models.CharField(
        max_length=150,
        blank=True,
        default='SMW4 NOC (COXCLS)',
    )

    noc_from_email = models.EmailField(
        blank=True,
        help_text='Fixed From address for Shift Report and MNOC PFE email.',
    )

    # Shift Report has its own shared SMTP authentication.
    # This avoids individual mailbox configuration for every engineer.
    smtp_host = models.CharField(
        max_length=150,
        blank=True,
        default='smtp.office365.com',
    )

    smtp_port = models.PositiveIntegerField(
        default=587,
    )

    smtp_username = models.CharField(
        max_length=200,
        blank=True,
    )

    smtp_password = models.CharField(
        max_length=300,
        blank=True,
    )

    smtp_use_tls = models.BooleanField(
        default=True,
    )

    mandatory_to = models.TextField(
        blank=True,
        help_text=(
            'Fixed mandatory TO addresses, one per line or comma-separated.'
        ),
    )

    # Kept so any already-entered manager address is not lost.
    manager_email = models.EmailField(
        blank=True,
        help_text='Legacy manager mandatory TO address.',
    )

    mandatory_cc = models.TextField(
        blank=True,
        help_text=(
            'Mandatory CC addresses, one per line or comma-separated. '
            'Report writers may add CC addresses but cannot remove these.'
        ),
    )

    mnoc_to = models.TextField(
        blank=True,
        help_text=(
            'Recipients for the separate MNOC night-shift email, '
            'one per line or comma-separated.'
        ),
    )

    mnoc_cc = models.TextField(
        blank=True,
        help_text='Mandatory CC recipients for the MNOC PFE email.',
    )

    noc_mobile = models.CharField(
        max_length=100,
        blank=True,
    )

    company_name = models.CharField(
        max_length=200,
        blank=True,
        default='Bangladesh Submarine Cables PLC. (BSCPLC)',
    )

    website = models.CharField(
        max_length=200,
        blank=True,
        default='www.bscplc.gov.bd',
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=150, blank=True)

    class Meta:
        verbose_name = 'Shift Report Configuration'
        verbose_name_plural = 'Shift Report Configuration'

    def __str__(self):
        return 'Shift Report Configuration'


class ShiftHandoverContact(models.Model):
    """
    Approved handover recipient selectable by a shift operator.

    This can optionally link to a SysMonitor user, but email/name remain
    explicit so operational contacts are not dependent on login accounts.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='shift_handover_contacts',
    )

    name = models.CharField(max_length=150)
    designation = models.CharField(max_length=150, blank=True)
    email = models.EmailField()
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Shift Handover Contact'
        verbose_name_plural = 'Shift Handover Contacts'

    def __str__(self):
        return f'{self.name} — {self.email}'


class ShiftReport(models.Model):
    SHIFT_CHOICES = [
        ('MORNING', 'Morning Shift'),
        ('EVENING', 'Evening Shift'),
        ('NIGHT', 'Night Shift'),
    ]

    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('SUBMITTED', 'Submitted'),
        ('SENT', 'Sent'),
        ('FAILED', 'Email Failed'),
    ]

    report_date = models.DateField(
        help_text='Operational date on which this shift started.'
    )

    shift = models.CharField(
        max_length=10,
        choices=SHIFT_CHOICES,
    )

    shift_start = models.DateTimeField()
    shift_end = models.DateTimeField()

    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='prepared_shift_reports',
    )

    handover_to = models.ForeignKey(
        ShiftHandoverContact,
        on_delete=models.PROTECT,
        related_name='received_shift_reports',
    )

    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default='DRAFT',
    )

    # ---------------------------------------------------------
    # Section B — Regular Shift Activities
    # These fields map directly to the operational email/report format.
    # ---------------------------------------------------------
    ac_shifting = models.TextField(
        blank=True,
        default='Shifting of active AC as per rotational turn was performed.',
    )

    network_status = models.CharField(
        max_length=100,
        blank=True,
        default='Normal',
    )

    cable_status = models.CharField(
        max_length=100,
        blank=True,
        default='Normal',
    )

    pfe_status = models.CharField(
        max_length=200,
        blank=True,
        default='',
    )

    dwdm_status = models.CharField(
        max_length=100,
        blank=True,
        default='Normal',
    )

    maintenance_activity = models.TextField(
        blank=True,
        default='None',
    )

    generator_status_text = models.TextField(
        blank=True,
    )

    rain_water_leakage = models.TextField(
        blank=True,
        default='No significant rain was observed',
    )

    bandwidth_status = models.TextField(
        blank=True,
        default=(
            'Total Capacity: 4,650 Gbps\n'
            'Used Capacity: 2413.875 Gbps (51.91%) '
            '(Currently Carrying Traffic)\n'
            'Free Capacity: 2,236.125 Gbps (48.09%)\n'
            'Assigned Capacity: Approx. 2,725 Gbps '
            '(Ready for service)\n'
            'Total Active Circuits: 80\n'
            '100G: 19\n'
            '10G: 51\n'
            'STM-16: 1\n'
            'STM-1: 9'
        ),
    )

    # Legacy/free-form fields retained for existing records.
    # New composer uses the structured fields above and Section C rows below.
    regular_activities = models.TextField(blank=True)
    issues_observed = models.TextField(blank=True)
    pending_handover = models.TextField(blank=True)
    important_notes = models.TextField(blank=True)

    # Optional extra CC entered by report writer.
    additional_cc = models.TextField(blank=True)

    # Snapshot data captured when report is prepared/submitted.
    outage_summary = models.JSONField(default=dict, blank=True)
    generator_summary = models.JSONField(default=dict, blank=True)
    sensor_summary = models.JSONField(default=dict, blank=True)

    # Night shift specific snapshot.
    previous_day_summary = models.JSONField(default=dict, blank=True)
    mnoc_notes = models.TextField(blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    # Individual delivery timestamps make Night Shift retries safe:
    # if the normal email succeeds but MNOC fails, retry only MNOC.
    main_email_sent_at = models.DateTimeField(null=True, blank=True)
    mnoc_email_sent_at = models.DateTimeField(null=True, blank=True)

    email_subject = models.CharField(max_length=300, blank=True)
    email_to = models.JSONField(default=list, blank=True)
    email_cc = models.JSONField(default=list, blank=True)
    email_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-shift_start', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['report_date', 'shift'],
                name='unique_shift_report_date_shift',
            ),
        ]

    def __str__(self):
        return (
            f'{self.report_date} — '
            f'{self.get_shift_display()} — '
            f'{self.prepared_by.username}'
        )


class ShiftImportantIssue(models.Model):
    """
    Historical important issues register.

    These records can later be exported into the XLSX attachment and selected
    into a shift report without rewriting old issue history.
    """

    STATUS_CHOICES = [
        ('OPEN', 'Open'),
        ('MONITORING', 'Monitoring'),
        ('RESOLVED', 'Resolved'),
    ]

    # Columns matching the existing Historical Important Issues workbook.
    category_1 = models.CharField(
        max_length=150,
        blank=True,
    )

    category_2 = models.CharField(
        max_length=200,
        blank=True,
    )

    title = models.CharField(
        max_length=200,
        blank=True,
    )

    description = models.TextField(
        blank=True,
    )

    remarks = models.TextField(
        blank=True,
    )

    event_type = models.CharField(
        max_length=100,
        blank=True,
    )
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default='OPEN',
    )

    opened_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='shift_important_issues',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-opened_at', '-id']

    def __str__(self):
        return self.title


class MnocPfeReport(models.Model):
    """
    Daily MNOC PFE reading prepared after Night Shift.

    All Shift Engineers may view history.
    Only the creator (or Admin) may edit an unsent draft.
    Only one report is allowed per calendar date.
    """

    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('SENT', 'Sent'),
        ('FAILED', 'Email Failed'),
    ]

    MODE_CHOICES = [
        ('CURRENT', 'CURRENT'),
        ('VOLTAGE', 'VOLTAGE'),
        ('OTHER', 'OTHER'),
    ]

    report_date = models.DateField(
        unique=True,
    )

    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='prepared_mnoc_pfe_reports',
    )

    voltage_v = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    current_ma = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default='CURRENT',
    )

    remark = models.CharField(
        max_length=100,
        default='OK',
    )

    alarm_status = models.CharField(
        max_length=200,
        default='None',
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='DRAFT',
    )

    sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    email_subject = models.CharField(
        max_length=300,
        blank=True,
    )

    email_to = models.JSONField(
        default=list,
        blank=True,
    )

    email_cc = models.JSONField(
        default=list,
        blank=True,
    )

    email_error = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ['-report_date', '-id']
        verbose_name = 'MNOC PFE Report'
        verbose_name_plural = 'MNOC PFE Reports'

    def __str__(self):
        return (
            f'{self.report_date} — '
            f'{self.voltage_v} V / {self.current_ma} mA'
        )



class ShiftReportActivity(models.Model):
    """
    Section C — Shift Activities and the Issues.

    One Shift Report can contain any number of rows matching the
    operational email table:
      Type | Client/Vendor | Details | Status | Remarks
    """

    report = models.ForeignKey(
        ShiftReport,
        on_delete=models.CASCADE,
        related_name='activity_rows',
    )

    activity_type = models.CharField(
        max_length=200,
        blank=True,
    )

    client_vendor = models.CharField(
        max_length=250,
        blank=True,
    )

    details = models.TextField(
        blank=True,
    )

    status = models.CharField(
        max_length=150,
        blank=True,
    )

    remarks = models.TextField(
        blank=True,
    )

    display_order = models.PositiveIntegerField(
        default=100,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            'display_order',
            'id',
        ]

    def __str__(self):
        return (
            f'{self.report} — '
            f'{self.activity_type or "Activity"}'
        )
