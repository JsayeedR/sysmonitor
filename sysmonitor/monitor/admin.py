from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from .models import Device, DeviceStatus, Event, SystemStatus, UserProfile, ActivityLog, OutageCycle, SensorAlarmConfig

@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display  = ('name', 'ip_address', 'description', 'is_active', 'added_at')
    list_filter   = ('is_active',)
    search_fields = ('name', 'ip_address')
    list_editable = ('is_active',)


@admin.register(DeviceStatus)
class DeviceStatusAdmin(admin.ModelAdmin):
    list_display  = ('device', 'status', 'response_ms', 'checked_at')
    list_filter   = ('status', 'device')
    search_fields = ('device__name',)
    readonly_fields = ('checked_at',)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display  = ('level', 'device', 'message', 'created_at')
    list_filter   = ('level', 'device')
    search_fields = ('message',)
    readonly_fields = ('created_at',)


@admin.register(SystemStatus)
class SystemStatusAdmin(admin.ModelAdmin):
    list_display  = ('status', 'note', 'updated_at')
    readonly_fields = ('updated_at',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ('user', 'role')
    list_filter   = ('role',)
    search_fields = ('user__username',)

@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display  = ('user', 'action', 'detail', 'ip_address', 'timestamp')
    list_filter   = ('action',)
    search_fields = ('user__username', 'detail')
    readonly_fields = ('timestamp',)

@admin.register(OutageCycle)
class OutageCycleAdmin(admin.ModelAdmin):
    list_display  = ('cycle_type', 'outage_start', 'pdb_restored',
                     'cycle_end', 'pdb_duration_sec', 'gen_runtime_sec', 'is_complete')
    list_filter   = ('cycle_type', 'is_complete')
    readonly_fields = ('created_at', 'updated_at')

admin.site.register(SensorAlarmConfig)


# Stage 2 protection: Django Admin cannot bypass custom deletion safeguards.
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

@admin.register(User)
class HistoricalSafeUserAdmin(UserAdmin):
    actions = None

    def has_delete_permission(self, request, obj=None):
        return False

    def delete_model(self, request, obj):
        raise PermissionError('User deletion disabled until Stage 2 migration.')

    def delete_queryset(self, request, queryset):
        raise PermissionError('Bulk user deletion disabled until Stage 2 migration.')
