from django.conf import settings
from django.db import models


class ShiftReportConfig(models.Model):
    """
    MASTER-owned Shift Report configuration.

    One row (pk=1) is expected. Email addresses are configurable instead of
    hard-coded so NOC staff changes do not require a code deployment.
    """

    manager_email = models.EmailField(
        blank=True,
        help_text='Mandatory To recipient for every normal shift report.',
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
        help_text='Mandatory CC recipients for the MNOC night-shift email.',
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

    # Operator-written sections.
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

    title = models.CharField(max_length=200)
    description = models.TextField()
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
