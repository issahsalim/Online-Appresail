from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.home, name='home'),
    # ... your other urls ...
    path('employee/dashboard/', views.employee_dashboard, name='employee_dashboard'),
    path('employee/fill-targets/', views.fill_targets, name='fill_targets'),
    path('employee/fill-targets/<int:year>/', views.fill_targets, name='fill_targets_year'),
    path('employee/appraisal/<int:appraisal_id>/', views.employee_appraisal_detail, name='employee_appraisal_detail'),
    path('employee/appraisal/<int:appraisal_id>/pdf/', views.employee_appraisal_pdf, name='employee_appraisal_pdf'),
    path('employee/submit/<int:appraisal_id>/', views.submit_to_supervisor, name='submit_to_supervisor'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('profile/', views.profile, name='profile'),
    path('profile/password/', views.change_password, name='password_change'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/<int:notification_id>/open/', views.open_notification, name='open_notification'),
    path('delete_noti/<int:noti_id>/',views.delete_noti, name='delete_noti'), 
    path('notifications/read-all/', views.mark_all_notifications_read, name='mark_all_notifications_read'),
    path('supervisor/dashboard/', views.supervisor_dashboard, name='supervisor_dashboard'),
    path('supervisor/create-appraisals/', views.supervisor_create_appraisals, name='supervisor_create_appraisals'),
    path('supervisor/review/<int:appraisal_id>/', views.supervisor_review, name='supervisor_review'),
    path('supervisor/appraisal/<int:appraisal_id>/', views.supervisor_view_appraisal, name='supervisor_view_appraisal'),
    path('supervisor/submit/<int:appraisal_id>/', views.supervisor_submit_to_hod, name='supervisor_submit_to_hod'),
    path('appraisal/<int:appraisal_id>/return/', views.return_appraisal, name='return_appraisal'),
    # ... other urls ...
    path('dashboard/', views.dashboard_router, name='dashboard_router'),
    
    path('supervisor/dashboard/', views.supervisor_dashboard, name='supervisor_dashboard'),
    path('supervisor/review/<int:appraisal_id>/', views.supervisor_review, name='supervisor_review'),
    path('supervisor/submit/<int:appraisal_id>/', views.supervisor_submit_to_hod, name='supervisor_submit_to_hod'),

    path('hod/dashboard/', views.hod_dashboard, name='hod_dashboard'),
    path('hod/review/<int:appraisal_id>/', views.hod_review, name='hod_review'),
    path('hod/view/<int:appraisal_id>/', views.hod_view_appraisal, name='hod_view_appraisal'),

        path('hr/dashboard/', views.hr_dashboard, name='hr_dashboard'),
        path('hr/supervisor-assignments/', views.hr_supervisor_assignments, name='hr_supervisor_assignments'),
        path('hr/supervisor-assignment-options/', views.hr_assignment_options, name='hr_assignment_options'),
        path('hr/supervisor-assignments/search/', views.hr_search_supervisor_assignments, name='hr_search_supervisor_assignments'),
        path('hr/completed-appraisals/search/', views.hr_search_completed_appraisals, name='hr_search_completed_appraisals'),
        path('hr/completed-appraisals/<int:appraisal_id>/', views.hr_completed_appraisal, name='hr_completed_appraisal'),
        path('hr/approve/<int:appraisal_id>/', views.hr_approve, name='hr_approve'),
        path('hr/new_app_cycle/', views.newcycle, name='new_app_cycle'),
        path('hr/new_app_cycle/<int:cycle_id>/status/', views.set_appraisal_cycle_status, name='set_appraisal_cycle_status'),
        path('hr/new_app_cycle/<int:cycle_id>/delete/', views.delete_appraisal_cycle, name='delete_appraisal_cycle'),
            
]
