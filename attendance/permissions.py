  from django.core.exceptions import PermissionDenied
from django.contrib.auth.models import Permission
from accounts.models import CustomUser, Department
from employees.models import EmployeeProfile
from .utils import EmployeeDataManager

class AttendancePermissionMixin:
    def has_attendance_access(self, user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        return user.has_permission('view_attendance')
    
    def can_view_all_attendance(self, user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name == 'HR_ADMIN':
            return True
        
        if user.role and user.role.can_view_all_data:
            return True
        
        return False
    
    def can_manage_attendance(self, user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        if user.role and user.role.can_manage_employees:
            return True
        
        return user.has_permission('change_attendance')

class EmployeeAttendancePermission:
    @staticmethod
    def can_view_employee_attendance(user, target_employee):
        if user.is_superuser:
            return True
        
        if user == target_employee:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        if user.role and user.role.can_view_all_data:
            return True
        
        if user.role and user.role.name == 'DEPARTMENT_MANAGER':
            if user.department and target_employee.department == user.department:
                return True
        
        if target_employee.manager == user:
            return True
        
        subordinates = user.get_subordinates()
        if target_employee in subordinates:
            return True
        
        return False
    
    @staticmethod
    def can_edit_employee_attendance(user, target_employee):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        if user.role and user.role.can_manage_employees:
            if user.role.name == 'DEPARTMENT_MANAGER':
                if user.department and target_employee.department == user.department:
                    return True
            else:
                return True
        
        if target_employee.manager == user:
            return True
        
        return False
    
    @staticmethod
    def can_approve_attendance_correction(user, target_employee):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        if target_employee.manager == user:
            return True
        
        if user.role and user.role.name == 'DEPARTMENT_MANAGER':
            if user.department and target_employee.department == user.department:
                return True
        
        return False

class LeavePermission:
    @staticmethod
    def can_apply_leave(user, employee):
        if user != employee:
            return False
        
        profile = EmployeeDataManager.get_employee_profile(employee)
        if not profile or not profile.is_active:
            return False
        
        if profile.employment_status == 'PROBATION':
            from .models import LeaveType
            leave_type = LeaveType.objects.filter(applicable_after_probation_only=False).first()
            return leave_type is not None
        
        return True
    
    @staticmethod
    def can_approve_leave(user, leave_request):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        if user.role and user.role.can_approve_leave:
            return True
        
        if leave_request.employee.manager == user:
            return True
        
        if user.role and user.role.name == 'DEPARTMENT_MANAGER':
            if user.department and leave_request.employee.department == user.department:
                return True
        
        return False
    
    @staticmethod
    def can_view_leave_balance(user, target_employee):
        if user == target_employee:
            return True
        
        return EmployeeAttendancePermission.can_view_employee_attendance(user, target_employee)

class DevicePermission:
    @staticmethod
    def can_manage_devices(user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'SUPER_ADMIN']:
            return True
        
        return user.has_permission('change_attendancedevice')
    
    @staticmethod
    def can_sync_device_data(user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        return user.has_permission('sync_device_data')
    
    @staticmethod
    def can_view_device_logs(user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        return user.has_permission('view_attendancelog')

class ReportPermission:
    @staticmethod
    def can_generate_reports(user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        if user.role and user.role.name == 'DEPARTMENT_MANAGER':
            return True
        
        return user.has_permission('generate_attendance_reports')
    
    @staticmethod
    def can_view_department_reports(user, department):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        if user.role and user.role.can_view_all_data:
            return True
        
        if user.department == department:
            return True
        
        return False
    
    @staticmethod
    def can_export_attendance_data(user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER', 'PAYROLL_MANAGER']:
            return True
        
        return user.has_permission('export_attendance_data')

class SystemPermission:
    @staticmethod
    def can_manage_shifts(user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        return user.has_permission('change_shift')
    
    @staticmethod
    def can_manage_holidays(user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
            return True
        
        return user.has_permission('change_holiday')
    
    @staticmethod
    def can_manage_leave_types(user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'SUPER_ADMIN']:
            return True
        
        return user.has_permission('change_leavetype')
    
    @staticmethod
    def can_access_attendance_settings(user):
        if user.is_superuser:
            return True
        
        if user.role and user.role.name in ['HR_ADMIN', 'SUPER_ADMIN']:
            return True
        
        return False

def check_attendance_permission(user, permission_type, target_employee=None, **kwargs):
    permission_map = {
        'view_attendance': lambda: AttendancePermissionMixin().has_attendance_access(user),
        'view_employee_attendance': lambda: EmployeeAttendancePermission.can_view_employee_attendance(user, target_employee),
        'edit_employee_attendance': lambda: EmployeeAttendancePermission.can_edit_employee_attendance(user, target_employee),
        'approve_correction': lambda: EmployeeAttendancePermission.can_approve_attendance_correction(user, target_employee),
        'apply_leave': lambda: LeavePermission.can_apply_leave(user, target_employee),
        'approve_leave': lambda: LeavePermission.can_approve_leave(user, kwargs.get('leave_request')),
        'manage_devices': lambda: DevicePermission.can_manage_devices(user),
        'sync_devices': lambda: DevicePermission.can_sync_device_data(user),
        'generate_reports': lambda: ReportPermission.can_generate_reports(user),
        'export_data': lambda: ReportPermission.can_export_attendance_data(user),
        'manage_shifts': lambda: SystemPermission.can_manage_shifts(user),
        'manage_holidays': lambda: SystemPermission.can_manage_holidays(user),
        'manage_leave_types': lambda: SystemPermission.can_manage_leave_types(user),
        'access_settings': lambda: SystemPermission.can_access_attendance_settings(user),
    }
    
    permission_func = permission_map.get(permission_type)
    if not permission_func:
        return False
    
    return permission_func()

def require_attendance_permission(permission_type, target_employee=None, **kwargs):
    def decorator(func):
        def wrapper(request, *args, **kwargs_inner):
            if not check_attendance_permission(request.user, permission_type, target_employee, **kwargs):
                raise PermissionDenied(f"You don't have permission to {permission_type}")
            return func(request, *args, **kwargs_inner)
        return wrapper
    return decorator

def get_accessible_employees(user):
    if user.is_superuser:
        return CustomUser.active.all()
    
    if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
        return CustomUser.active.all()
    
    if user.role and user.role.can_view_all_data:
        return CustomUser.active.all()
    
    accessible_employees = [user]
    
    if user.role and user.role.name == 'DEPARTMENT_MANAGER':
        if user.department:
            dept_employees = user.department.get_all_employees()
            accessible_employees.extend(dept_employees)
    
    subordinates = user.get_subordinates()
    accessible_employees.extend(subordinates)
    
    return CustomUser.objects.filter(id__in=[emp.id for emp in accessible_employees])

def get_accessible_departments(user):
    if user.is_superuser:
        return Department.active.all()
    
    if user.role and user.role.name in ['HR_ADMIN', 'HR_MANAGER']:
        return Department.active.all()
    
    if user.role and user.role.can_view_all_data:
        return Department.active.all()
    
    accessible_departments = []
    
    if user.department:
        accessible_departments.append(user.department)
    
    if user.role and user.role.name == 'DEPARTMENT_MANAGER':
        managed_departments = Department.objects.filter(manager=user)
        accessible_departments.extend(managed_departments)
    
    return Department.objects.filter(id__in=[dept.id for dept in accessible_departments])
