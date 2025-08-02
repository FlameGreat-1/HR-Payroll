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
from employees.models import EmployeeProfile, Education, Contract
from employees.admin import EmployeeProfileAdmin, EducationAdmin, ContractAdmin
from .models import (
    CustomUser,
    Department,
    Role,
    UserSession,
    PasswordResetToken,
    AuditLog,
    SystemConfiguration,
)
from .forms import EmployeeRegistrationForm, EmployeeUpdateForm
from attendance.models import (
    Attendance,
    AttendanceDevice,
    Shift,
    EmployeeShift,
    LeaveRequest,
    LeaveBalance,
    LeaveType,
    Holiday,
    MonthlyAttendanceSummary,
    AttendanceCorrection,
    AttendanceReport,
    AttendanceLog,
)
from attendance.admin import (
    AttendanceAdmin,
    AttendanceDeviceAdmin,
    ShiftAdmin,
    EmployeeShiftAdmin,
    LeaveRequestAdmin,
    LeaveBalanceAdmin,
    LeaveTypeAdmin,
    HolidayAdmin,
    MonthlyAttendanceSummaryAdmin,
    AttendanceCorrectionAdmin,
    AttendanceReportAdmin,
    AttendanceLogAdmin,
)

from .admin_site import hr_admin_site  

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

