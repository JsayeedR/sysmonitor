from django.urls import path

from . import views


app_name = 'shiftreport'

urlpatterns = [
    path(
        '',
        views.shift_report_home,
        name='home',
    ),
    path(
        'create/',
        views.shift_report_create,
        name='create',
    ),
    path(
        '<int:report_id>/',
        views.shift_report_edit,
        name='edit',
    ),
    path(
        '<int:report_id>/save/',
        views.shift_report_save,
        name='save',
    ),
    path(
        '<int:report_id>/refresh-summary/',
        views.shift_report_refresh_summary,
        name='refresh_summary',
    ),

    path(
        '<int:report_id>/send/',
        views.shift_report_send,
        name='send',
    ),
    path(
        'issues/add/',
        views.shift_issue_add,
        name='issue_add',
    ),
    path(
        'issues/<int:issue_id>/update/',
        views.shift_issue_update,
        name='issue_update',
    ),

    path(
        'config/settings/',
        views.shift_report_config,
        name='config',
    ),
    path(
        'config/settings/save/',
        views.shift_report_config_save,
        name='config_save',
    ),
    path(
        'config/handover/add/',
        views.shift_handover_add,
        name='handover_add',
    ),
    path(
        'config/handover/<int:contact_id>/toggle/',
        views.shift_handover_toggle,
        name='handover_toggle',
    ),
]
