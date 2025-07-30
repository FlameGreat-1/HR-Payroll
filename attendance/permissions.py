from django.core.exceptions import PermissionDenied
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
import logging

from accounts.models import CustomUser, Department
from employees.models import EmployeeProfile
from .utils import EmployeeDataManager

logger = logging.getLogger(__name__)


class AttendancePermissions:
    VIEW_ATTENDANCE = "attendance.view_attendance"
    CHANGE_ATTENDANCE = "attendance.change_attendance"
    DELETE_ATTENDANCE = "attendance.delete_attendance"
    ADD_ATTENDANCE = "attendance.add_attendance"
    SYNC_DEVICE_DATA = "attendance.sync_device_data"
    MANAGE_DEVICES = "attendance.manage_devices"
    GENERATE_REPORTS = "attendance.generate_reports"
    EXPORT_DATA = "attendance.export_data"
    APPROVE_LEAVE = "attendance.approve_leave"
    MANAGE_LEAVE_TYPES = "attendance.manage_leave_types"
    BULK_UPDATE_ATTENDANCE = "attendance.bulk_update_attendance"
    BULK_IMPORT_ATTENDANCE = "attendance.bulk_import_attendance"
    VIEW_AUDIT_LOGS = "attendance.view_auditlog"


class PermissionCache:
    @staticmethod
    def get_cached_permission(user_id, permission_key, default=False):
        cache_key = f"perm_{user_id}_{permission_key}"
        return cache.get(cache_key, default)

    @staticmethod
    def set_cached_permission(user_id, permission_key, result, timeout=300):
        cache_key = f"perm_{user_id}_{permission_key}"
        cache.set(cache_key, result, timeout)

    @staticmethod
    def clear_user_permissions(user_id):
        cache_pattern = f"perm_{user_id}_*"
        cache.delete_many([cache_pattern])


