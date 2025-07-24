from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth import get_user_model
from django.utils.html import format_html
from django.urls import reverse, path
from django.utils import timezone
from django.db.models import Count, Q
from django.contrib.admin.models import LogEntry
from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db import transaction

from .models import Department, Role, UserSession, PasswordResetToken, AuditLog, SystemConfiguration
from .forms import EmployeeRegistrationForm, EmployeeUpdateForm
from .utils import generate_secure_password, SystemUtilities

User = get_user_model()


class BaseAdminMixin:
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return self.optimize_queryset(queryset)
    
    def optimize_queryset(self, queryset):
        return queryset
    
    def safe_bulk_action(self, request, queryset, action_func, success_message):
        try:
            with transaction.atomic():
                count = action_func(queryset)
                self.message_user(request, success_message.format(count=count))
        except Exception as e:
            self.message_user(request, f"Action failed: {str(e)}", level=messages.ERROR)


@admin.register(User)
class CustomUserAdmin(BaseAdminMixin, BaseUserAdmin):
    add_form = EmployeeRegistrationForm
    form = EmployeeUpdateForm
    model = User
    
    list_display = [
        'employee_code', 'get_full_name', 'email', 'department', 'role',
        'status', 'is_active', 'last_login', 'created_at'
    ]
    
    list_filter = [
        'status', 'is_active', 'is_verified', 'gender', 'department', 'role',
        'created_at', 'last_login', 'hire_date'
    ]
    
    search_fields = [
        'employee_code', 'first_name', 'last_name', 'email',
        'phone_number', 'job_title'
    ]
    
    ordering = ['employee_code']
    
    readonly_fields = [
        'employee_code', 'created_at', 'updated_at', 'last_login',
        'last_login_ip', 'failed_login_attempts', 'account_locked_until',
        'password_changed_at', 'created_by'
    ]
    
    fieldsets = (
        ('Basic Information', {
            'fields': (
                'employee_code', 'first_name', 'last_name', 'middle_name',
                'email', 'phone_number', 'date_of_birth', 'gender'
            )
        }),
        ('Address Information', {
            'fields': (
                'address_line1', 'address_line2', 'city', 'state',
                'postal_code', 'country'
            ),
            'classes': ('collapse',)
        }),
        ('Emergency Contact', {
            'fields': (
                'emergency_contact_name', 'emergency_contact_phone',
                'emergency_contact_relationship'
            ),
            'classes': ('collapse',)
        }),
        ('Employment Information', {
            'fields': (
                'department', 'role', 'job_title', 'manager',
                'hire_date', 'termination_date', 'status'
            )
        }),
        ('Account Status', {
            'fields': (
                'is_active', 'is_verified', 'must_change_password'
            )
        }),
        ('Security Information', {
            'fields': (
                'last_login', 'last_login_ip', 'failed_login_attempts',
                'account_locked_until', 'password_changed_at'
            ),
            'classes': ('collapse',)
        }),
        ('System Information', {
            'fields': (
                'created_at', 'updated_at', 'created_by'
            ),
            'classes': ('collapse',)
        })
    )
    
    add_fieldsets = (
        ('Basic Information', {
            'fields': (
                'employee_code', 'first_name', 'last_name', 'middle_name',
                'email', 'phone_number', 'date_of_birth', 'gender'
            )
        }),
        ('Employment Information', {
            'fields': (
                'department', 'role', 'job_title', 'manager', 'hire_date'
            )
        }),
        ('Password', {
            'fields': ('password1', 'password2')
        })
    )
    
    actions = [
        'activate_users', 'deactivate_users', 'suspend_users',
        'reset_passwords', 'unlock_accounts', 'verify_users'
    ]
    
    def optimize_queryset(self, queryset):
        return queryset.select_related('department', 'role', 'manager', 'created_by')
    
    def get_full_name(self, obj):
        return obj.get_full_name()
    get_full_name.short_description = 'Full Name'
    get_full_name.admin_order_field = 'first_name'
    
    def activate_users(self, request, queryset):
        def action(qs):
            return qs.update(status='ACTIVE', is_active=True)
        self.safe_bulk_action(request, queryset, action, '{count} users activated successfully.')
    activate_users.short_description = 'Activate selected users'
    
    def deactivate_users(self, request, queryset):
        def action(qs):
            return qs.update(status='INACTIVE', is_active=False)
        self.safe_bulk_action(request, queryset, action, '{count} users deactivated successfully.')
    deactivate_users.short_description = 'Deactivate selected users'
    
    def suspend_users(self, request, queryset):
        def action(qs):
            return qs.update(status='SUSPENDED')
        self.safe_bulk_action(request, queryset, action, '{count} users suspended successfully.')
    suspend_users.short_description = 'Suspend selected users'
    
    def reset_passwords(self, request, queryset):
        def action(qs):
            return qs.update(must_change_password=True)
        self.safe_bulk_action(request, queryset, action, '{count} users marked for password reset.')
    reset_passwords.short_description = 'Force password reset for selected users'
    
    def unlock_accounts(self, request, queryset):
        def action(qs):
            return qs.update(failed_login_attempts=0, account_locked_until=None)
        self.safe_bulk_action(request, queryset, action, '{count} accounts unlocked successfully.')
    unlock_accounts.short_description = 'Unlock selected accounts'
    
    def verify_users(self, request, queryset):
        def action(qs):
            return qs.update(is_verified=True)
        self.safe_bulk_action(request, queryset, action, '{count} users verified successfully.')
    verify_users.short_description = 'Verify selected users'


