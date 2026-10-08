from django.contrib import admin

from .models import (
    ShiftHandoverContact,
    ShiftImportantIssue,
    ShiftReport,
    ShiftReportConfig,
)


@admin.register(ShiftReportConfig)
class ShiftReportConfigAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'manager_email',
        'updated_by',
        'updated_at',
    )


@admin.register(ShiftHandoverContact)
class ShiftHandoverContactAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'designation',
        'email',
        'is_active',
        'display_order',
    )
    list_filter = ('is_active',)
    search_fields = ('name', 'designation', 'email')


@admin.register(ShiftReport)
class ShiftReportAdmin(admin.ModelAdmin):
    list_display = (
        'report_date',
        'shift',
        'prepared_by',
        'handover_to',
        'status',
        'sent_at',
    )
    list_filter = ('shift', 'status', 'report_date')
    search_fields = (
        'prepared_by__username',
        'handover_to__name',
        'handover_to__email',
    )


@admin.register(ShiftImportantIssue)
class ShiftImportantIssueAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'status',
        'opened_at',
        'resolved_at',
        'added_by',
    )
    list_filter = ('status',)
    search_fields = ('title', 'description')