class CustomUserAdmin(BaseAdminMixin, BaseUserAdmin):
    add_form = EmployeeRegistrationForm
    form = EmployeeUpdateForm
    model = CustomUser

    list_display = [
        "employee_code",
        "get_full_name",
        "email",
        "department",
        "role",
        "status",
        "is_active",
        "last_login",
        "created_at",
    ]

    list_filter = [
        "status",
        "is_active",
        "is_verified",
        "gender",
        "department",
        "role",
        "created_at",
        "last_login",
        "hire_date",
    ]

    search_fields = [
        "employee_code",
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "job_title",
    ]

    ordering = ["employee_code"]

    readonly_fields = [
        "created_at",
        "updated_at",
        "last_login",
        "last_login_ip",
        "failed_login_attempts",
        "account_locked_until",
        "password_changed_at",
        "created_by",
    ]

    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "employee_code",
                    "first_name",
                    "last_name",
                    "middle_name",
                    "email",
                    "phone_number",
                    "date_of_birth",
                    "gender",
                )
            },
        ),
        (
            "Address Information",
            {
                "fields": (
                    "address_line1",
                    "address_line2",
                    "city",
                    "state",
                    "postal_code",
                    "country",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Emergency Contact",
            {
                "fields": (
                    "emergency_contact_name",
                    "emergency_contact_phone",
                    "emergency_contact_relationship",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Employment Information",
            {
                "fields": (
                    "department",
                    "role",
                    "job_title",
                    "manager",
                    "hire_date",
                    "termination_date",
                    "status",
                )
            },
        ),
        (
            "Account Status",
            {"fields": ("is_active", "is_verified", "must_change_password")},
        ),
        (
            "Permissions",
            {
                "fields": ("is_staff", "is_superuser", "groups", "user_permissions"),
                "classes": ("collapse",),
            },
        ),
        (
            "Security Information",
            {
                "fields": (
                    "last_login",
                    "last_login_ip",
                    "failed_login_attempts",
                    "account_locked_until",
                    "password_changed_at",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "System Information",
            {
                "fields": ("created_at", "updated_at", "created_by"),
                "classes": ("collapse",),
            },
        ),
    )

    add_fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "middle_name",
                    "email",
                    "phone_number",
                    "date_of_birth",
                    "gender",
                )
            },
        ),
        (
            "Employment Information",
            {"fields": ("department", "role", "job_title", "manager", "hire_date")},
        ),
        ("Password", {"fields": ("password1", "password2")}),
    )

    filter_horizontal = (
        "groups",
        "user_permissions",
    )

    actions = [
        "activate_users",
        "deactivate_users",
        "suspend_users",
        "reset_passwords",
        "unlock_accounts",
        "verify_users",
    ]

    def optimize_queryset(self, queryset):
        return queryset.select_related("department", "role", "manager", "created_by")

    def get_full_name(self, obj):
        return obj.get_full_name()

    get_full_name.short_description = "Full Name"
    get_full_name.admin_order_field = "first_name"

    def activate_users(self, request, queryset):
        def action(qs):
            return qs.update(status="ACTIVE", is_active=True)

        self.safe_bulk_action(
            request, queryset, action, "{count} users activated successfully."
        )

    activate_users.short_description = "Activate selected users"

    def deactivate_users(self, request, queryset):
        def action(qs):
            return qs.update(status="INACTIVE", is_active=False)

        self.safe_bulk_action(
            request, queryset, action, "{count} users deactivated successfully."
        )

    deactivate_users.short_description = "Deactivate selected users"

    def suspend_users(self, request, queryset):
        def action(qs):
            return qs.update(status="SUSPENDED")

        self.safe_bulk_action(
            request, queryset, action, "{count} users suspended successfully."
        )

    suspend_users.short_description = "Suspend selected users"

    def reset_passwords(self, request, queryset):
        def action(qs):
            return qs.update(must_change_password=True)

        self.safe_bulk_action(
            request, queryset, action, "{count} users marked for password reset."
        )

    reset_passwords.short_description = "Force password reset for selected users"

    def unlock_accounts(self, request, queryset):
        def action(qs):
            return qs.update(failed_login_attempts=0, account_locked_until=None)

        self.safe_bulk_action(
            request, queryset, action, "{count} accounts unlocked successfully."
        )

    unlock_accounts.short_description = "Unlock selected accounts"

    def verify_users(self, request, queryset):
        def action(qs):
            return qs.update(is_verified=True)

        self.safe_bulk_action(
            request, queryset, action, "{count} users verified successfully."
        )

    verify_users.short_description = "Verify selected users"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

class DepartmentAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        "code",
        "name",
        "manager",
        "parent_department",
        "employee_count",
        "is_active",
        "created_at",
    ]

    list_filter = ["is_active", "created_at", "parent_department"]

    search_fields = ["code", "name", "description"]

    ordering = ["code"]

    readonly_fields = ["created_at", "updated_at", "created_by"]

    fieldsets = (
        (
            "Basic Information",
            {"fields": ("code", "name", "description", "budget", "location")},
        ),
        ("Hierarchy", {"fields": ("manager", "parent_department")}),
        ("Status", {"fields": ("is_active",)}),
        (
            "System Information",
            {
                "fields": ("created_at", "updated_at", "created_by"),
                "classes": ("collapse",),
            },
        ),
    )

    actions = ["activate_departments", "deactivate_departments"]

    def optimize_queryset(self, queryset):
        return queryset.select_related(
            "manager", "parent_department", "created_by"
        ).annotate(
            active_employee_count=Count(
                "employees", filter=Q(employees__is_active=True)
            )
        )

    def employee_count(self, obj):
        return getattr(
            obj, "active_employee_count", obj.employees.filter(is_active=True).count()
        )

    employee_count.short_description = "Active Employees"

    def activate_departments(self, request, queryset):
        def action(qs):
            return qs.update(is_active=True)

        self.safe_bulk_action(
            request, queryset, action, "{count} departments activated successfully."
        )

    activate_departments.short_description = "Activate selected departments"

    def deactivate_departments(self, request, queryset):
        def action(qs):
            return qs.update(is_active=False)

        self.safe_bulk_action(
            request, queryset, action, "{count} departments deactivated successfully."
        )

    deactivate_departments.short_description = "Deactivate selected departments"


class RoleAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        "name",
        "display_name",
        "user_count",
        "permission_count",
        "is_active",
        "created_at",
    ]

    list_filter = ["is_active", "created_at", "name"]

    search_fields = ["name", "display_name", "description"]

    ordering = ["display_name"]

    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "Basic Information",
            {"fields": ("name", "display_name", "description", "level")},
        ),
        (
            "Role Capabilities",
            {
                "fields": (
                    "can_manage_employees",
                    "can_view_all_data",
                    "can_approve_leave",
                    "can_manage_payroll",
                )
            },
        ),
        ("Permissions", {"fields": ("permissions",)}),
        ("Status", {"fields": ("is_active",)}),
        (
            "System Information",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    filter_horizontal = ["permissions"]

    actions = ["activate_roles", "deactivate_roles"]

    def optimize_queryset(self, queryset):
        return queryset.prefetch_related("permissions").annotate(
            active_user_count=Count("users", filter=Q(users__is_active=True)),
            total_permissions=Count("permissions"),
        )

    def user_count(self, obj):
        return getattr(
            obj, "active_user_count", obj.users.filter(is_active=True).count()
        )

    user_count.short_description = "Active Users"

    def permission_count(self, obj):
        return getattr(obj, "total_permissions", obj.permissions.count())

    permission_count.short_description = "Permissions"

    def activate_roles(self, request, queryset):
        def action(qs):
            return qs.update(is_active=True)

        self.safe_bulk_action(
            request, queryset, action, "{count} roles activated successfully."
        )

    activate_roles.short_description = "Activate selected roles"

    def deactivate_roles(self, request, queryset):
        def action(qs):
            return qs.update(is_active=False)

        self.safe_bulk_action(
            request, queryset, action, "{count} roles deactivated successfully."
        )

    deactivate_roles.short_description = "Deactivate selected roles"

class UserSessionAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        "user",
        "get_employee_code",
        "ip_address",
        "login_time",
        "last_activity",
        "is_active",
        "session_duration",
    ]

    list_filter = ["is_active", "login_time", "last_activity"]

    search_fields = [
        "user__employee_code",
        "user__first_name",
        "user__last_name",
        "ip_address",
    ]

    ordering = ["-login_time"]

    readonly_fields = ["session_key_hash", "login_time", "last_activity", "logout_time"]

    fieldsets = (
        (
            "Session Information",
            {
                "fields": (
                    "user",
                    "session_key_hash",
                    "ip_address",
                    "user_agent",
                    "device_type",
                    "location",
                )
            },
        ),
        (
            "Timing",
            {"fields": ("login_time", "last_activity", "logout_time", "is_active")},
        ),
    )

    actions = ["terminate_sessions"]

    def optimize_queryset(self, queryset):
        return queryset.select_related("user")

    def get_employee_code(self, obj):
        return obj.user.employee_code

    get_employee_code.short_description = "Employee Code"
    get_employee_code.admin_order_field = "user__employee_code"

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

    session_duration.short_description = "Duration"

    def terminate_sessions(self, request, queryset):
        def action(qs):
            count = 0
            for session in qs.filter(is_active=True):
                session.terminate_session()
                count += 1
            return count

        self.safe_bulk_action(
            request, queryset, action, "{count} sessions terminated successfully."
        )

    terminate_sessions.short_description = "Terminate selected sessions"


class PasswordResetTokenAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        "user",
        "get_employee_code",
        "token_preview",
        "created_at",
        "expires_at",
        "is_used",
        "is_expired_status",
    ]

    list_filter = ["is_used", "created_at", "expires_at"]

    search_fields = [
        "user__employee_code",
        "user__first_name",
        "user__last_name",
        "user__email",
        "ip_address",
    ]

    ordering = ["-created_at"]

    readonly_fields = ["token", "created_at", "expires_at", "used_at", "ip_address"]

    fieldsets = (
        ("Token Information", {"fields": ("user", "token", "ip_address")}),
        ("Status", {"fields": ("is_used", "used_at")}),
        ("Timing", {"fields": ("created_at", "expires_at")}),
    )

    actions = ["mark_as_used", "delete_expired_tokens"]

    def optimize_queryset(self, queryset):
        return queryset.select_related("user")

    def get_employee_code(self, obj):
        return obj.user.employee_code

    get_employee_code.short_description = "Employee Code"
    get_employee_code.admin_order_field = "user__employee_code"

    def token_preview(self, obj):
        return f"{obj.token[:8]}..."

    token_preview.short_description = "Token"

    def is_expired_status(self, obj):
        if obj.is_expired():
            return format_html('<span style="color: red;">Expired</span>')
        elif obj.is_used:
            return format_html('<span style="color: orange;">Used</span>')
        else:
            return format_html('<span style="color: green;">Valid</span>')

    is_expired_status.short_description = "Status"

    def mark_as_used(self, request, queryset):
        def action(qs):
            return qs.filter(is_used=False).update(is_used=True, used_at=timezone.now())

        self.safe_bulk_action(
            request, queryset, action, "{count} tokens marked as used."
        )

    mark_as_used.short_description = "Mark selected tokens as used"

    def delete_expired_tokens(self, request, queryset):
        def action(qs):
            expired_tokens = [token for token in qs if token.is_expired()]
            count = len(expired_tokens)
            for token in expired_tokens:
                token.delete()
            return count

        self.safe_bulk_action(
            request, queryset, action, "{count} expired tokens deleted."
        )

    delete_expired_tokens.short_description = "Delete expired tokens"
class AuditLogAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        "timestamp",
        "user",
        "get_employee_code",
        "action",
        "object_repr_preview",
        "ip_address",
    ]

    list_filter = ["action", "timestamp", "user__department", "user__role"]

    search_fields = [
        "user__employee_code",
        "user__first_name",
        "user__last_name",
        "object_repr",
        "ip_address",
        "action",
    ]

    ordering = ["-timestamp"]

    readonly_fields = [
        "user",
        "action",
        "object_repr",
        "ip_address",
        "user_agent",
        "timestamp",
        "changes",
        "model_name",
        "object_id",
        "session_key",
    ]

    fieldsets = (
        (
            "Action Information",
            {
                "fields": (
                    "user",
                    "action",
                    "object_repr",
                    "model_name",
                    "object_id",
                )
            },
        ),
        (
            "Request Information",
            {"fields": ("ip_address", "user_agent", "session_key")},
        ),
        ("Timing", {"fields": ("timestamp",)}),
        ("Additional Data", {"fields": ("changes",), "classes": ("collapse",)}),
    )

    actions = ["export_selected_logs", "delete_old_logs"]

    def optimize_queryset(self, queryset):
        return queryset.select_related("user__department", "user__role")

    def get_employee_code(self, obj):
        return obj.user.employee_code if obj.user else "System"

    get_employee_code.short_description = "Employee Code"
    get_employee_code.admin_order_field = "user__employee_code"

    def object_repr_preview(self, obj):
        if obj.object_repr and len(obj.object_repr) > 50:
            return f"{obj.object_repr[:50]}..."
        return obj.object_repr or ""

    object_repr_preview.short_description = "Object"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def export_selected_logs(self, request, queryset):
        try:
            self.message_user(request, f"{queryset.count()} logs prepared for export.")
        except Exception as e:
            self.message_user(request, f"Export failed: {str(e)}", level=messages.ERROR)

    export_selected_logs.short_description = "Export selected logs"

    def delete_old_logs(self, request, queryset):
        def action(qs):
            cutoff_date = timezone.now() - timezone.timedelta(days=365)
            old_logs = qs.filter(timestamp__lt=cutoff_date)
            count = old_logs.count()
            old_logs.delete()
            return count

        self.safe_bulk_action(request, queryset, action, "{count} old logs deleted.")

    delete_old_logs.short_description = "Delete logs older than 1 year"