@admin.register(Department)
class DepartmentAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        'code', 'name', 'manager', 'parent_department',
        'employee_count', 'is_active', 'created_at'
    ]
    
    list_filter = ['is_active', 'created_at', 'parent_department']
    
    search_fields = ['code', 'name', 'description']
    
    ordering = ['code']
    
    readonly_fields = ['created_at', 'updated_at', 'created_by']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('code', 'name', 'description')
        }),
        ('Hierarchy', {
            'fields': ('manager', 'parent_department')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('System Information', {
            'fields': ('created_at', 'updated_at', 'created_by'),
            'classes': ('collapse',)
        })
    )
    
    actions = ['activate_departments', 'deactivate_departments']
    
    def optimize_queryset(self, queryset):
        return queryset.select_related('manager', 'parent_department', 'created_by').annotate(
            active_employee_count=Count('employees', filter=Q(employees__is_active=True))
        )
    
    def employee_count(self, obj):
        return getattr(obj, 'active_employee_count', obj.employees.filter(is_active=True).count())
    employee_count.short_description = 'Active Employees'
    
    def activate_departments(self, request, queryset):
        def action(qs):
            return qs.update(is_active=True)
        self.safe_bulk_action(request, queryset, action, '{count} departments activated successfully.')
    activate_departments.short_description = 'Activate selected departments'
    
    def deactivate_departments(self, request, queryset):
        def action(qs):
            return qs.update(is_active=False)
        self.safe_bulk_action(request, queryset, action, '{count} departments deactivated successfully.')
    deactivate_departments.short_description = 'Deactivate selected departments'


@admin.register(Role)
class RoleAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        'name', 'display_name', 'user_count', 'permission_count',
        'is_active', 'created_at'
    ]
    
    list_filter = ['is_active', 'created_at', 'name']
    
    search_fields = ['name', 'display_name', 'description']
    
    ordering = ['display_name']
    
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'display_name', 'description')
        }),
        ('Permissions', {
            'fields': ('permissions',)
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('System Information', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    filter_horizontal = ['permissions']
    
    actions = ['activate_roles', 'deactivate_roles']
    
    def optimize_queryset(self, queryset):
        return queryset.prefetch_related('permissions').annotate(
            active_user_count=Count('users', filter=Q(users__is_active=True)),
            total_permissions=Count('permissions')
        )
    
    def user_count(self, obj):
        return getattr(obj, 'active_user_count', obj.users.filter(is_active=True).count())
    user_count.short_description = 'Active Users'
    
    def permission_count(self, obj):
        return getattr(obj, 'total_permissions', obj.permissions.count())
    permission_count.short_description = 'Permissions'
    
    def activate_roles(self, request, queryset):
        def action(qs):
            return qs.update(is_active=True)
        self.safe_bulk_action(request, queryset, action, '{count} roles activated successfully.')
    activate_roles.short_description = 'Activate selected roles'
    
    def deactivate_roles(self, request, queryset):
        def action(qs):
            return qs.update(is_active=False)
        self.safe_bulk_action(request, queryset, action, '{count} roles deactivated successfully.')
    deactivate_roles.short_description = 'Deactivate selected roles'


@admin.register(UserSession)
class UserSessionAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        'user', 'get_employee_code', 'ip_address', 'login_time',
        'last_activity', 'is_active', 'session_duration'
    ]
    
    list_filter = ['is_active', 'login_time', 'last_activity']
    
    search_fields = [
        'user__employee_code', 'user__first_name', 'user__last_name',
        'ip_address'
    ]
    
    ordering = ['-login_time']
    
    readonly_fields = [
        'session_key_hash', 'login_time', 'last_activity', 'logout_time'
    ]
    
    fieldsets = (
        ('Session Information', {
            'fields': (
                'user', 'session_key_hash', 'ip_address', 'user_agent'
            )
        }),
        ('Timing', {
            'fields': (
                'login_time', 'last_activity', 'logout_time', 'is_active'
            )
        })
    )
    
    actions = ['terminate_sessions']
    
    def optimize_queryset(self, queryset):
        return queryset.select_related('user')
    
    def get_employee_code(self, obj):
        return obj.user.employee_code
    get_employee_code.short_description = 'Employee Code'
    get_employee_code.admin_order_field = 'user__employee_code'
    
    def session_duration(self, obj):
        if obj.logout_time:
            duration = obj.logout_time - obj.login_time
        else:
            duration = timezone.now() - obj.login_time
        
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    session_duration.short_description = 'Duration'
    
    def terminate_sessions(self, request, queryset):
        def action(qs):
            count = 0
            for session in qs.filter(is_active=True):
                session.terminate_session()
                count += 1
            return count
        self.safe_bulk_action(request, queryset, action, '{count} sessions terminated successfully.')
    terminate_sessions.short_description = 'Terminate selected sessions'

class PasswordResetTokenAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        'user', 'get_employee_code', 'token_preview', 'created_at',
        'expires_at', 'is_used', 'is_expired_status'
    ]
    
    list_filter = ['is_used', 'created_at', 'expires_at']
    
    search_fields = [
        'user__employee_code', 'user__first_name', 'user__last_name',
        'user__email', 'ip_address'
    ]
    
    ordering = ['-created_at']
    
    readonly_fields = [
        'token', 'created_at', 'expires_at', 'used_at', 'ip_address'
    ]
    
    fieldsets = (
        ('Token Information', {
            'fields': ('user', 'token', 'ip_address')
        }),
        ('Status', {
            'fields': ('is_used', 'used_at')
        }),
        ('Timing', {
            'fields': ('created_at', 'expires_at')
        })
    )
    
    actions = ['mark_as_used', 'delete_expired_tokens']
    
    def optimize_queryset(self, queryset):
        return queryset.select_related('user')
    
    def get_employee_code(self, obj):
        return obj.user.employee_code
    get_employee_code.short_description = 'Employee Code'
    get_employee_code.admin_order_field = 'user__employee_code'
    
    def token_preview(self, obj):
        return f"{obj.token[:8]}..."
    token_preview.short_description = 'Token'
    
    def is_expired_status(self, obj):
        if obj.is_expired():
            return format_html('<span style="color: red;">Expired</span>')
        elif obj.is_used:
            return format_html('<span style="color: orange;">Used</span>')
        else:
            return format_html('<span style="color: green;">Valid</span>')
    is_expired_status.short_description = 'Status'
    
    def mark_as_used(self, request, queryset):
        def action(qs):
            return qs.filter(is_used=False).update(is_used=True, used_at=timezone.now())
        self.safe_bulk_action(request, queryset, action, '{count} tokens marked as used.')
    mark_as_used.short_description = 'Mark selected tokens as used'
    
    def delete_expired_tokens(self, request, queryset):
        def action(qs):
            expired_tokens = [token for token in qs if token.is_expired()]
            count = len(expired_tokens)
            for token in expired_tokens:
                token.delete()
            return count
        self.safe_bulk_action(request, queryset, action, '{count} expired tokens deleted.')
    delete_expired_tokens.short_description = 'Delete expired tokens'


class AuditLogAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        'timestamp', 'user', 'get_employee_code', 'action',
        'description_preview', 'ip_address'
    ]
    
    list_filter = [
        'action', 'timestamp', 'user__department', 'user__role'
    ]
    
    search_fields = [
        'user__employee_code', 'user__first_name', 'user__last_name',
        'description', 'ip_address', 'action'
    ]
    
    ordering = ['-timestamp']
    
    readonly_fields = [
        'user', 'action', 'description', 'ip_address',
        'user_agent', 'timestamp', 'additional_data'
    ]
    
    fieldsets = (
        ('Action Information', {
            'fields': ('user', 'action', 'description')
        }),
        ('Request Information', {
            'fields': ('ip_address', 'user_agent')
        }),
        ('Timing', {
            'fields': ('timestamp',)
        }),
        ('Additional Data', {
            'fields': ('additional_data',),
            'classes': ('collapse',)
        })
    )
    
    actions = ['export_selected_logs', 'delete_old_logs']
    
    def optimize_queryset(self, queryset):
        return queryset.select_related('user__department', 'user__role')
    
    def get_employee_code(self, obj):
        return obj.user.employee_code if obj.user else 'System'
    get_employee_code.short_description = 'Employee Code'
    get_employee_code.admin_order_field = 'user__employee_code'
    
    def description_preview(self, obj):
        if len(obj.description) > 50:
            return f"{obj.description[:50]}..."
        return obj.description
    description_preview.short_description = 'Description'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def export_selected_logs(self, request, queryset):
        try:
            report_data = SystemUtilities.generate_audit_report(
                start_date=timezone.now() - timezone.timedelta(days=30),
                end_date=timezone.now(),
                user_filter=None
            )
            self.message_user(request, f'{queryset.count()} logs prepared for export.')
        except Exception as e:
            self.message_user(request, f'Export failed: {str(e)}', level=messages.ERROR)
    export_selected_logs.short_description = 'Export selected logs'
    
    def delete_old_logs(self, request, queryset):
        def action(qs):
            cutoff_date = timezone.now() - timezone.timedelta(days=365)
            old_logs = qs.filter(timestamp__lt=cutoff_date)
            count = old_logs.count()
            old_logs.delete()
            return count
        self.safe_bulk_action(request, queryset, action, '{count} old logs deleted.')
    delete_old_logs.short_description = 'Delete logs older than 1 year'


class SystemConfigurationAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        'key', 'value_preview', 'description_preview',
        'is_active', 'updated_at', 'updated_by'
    ]
    
    list_filter = ['is_active', 'updated_at']
    
    search_fields = ['key', 'value', 'description']
    
    ordering = ['key']
    
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Configuration', {
            'fields': ('key', 'value', 'description')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('System Information', {
            'fields': ('created_at', 'updated_at', 'updated_by'),
            'classes': ('collapse',)
        })
    )
    
    actions = ['activate_configs', 'deactivate_configs', 'reset_to_defaults']
    
    def optimize_queryset(self, queryset):
        return queryset.select_related('updated_by')
    
    def value_preview(self, obj):
        if len(obj.value) > 30:
            return f"{obj.value[:30]}..."
        return obj.value
    value_preview.short_description = 'Value'
    
    def description_preview(self, obj):
        if obj.description and len(obj.description) > 40:
            return f"{obj.description[:40]}..."
        return obj.description or ''
    description_preview.short_description = 'Description'
    
    def activate_configs(self, request, queryset):
        def action(qs):
            return qs.update(is_active=True, updated_by=request.user)
        self.safe_bulk_action(request, queryset, action, '{count} configurations activated.')
    activate_configs.short_description = 'Activate selected configurations'
    
    def deactivate_configs(self, request, queryset):
        def action(qs):
            return qs.update(is_active=False, updated_by=request.user)
        self.safe_bulk_action(request, queryset, action, '{count} configurations deactivated.')
    deactivate_configs.short_description = 'Deactivate selected configurations'
    
    def reset_to_defaults(self, request, queryset):
        default_values = {
            'COMPANY_NAME': 'HR Payroll System',
            'WORKING_HOURS_PER_DAY': '8.00',
            'WORKING_DAYS_PER_WEEK': '5',
            'PASSWORD_EXPIRY_DAYS': '90',
            'MAX_LOGIN_ATTEMPTS': '5',
            'SESSION_TIMEOUT': '30'
        }
        
        def action(qs):
            updated = 0
            for config in qs:
                if config.key in default_values:
                    config.value = default_values[config.key]
                    config.updated_by = request.user
                    config.save()
                    updated += 1
            return updated
        
        self.safe_bulk_action(request, queryset, action, '{count} configurations reset to defaults.')
    reset_to_defaults.short_description = 'Reset selected to default values'