class AttendancePermissionMixin:
    @staticmethod
    def _safe_get_user_role(user):
        try:
            return user.role if hasattr(user, "role") else None
        except AttributeError:
            return None

    @staticmethod
    def _safe_get_user_department(user):
        try:
            return user.department if hasattr(user, "department") else None
        except AttributeError:
            return None

    def has_attendance_access(self, user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = self._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return user.has_perm(AttendancePermissions.VIEW_ATTENDANCE)

    def can_view_all_attendance(self, user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = self._safe_get_user_role(user)
        if role and role.name == "HR_ADMIN":
            return True

        if role and hasattr(role, "can_view_all_data") and role.can_view_all_data:
            return True

        return False

    def can_manage_attendance(self, user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = self._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if role and hasattr(role, "can_manage_employees") and role.can_manage_employees:
            return True

        return user.has_perm(AttendancePermissions.CHANGE_ATTENDANCE)


class EmployeeAttendancePermission:
    @staticmethod
    def _safe_get_user_role(user):
        try:
            return user.role if hasattr(user, "role") else None
        except AttributeError:
            return None

    @staticmethod
    def _safe_get_user_department(user):
        try:
            return user.department if hasattr(user, "department") else None
        except AttributeError:
            return None

    @staticmethod
    def can_view_employee_attendance(user, target_employee):
        if not user or not user.is_authenticated or not target_employee:
            return False

        if user.is_superuser:
            return True

        if user == target_employee:
            return True

        role = EmployeeAttendancePermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if role and hasattr(role, "can_view_all_data") and role.can_view_all_data:
            return True

        if role and role.name == "DEPARTMENT_MANAGER":
            user_dept = EmployeeAttendancePermission._safe_get_user_department(user)
            target_dept = EmployeeAttendancePermission._safe_get_user_department(
                target_employee
            )
            if user_dept and target_dept and target_dept == user_dept:
                return True

        if hasattr(target_employee, "manager") and target_employee.manager == user:
            return True

        try:
            subordinates = (
                user.get_subordinates() if hasattr(user, "get_subordinates") else []
            )
            if target_employee in subordinates:
                return True
        except:
            pass

        return False

    @staticmethod
    def can_edit_employee_attendance(user, target_employee):
        if not user or not user.is_authenticated or not target_employee:
            return False

        if user.is_superuser:
            return True

        role = EmployeeAttendancePermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if role and hasattr(role, "can_manage_employees") and role.can_manage_employees:
            if role.name == "DEPARTMENT_MANAGER":
                user_dept = EmployeeAttendancePermission._safe_get_user_department(user)
                target_dept = EmployeeAttendancePermission._safe_get_user_department(
                    target_employee
                )
                if user_dept and target_dept and target_dept == user_dept:
                    return True
            else:
                return True

        if hasattr(target_employee, "manager") and target_employee.manager == user:
            return True

        return False

    @staticmethod
    def can_approve_attendance_correction(user, target_employee):
        if not user or not user.is_authenticated or not target_employee:
            return False

        if user.is_superuser:
            return True

        role = EmployeeAttendancePermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if hasattr(target_employee, "manager") and target_employee.manager == user:
            return True

        if role and role.name == "DEPARTMENT_MANAGER":
            user_dept = EmployeeAttendancePermission._safe_get_user_department(user)
            target_dept = EmployeeAttendancePermission._safe_get_user_department(
                target_employee
            )
            if user_dept and target_dept and target_dept == user_dept:
                return True

        return False


class LeavePermission:
    @staticmethod
    def _safe_get_user_role(user):
        try:
            return user.role if hasattr(user, "role") else None
        except AttributeError:
            return None

    @staticmethod
    def _safe_get_user_department(user):
        try:
            return user.department if hasattr(user, "department") else None
        except AttributeError:
            return None

    @staticmethod
    def can_apply_leave(user, employee):
        if not user or not user.is_authenticated or not employee:
            return False

        if user != employee:
            return False

        try:
            profile = EmployeeDataManager.get_employee_profile(employee)
            if not profile or not profile.is_active:
                return False

            from .models import LeaveBalance

            current_year = timezone.now().year
            leave_balances = LeaveBalance.objects.filter(
                employee=employee, year=current_year
            )

            if not leave_balances.exists():
                return False

            if profile.employment_status == "PROBATION":
                from .models import LeaveType

                leave_type = LeaveType.objects.filter(
                    applicable_after_probation_only=False, is_active=True
                ).first()
                return leave_type is not None

            return True
        except Exception:
            return False

    @staticmethod
    def can_approve_leave(user, leave_request):
        if not user or not user.is_authenticated or not leave_request:
            return False

        if user.is_superuser:
            return True

        role = LeavePermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if role and hasattr(role, "can_approve_leave") and role.can_approve_leave:
            return True

        if (
            hasattr(leave_request.employee, "manager")
            and leave_request.employee.manager == user
        ):
            return True

        if role and role.name == "DEPARTMENT_MANAGER":
            user_dept = LeavePermission._safe_get_user_department(user)
            target_dept = LeavePermission._safe_get_user_department(
                leave_request.employee
            )
            if user_dept and target_dept and target_dept == user_dept:
                return True

        return False

    @staticmethod
    def can_view_leave_balance(user, target_employee):
        if user == target_employee:
            return True

        return EmployeeAttendancePermission.can_view_employee_attendance(
            user, target_employee
        )


class DevicePermission:
    @staticmethod
    def _safe_get_user_role(user):
        try:
            return user.role if hasattr(user, "role") else None
        except AttributeError:
            return None

    @staticmethod
    def can_manage_devices(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = DevicePermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "SUPER_ADMIN"]:
            return True

        return user.has_perm(AttendancePermissions.MANAGE_DEVICES)

    @staticmethod
    def can_sync_device_data(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = DevicePermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return user.has_perm(AttendancePermissions.SYNC_DEVICE_DATA)

    @staticmethod
    def can_view_device_logs(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = DevicePermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return user.has_perm("attendance.view_attendancelog")


class ReportPermission:
    @staticmethod
    def _safe_get_user_role(user):
        try:
            return user.role if hasattr(user, "role") else None
        except AttributeError:
            return None

    @staticmethod
    def _safe_get_user_department(user):
        try:
            return user.department if hasattr(user, "department") else None
        except AttributeError:
            return None

    @staticmethod
    def can_generate_reports(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = ReportPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if role and role.name == "DEPARTMENT_MANAGER":
            return True

        return user.has_perm(AttendancePermissions.GENERATE_REPORTS)

    @staticmethod
    def can_view_department_reports(user, department):
        if not user or not user.is_authenticated or not department:
            return False

        if user.is_superuser:
            return True

        role = ReportPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if role and hasattr(role, "can_view_all_data") and role.can_view_all_data:
            return True

        user_dept = ReportPermission._safe_get_user_department(user)
        if user_dept == department:
            return True

        return False

    @staticmethod
    def can_export_attendance_data(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = ReportPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER", "PAYROLL_MANAGER"]:
            return True

        return user.has_perm(AttendancePermissions.EXPORT_DATA)


class SystemPermission:
    @staticmethod
    def _safe_get_user_role(user):
        try:
            return user.role if hasattr(user, "role") else None
        except AttributeError:
            return None

    @staticmethod
    def can_manage_shifts(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = SystemPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return user.has_perm("attendance.change_shift")

    @staticmethod
    def can_manage_holidays(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = SystemPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return user.has_perm("attendance.change_holiday")

    @staticmethod
    def can_manage_leave_types(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = SystemPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "SUPER_ADMIN"]:
            return True

        return user.has_perm(AttendancePermissions.MANAGE_LEAVE_TYPES)

    @staticmethod
    def can_access_attendance_settings(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = SystemPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "SUPER_ADMIN"]:
            return True

        return False


class BulkOperationPermission:
    @staticmethod
    def _safe_get_user_role(user):
        try:
            return user.role if hasattr(user, "role") else None
        except AttributeError:
            return None

    @staticmethod
    def can_bulk_update_attendance(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = BulkOperationPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return user.has_perm(AttendancePermissions.BULK_UPDATE_ATTENDANCE)

    @staticmethod
    def can_bulk_import_attendance(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = BulkOperationPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN"]:
            return True

        return user.has_perm(AttendancePermissions.BULK_IMPORT_ATTENDANCE)


class TimeBasedPermission:
    @staticmethod
    def _safe_get_user_role(user):
        try:
            return user.role if hasattr(user, "role") else None
        except AttributeError:
            return None

    @staticmethod
    def can_edit_past_attendance(user, attendance_date):
        if not user or not user.is_authenticated or not attendance_date:
            return False

        if user.is_superuser:
            return True

        role = TimeBasedPermission._safe_get_user_role(user)
        if role and role.name == "HR_ADMIN":
            return True

        if role and role.name in ["HR_MANAGER", "DEPARTMENT_MANAGER"]:
            cutoff_date = timezone.now().date() - timedelta(days=7)
            return attendance_date >= cutoff_date

        cutoff_date = timezone.now().date() - timedelta(days=2)
        return attendance_date >= cutoff_date

    @staticmethod
    def can_edit_future_attendance(user, attendance_date):
        if not user or not user.is_authenticated or not attendance_date:
            return False

        if user.is_superuser:
            return True

        role = TimeBasedPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        max_future_date = timezone.now().date() + timedelta(days=30)
        return attendance_date <= max_future_date


class AuditPermission:
    @staticmethod
    def _safe_get_user_role(user):
        try:
            return user.role if hasattr(user, "role") else None
        except AttributeError:
            return None

    @staticmethod
    def can_view_audit_logs(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = AuditPermission._safe_get_user_role(user)
        if role and role.name in ["HR_ADMIN", "SUPER_ADMIN"]:
            return True

        return user.has_perm(AttendancePermissions.VIEW_AUDIT_LOGS)

    @staticmethod
    def can_delete_audit_logs(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        role = AuditPermission._safe_get_user_role(user)
        if role and role.name == "SUPER_ADMIN":
            return True

        return False


def check_attendance_permission(user, permission_type, target_employee=None, **kwargs):
    if not user or not user.is_authenticated:
        return False

    cache_key = f"{user.id}_{permission_type}_{target_employee.id if target_employee else 'none'}"
    cached_result = PermissionCache.get_cached_permission(user.id, cache_key)
    if cached_result is not None:
        return cached_result

    permission_map = {
        "view_attendance": lambda: AttendancePermissionMixin().has_attendance_access(
            user
        ),
        "view_employee_attendance": lambda: EmployeeAttendancePermission.can_view_employee_attendance(
            user, target_employee
        ),
        "edit_employee_attendance": lambda: EmployeeAttendancePermission.can_edit_employee_attendance(
            user, target_employee
        ),
        "approve_correction": lambda: EmployeeAttendancePermission.can_approve_attendance_correction(
            user, target_employee
        ),
        "apply_leave": lambda: LeavePermission.can_apply_leave(user, target_employee),
        "approve_leave": lambda: LeavePermission.can_approve_leave(
            user, kwargs.get("leave_request")
        ),
        "manage_devices": lambda: DevicePermission.can_manage_devices(user),
        "sync_devices": lambda: DevicePermission.can_sync_device_data(user),
        "generate_reports": lambda: ReportPermission.can_generate_reports(user),
        "export_data": lambda: ReportPermission.can_export_attendance_data(user),
        "manage_shifts": lambda: SystemPermission.can_manage_shifts(user),
        "manage_holidays": lambda: SystemPermission.can_manage_holidays(user),
        "manage_leave_types": lambda: SystemPermission.can_manage_leave_types(user),
        "access_settings": lambda: SystemPermission.can_access_attendance_settings(
            user
        ),
        "bulk_update": lambda: BulkOperationPermission.can_bulk_update_attendance(user),
        "bulk_import": lambda: BulkOperationPermission.can_bulk_import_attendance(user),
        "view_audit_logs": lambda: AuditPermission.can_view_audit_logs(user),
        "edit_past_attendance": lambda: TimeBasedPermission.can_edit_past_attendance(
            user, kwargs.get("attendance_date")
        ),
        "edit_future_attendance": lambda: TimeBasedPermission.can_edit_future_attendance(
            user, kwargs.get("attendance_date")
        ),
    }

    permission_func = permission_map.get(permission_type)
    if not permission_func:
        return False

    try:
        result = permission_func()
        PermissionCache.set_cached_permission(user.id, cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Permission check failed for {permission_type}: {e}")
        return False


def require_attendance_permission(permission_type, target_employee=None, **kwargs):
    def decorator(func):
        def wrapper(request, *args, **kwargs_inner):
            if not check_attendance_permission(
                request.user, permission_type, target_employee, **kwargs
            ):
                raise PermissionDenied(
                    f"You don't have permission to {permission_type}"
                )
            return func(request, *args, **kwargs_inner)

        return wrapper

    return decorator


def get_accessible_employees(user):
    if not user or not user.is_authenticated:
        return CustomUser.objects.none()

    if user.is_superuser:
        return CustomUser.objects.filter(is_active=True)

    try:
        role = user.role if hasattr(user, "role") else None
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return CustomUser.objects.filter(is_active=True)

        if role and hasattr(role, "can_view_all_data") and role.can_view_all_data:
            return CustomUser.objects.filter(is_active=True)

        accessible_employee_ids = [user.id]

        if role and role.name == "DEPARTMENT_MANAGER":
            user_dept = user.department if hasattr(user, "department") else None
            if user_dept:
                dept_employees = CustomUser.objects.filter(
                    department=user_dept, is_active=True
                )
                accessible_employee_ids.extend([emp.id for emp in dept_employees])

        if hasattr(user, "get_subordinates"):
            subordinates = user.get_subordinates()
            accessible_employee_ids.extend([emp.id for emp in subordinates])

        return CustomUser.objects.filter(id__in=accessible_employee_ids, is_active=True)
    except Exception:
        return CustomUser.objects.filter(id=user.id, is_active=True)


def get_accessible_departments(user):
    if not user or not user.is_authenticated:
        return Department.objects.none()

    if user.is_superuser:
        return Department.objects.filter(is_active=True)

    try:
        role = user.role if hasattr(user, "role") else None
        if role and role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return Department.objects.filter(is_active=True)

        if role and hasattr(role, "can_view_all_data") and role.can_view_all_data:
            return Department.objects.filter(is_active=True)

        accessible_department_ids = []

        user_dept = user.department if hasattr(user, "department") else None
        if user_dept:
            accessible_department_ids.append(user_dept.id)

        if role and role.name == "DEPARTMENT_MANAGER":
            managed_departments = Department.objects.filter(
                manager=user, is_active=True
            )
            accessible_department_ids.extend([dept.id for dept in managed_departments])

        return Department.objects.filter(
            id__in=accessible_department_ids, is_active=True
        )
    except Exception:
        user_dept = user.department if hasattr(user, "department") else None
        if user_dept:
            return Department.objects.filter(id=user_dept.id, is_active=True)
        return Department.objects.none()
