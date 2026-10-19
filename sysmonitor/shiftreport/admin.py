from django.contrib import admin

from .models import (
    ShiftHandoverContact,
    ShiftImportantIssue,
    ShiftReport,
    ShiftReportConfig,
    MnocPfeReport,
    ShiftReportActivity,
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


@admin.register(MnocPfeReport)
class MnocPfeReportAdmin(admin.ModelAdmin):
    list_display = (
        'report_date',
        'prepared_by',
        'voltage_v',
        'current_ma',
        'mode',
        'remark',
        'alarm_status',
        'status',
        'sent_at',
    )

    list_filter = (
        'status',
        'mode',
        'report_date',
    )

    search_fields = (
        'prepared_by__username',
        'prepared_by__first_name',
        'prepared_by__last_name',
        'remark',
        'alarm_status',
    )



@admin.register(ShiftReportActivity)
class ShiftReportActivityAdmin(admin.ModelAdmin):
    list_display = (
        'report',
        'activity_type',
        'client_vendor',
        'status',
        'display_order',
    )

    search_fields = (
        'activity_type',
        'client_vendor',
        'details',
        'status',
        'remarks',
    )

# A14.3: centrally managed activated WET circuit counts.
from django.contrib import admin as _a143_admin
from .models import WetCircuitConfig as _A143WetCircuitConfig

@_a143_admin.register(_A143WetCircuitConfig)
class _A143WetCircuitConfigAdmin(_a143_admin.ModelAdmin):
    list_display = ('gbe100', 'ge10', 'stm16', 'stm1', 'updated_at', 'updated_by')
    readonly_fields = ('updated_at', 'updated_by')

    def has_add_permission(self, request):
        return request.user.is_superuser and not _A143WetCircuitConfig.objects.exists()

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user.get_username()
        super().save_model(request, obj, form, change)