class AdminLogEntryAdmin(admin.ModelAdmin):
    list_display = [
        'action_time', 'user', 'content_type', 'object_repr',
        'action_flag', 'change_message'
    ]
    
    list_filter = ['action_flag', 'action_time', 'content_type']
    
    search_fields = ['object_repr', 'change_message', 'user__username']
    
    ordering = ['-action_time']
    
    readonly_fields = [
        'action_time', 'user', 'content_type', 'object_id',
        'object_repr', 'action_flag', 'change_message'
    ]
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


admin.site.register(PasswordResetToken, PasswordResetTokenAdmin)
admin.site.register(AuditLog, AuditLogAdmin)
admin.site.register(SystemConfiguration, SystemConfigurationAdmin)
admin.site.register(LogEntry, AdminLogEntryAdmin)

admin.site.site_header = 'HR Payroll System Administration'
admin.site.site_title = 'HR Admin'
admin.site.index_title = 'HR Payroll System Administration'
admin.site.empty_value_display = '(None)'


@staff_member_required
def admin_dashboard_view(request):
    try:
        stats = SystemUtilities.get_system_statistics()
        
        context = {
            'total_users': stats.get('total_users', 0),
            'active_users': stats.get('active_users', 0),
            'total_departments': Department.objects.count(),
            'total_roles': Role.objects.count(),
            'active_sessions': stats.get('active_sessions', 0),
            'recent_activities': AuditLog.objects.select_related('user').order_by('-timestamp')[:10],
            'failed_logins_today': AuditLog.objects.filter(
                action='LOGIN_FAILED',
                timestamp__gte=timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            ).count(),
            'password_expiry_warnings': stats.get('password_expiry_warnings', 0)
        }
        
        return render(request, 'admin/dashboard.html', context)
    
    except Exception as e:
        context = {
            'error': f'Dashboard data unavailable: {str(e)}',
            'total_users': 0,
            'active_users': 0,
            'total_departments': 0,
            'total_roles': 0,
            'active_sessions': 0,
            'recent_activities': [],
            'failed_logins_today': 0,
            'password_expiry_warnings': 0
        }
        return render(request, 'admin/dashboard.html', context)


def get_admin_urls():
    return [
        path('dashboard/', admin_dashboard_view, name='admin_dashboard'),
    ]


original_get_urls = admin.site.get_urls
admin.site.get_urls = lambda: get_admin_urls() + original_get_urls()