class SystemConfigurationAdmin(BaseAdminMixin, admin.ModelAdmin):
    list_display = [
        "key",
        "value_preview",
        "description_preview",
        "is_active",
        "updated_at",
        "updated_by",
    ]

    list_filter = ["is_active", "updated_at"]

    search_fields = ["key", "value", "description"]

    ordering = ["key"]

    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        ("Configuration", {"fields": ("key", "value", "description")}),
        ("Status", {"fields": ("is_active",)}),
        (
            "System Information",
            {
                "fields": ("created_at", "updated_at", "updated_by"),
                "classes": ("collapse",),
            },
        ),
    )

    actions = ["activate_configs", "deactivate_configs", "reset_to_defaults"]

    def optimize_queryset(self, queryset):
        return queryset.select_related("updated_by")

    def value_preview(self, obj):
        if len(obj.value) > 30:
            return f"{obj.value[:30]}..."
        return obj.value

    value_preview.short_description = "Value"

    def description_preview(self, obj):
        if obj.description and len(obj.description) > 40:
            return f"{obj.description[:40]}..."
        return obj.description or ""

    description_preview.short_description = "Description"

    def activate_configs(self, request, queryset):
        def action(qs):
            return qs.update(is_active=True, updated_by=request.user)

        self.safe_bulk_action(
            request, queryset, action, "{count} configurations activated successfully."
        )

    activate_configs.short_description = "Activate selected configurations"

    def deactivate_configs(self, request, queryset):
        def action(qs):
            return qs.update(is_active=False, updated_by=request.user)

        self.safe_bulk_action(
            request,
            queryset,
            action,
            "{count} configurations deactivated successfully.",
        )

    deactivate_configs.short_description = "Deactivate selected configurations"

    def reset_to_defaults(self, request, queryset):
        try:
            SystemConfiguration.reset_to_defaults(user=request.user)
            self.message_user(
                request, "System configurations reset to defaults successfully."
            )
        except Exception as e:
            self.message_user(request, f"Reset failed: {str(e)}", level=messages.ERROR)

    reset_to_defaults.short_description = "Reset all configurations to defaults"


hr_admin_site.register(CustomUser, CustomUserAdmin)
hr_admin_site.register(Department, DepartmentAdmin)
hr_admin_site.register(Role, RoleAdmin)
hr_admin_site.register(UserSession, UserSessionAdmin)
hr_admin_site.register(PasswordResetToken, PasswordResetTokenAdmin)
hr_admin_site.register(AuditLog, AuditLogAdmin)
hr_admin_site.register(SystemConfiguration, SystemConfigurationAdmin)

# EMPLOYEE REGISTRATIONS
hr_admin_site.register(EmployeeProfile, EmployeeProfileAdmin)
hr_admin_site.register(Education, EducationAdmin)
hr_admin_site.register(Contract, ContractAdmin)

# ATTENDANCE REGISTRATIONS
hr_admin_site.register(Attendance, AttendanceAdmin)
hr_admin_site.register(AttendanceDevice, AttendanceDeviceAdmin)
hr_admin_site.register(Shift, ShiftAdmin)
hr_admin_site.register(EmployeeShift, EmployeeShiftAdmin)
hr_admin_site.register(LeaveRequest, LeaveRequestAdmin)
hr_admin_site.register(LeaveBalance, LeaveBalanceAdmin)
hr_admin_site.register(LeaveType, LeaveTypeAdmin)
hr_admin_site.register(Holiday, HolidayAdmin)
hr_admin_site.register(MonthlyAttendanceSummary, MonthlyAttendanceSummaryAdmin)
hr_admin_site.register(AttendanceCorrection, AttendanceCorrectionAdmin)
hr_admin_site.register(AttendanceReport, AttendanceReportAdmin)
hr_admin_site.register(AttendanceLog, AttendanceLogAdmin)
