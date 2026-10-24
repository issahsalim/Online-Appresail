from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    # ... your other urls ...
    path('employee/dashboard/', views.employee_dashboard, name='employee_dashboard'),
    path('employee/fill-targets/', views.fill_targets, name='fill_targets'),
    path('employee/submit/<int:appraisal_id>/', views.submit_to_supervisor, name='submit_to_supervisor'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('supervisor/dashboard/', views.supervisor_dashboard, name='supervisor_dashboard'),
    path('supervisor/create-appraisals/', views.supervisor_create_appraisals, name='supervisor_create_appraisals'),
    path('supervisor/review/<int:appraisal_id>/', views.supervisor_review, name='supervisor_review'),
    path('supervisor/submit/<int:appraisal_id>/', views.supervisor_submit_to_hod, name='supervisor_submit_to_hod'),
    # ... other urls ...
    path('dashboard/', views.dashboard_router, name='dashboard_router'),
    
    # Make sure this is here so they can edit their OWN goals
    path('employee/fill-targets/', views.fill_targets, name='fill_targets'),
    path('employee/submit/<int:appraisal_id>/', views.submit_to_supervisor, name='submit_to_supervisor'),
    
    path('supervisor/dashboard/', views.supervisor_dashboard, name='supervisor_dashboard'),
    path('supervisor/review/<int:appraisal_id>/', views.supervisor_review, name='supervisor_review'),
    path('supervisor/submit/<int:appraisal_id>/', views.supervisor_submit_to_hod, name='supervisor_submit_to_hod'),

    path('hod/dashboard/', views.hod_dashboard, name='hod_dashboard'),
    path('hod/review/<int:appraisal_id>/', views.hod_review, name='hod_review'),

        path('hr/dashboard/', views.hr_dashboard, name='hr_dashboard'),
    path('hr/approve/<int:appraisal_id>/', views.hr_approve, name='hr_approve'),
]

