from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    
    path('password/change/', views.ChangePasswordView.as_view(), name='change_password'),
    path('password/reset/', views.PasswordResetView.as_view(), name='password_reset'),
    path('password/reset/<str:token>/', views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('profile/update/', views.ProfileUpdateView.as_view(), name='profile_update'),
    
    path('employees/', views.EmployeeListView.as_view(), name='employee_list'),
    path('employees/create/', views.EmployeeCreateView.as_view(), name='employee_create'),
    path('employees/<int:pk>/', views.EmployeeDetailView.as_view(), name='employee_detail'),
    path('employees/<int:pk>/update/', views.EmployeeUpdateView.as_view(), name='employee_update'),
    path('employees/bulk-upload/', views.BulkEmployeeUploadView.as_view(), name='bulk_upload'),
    path('employees/export/', views.ExportEmployeesView.as_view(), name='export_employees'),
    
    path('departments/', views.DepartmentListView.as_view(), name='department_list'),
    path('departments/create/', views.DepartmentCreateView.as_view(), name='department_create'),
    
    path('roles/', views.RoleListView.as_view(), name='role_list'),
    path('roles/create/', views.RoleCreateView.as_view(), name='role_create'),
    
    path('sessions/', views.UserSessionsView.as_view(), name='user_sessions'),
    path('sessions/<int:session_id>/terminate/', views.TerminateSessionView.as_view(), name='terminate_session'),
    
    path('audit-logs/', views.AuditLogView.as_view(), name='audit_logs'),
    
    path('settings/', views.SystemSettingsView.as_view(), name='system_settings'),
    path('statistics/', views.SystemStatsView.as_view(), name='system_stats'),
    
    path('ajax/search-employees/', views.search_employees_ajax, name='search_employees_ajax'),
    path('ajax/check-employee-code/', views.check_employee_code, name='check_employee_code'),
    path('ajax/bulk-action/', views.bulk_user_action, name='bulk_user_action'),
    path('ajax/send-notification/', views.send_notification, name='send_notification'),
    path('ajax/generate-report/', views.generate_report, name='generate_report'),
    
    path('health/', views.health_check, name='health_check'),
]

handler404 = 'accounts.views.handler404'
handler500 = 'accounts.views.handler500'
handler403 = 'accounts.views.handler403'
