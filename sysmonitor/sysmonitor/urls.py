from django.contrib import admin
from django.urls import path
from monitor import views

urlpatterns = [
    path('admin/',                            admin.site.urls),
    path('',                                  views.dashboard,         name='dashboard'),
    path('login/',                            views.login_view,        name='login'),
    path('about/',                            views.about_view,        name='about'),
    path('logout/',                           views.logout_view,       name='logout'),
    path('api/status/',                       views.api_status,        name='api_status'),
    path('api/daily-summary/',               views.api_daily_summary, name='api_daily_summary'),
    path('api/report/',                       views.api_report,        name='api_report'),
    path('users/',                            views.user_list,         name='user_list'),
    path('users/create/',                     views.user_create,       name='user_create'),
    path('users/<int:user_id>/edit/',         views.user_edit,         name='user_edit'),
    path('users/<int:user_id>/delete/',       views.user_delete,       name='user_delete'),
    path('devices/',                          views.device_list,       name='device_list'),
    path('devices/add/',                      views.device_create,     name='device_create'),
    path('devices/<int:device_id>/edit/',     views.device_edit,       name='device_edit'),
    path('devices/<int:device_id>/delete/',   views.device_delete,     name='device_delete'),
    path('events/',                           views.event_log,         name='event_log'),
    path('activity/',                         views.activity_log,      name='activity_log'),
    path('report/',                           views.report_view,       name='report'),
    path('uptime/',                       views.uptime_status,      name='uptime_status'),
    path('uptime/<int:monitor_id>/log/',  views.uptime_status_log,  name='uptime_status_log'),
    path('smw6pac/',                      views.pac_status_view,   name='pac_status'),
]

# Notification routes (appended)
urlpatterns += [
    path('notifications/',                                    views.notifications_page,       name='notifications'),
    path('notifications/gateway/save/',                       views.notif_gateway_save,       name='notif_gateway_save'),
    path('notifications/gateway/test/',                       views.notif_gateway_test,       name='notif_gateway_test'),
    path('notifications/gateway/telegram-chats/',             views.notif_telegram_chats,     name='notif_telegram_chats'),
    path('notifications/recipient/add/',                      views.notif_recipient_add,      name='notif_recipient_add'),
    path('notifications/recipient/<int:rid>/edit/',           views.notif_recipient_edit,     name='notif_recipient_edit'),
    path('notifications/recipient/<int:rid>/delete/',         views.notif_recipient_delete,   name='notif_recipient_delete'),
    path('notifications/recipient/<int:rid>/test/',           views.notif_recipient_test,     name='notif_recipient_test'),
    path('notifications/recipient/<int:rid>/toggle/',         views.notif_recipient_toggle,   name='notif_recipient_toggle'),
    path('notifications/log/',                                views.notif_log,                name='notif_log'),
    path('notifications/whatsapp-health/',                     views.notif_whatsapp_health,    name='notif_whatsapp_health'),
    path('generator-log/',                                     views.generator_log_page,       name='generator_log_page'),
    path('generator-log/add/',                                 views.generator_log_add,        name='generator_log_add'),
    path('generator-log/<int:eid>/edit/',                      views.generator_log_edit,       name='generator_log_edit'),
    path('generator-log/<int:eid>/delete/',                    views.generator_log_delete,     name='generator_log_delete'),
    path('maintenance/status/',                                views.maintenance_status,       name='maintenance_status'),
    path('maintenance/start/',                                  views.maintenance_start,        name='maintenance_start'),
    path('maintenance/stop/',                                   views.maintenance_stop,         name='maintenance_stop'),
    path('profile/',                                            views.profile_view,             name='profile_view'),
    path('profile/edit/',                                       views.profile_edit,             name='profile_edit'),
    path('profile/edit/save/',                                  views.profile_edit_save,        name='profile_edit_save'),
    path('profile/password/',                                   views.profile_password,         name='profile_password'),
    path('profile/password/save/',                              views.profile_password_save,    name='profile_password_save'),
    path('profile/pending-changes/',                            views.profile_pending_changes,  name='profile_pending_changes'),
    path('profile/pending-changes/<int:pid>/approve/',          views.profile_change_approve,   name='profile_change_approve'),
    path('profile/pending-changes/<int:pid>/reject/',           views.profile_change_reject,    name='profile_change_reject'),
    path('system/',                         views.system_tools,          name='system_tools'),
    path('system/state/',                    views.system_live_state,     name='system_live_state'),
    path('system/journal/',                  views.system_journal,        name='system_journal'),
    path('system/cycle/<int:cid>/action/',   views.system_cycle_action,   name='system_cycle_action'),
    path('system/manual-cycle/add/',         views.system_manual_cycle_add, name='system_manual_cycle_add'),
    path('system/restart-ping/',             views.system_restart_ping,   name='system_restart_ping'),
    path('system/recent-cycles/',              views.system_recent_cycles,  name='system_recent_cycles'),
]

from django.conf import settings
from django.conf.urls.static import static
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

