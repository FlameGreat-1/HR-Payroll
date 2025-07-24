from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse_lazy
from functools import wraps
from django.utils.decorators import method_decorator
from django.contrib.auth import get_user_model
from django.db.models import Q
import json

User = get_user_model()


def permission_required(permission_name, raise_exception=True):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if raise_exception:
                    raise PermissionDenied("Authentication required")
                return redirect('accounts:login')
            
            if not request.user.is_active:
                if raise_exception:
                    raise PermissionDenied("Account is inactive")
                return redirect('accounts:login')
            
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            
            if request.user.has_permission(permission_name):
                return view_func(request, *args, **kwargs)
            
            if raise_exception:
                raise PermissionDenied(f"Permission '{permission_name}' required")
            
            messages.error(request, 'You do not have permission to access this resource.')
            return redirect('accounts:dashboard')
        
        return _wrapped_view
    return decorator


def role_required(*allowed_roles, raise_exception=True):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if raise_exception:
                    raise PermissionDenied("Authentication required")
                return redirect('accounts:login')
            
            if not request.user.is_active:
                if raise_exception:
                    raise PermissionDenied("Account is inactive")
                return redirect('accounts:login')
            
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            
            if request.user.role and request.user.role.name in allowed_roles:
                return view_func(request, *args, **kwargs)
            
            if raise_exception:
                raise PermissionDenied(f"Role must be one of: {', '.join(allowed_roles)}")
            
            messages.error(request, 'You do not have permission to access this resource.')
            return redirect('accounts:dashboard')
        
        return _wrapped_view
    return decorator


def department_access_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        
        if not request.user.is_active:
            return redirect('accounts:login')
        
        department_id = kwargs.get('department_id') or request.GET.get('department_id')
        
        if department_id:
            from .models import Department
            try:
                department = Department.objects.get(id=department_id, is_active=True)
                
                if request.user.is_superuser:
                    return view_func(request, *args, **kwargs)
                
                if not request.user.role:
                    if request.user.department and request.user.department.id == int(department_id):
                        return view_func(request, *args, **kwargs)
                    raise PermissionDenied("Access denied to this department")
                
                role_name = request.user.role.name
                
                if role_name in ['SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER']:
                    return view_func(request, *args, **kwargs)
                
                elif role_name == 'DEPARTMENT_MANAGER':
                    if (request.user.department and 
                        (request.user.department.id == int(department_id) or 
                         department.parent_department == request.user.department)):
                        return view_func(request, *args, **kwargs)
                    raise PermissionDenied("Access denied to this department")
                
                elif role_name in ['PAYROLL_MANAGER', 'ACCOUNTANT', 'AUDITOR']:
                    return view_func(request, *args, **kwargs)
                
                else:
                    if request.user.department and request.user.department.id == int(department_id):
                        return view_func(request, *args, **kwargs)
                    raise PermissionDenied("Access denied to this department")
                
            except Department.DoesNotExist:
                raise PermissionDenied("Department not found")
        
        return view_func(request, *args, **kwargs)
    
    return _wrapped_view


def employee_access_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        
        if not request.user.is_active:
            return redirect('accounts:login')
        
        employee_id = kwargs.get('employee_id') or kwargs.get('user_id') or request.GET.get('employee_id')
        
        if employee_id:
            try:
                target_employee = User.objects.get(id=employee_id, is_active=True)
                
                if request.user.is_superuser:
                    return view_func(request, *args, **kwargs)
                
                if request.user.id == int(employee_id):
                    return view_func(request, *args, **kwargs)
                
                if not request.user.role:
                    raise PermissionDenied("Access denied to this employee")
                
                role_name = request.user.role.name
                
                if role_name in ['SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER']:
                    return view_func(request, *args, **kwargs)
                
                elif role_name == 'DEPARTMENT_MANAGER':
                    if (request.user.department and target_employee.department and
                        request.user.department == target_employee.department):
                        return view_func(request, *args, **kwargs)
                    
                    if target_employee in request.user.get_subordinates():
                        return view_func(request, *args, **kwargs)
                    
                    raise PermissionDenied("Access denied to this employee")
                
                elif role_name in ['PAYROLL_MANAGER', 'ACCOUNTANT']:
                    return view_func(request, *args, **kwargs)
                
                elif role_name == 'AUDITOR':
                    return view_func(request, *args, **kwargs)
                
                else:
                    raise PermissionDenied("Access denied to this employee")
                
            except User.DoesNotExist:
                raise PermissionDenied("Employee not found")
        
        return view_func(request, *args, **kwargs)
    
    return _wrapped_view


def payroll_access_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        
        if not request.user.is_active:
            return redirect('accounts:login')
        
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        
        if not request.user.role:
            raise PermissionDenied("Payroll access denied")
        
        role_name = request.user.role.name
        
        if role_name in ['SUPER_ADMIN', 'HR_ADMIN', 'PAYROLL_MANAGER', 'ACCOUNTANT']:
            return view_func(request, *args, **kwargs)
        
        raise PermissionDenied("Payroll access denied")
    
    return _wrapped_view


def attendance_access_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        
        if not request.user.is_active:
            return redirect('accounts:login')
        
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        
        if not request.user.role:
            employee_id = kwargs.get('employee_id') or request.GET.get('employee_id')
            if employee_id and int(employee_id) == request.user.id:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("Attendance access denied")
        
        role_name = request.user.role.name
        
        if role_name in ['SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER']:
            return view_func(request, *args, **kwargs)
        
        elif role_name == 'DEPARTMENT_MANAGER':
            employee_id = kwargs.get('employee_id') or request.GET.get('employee_id')
            if employee_id:
                try:
                    target_employee = User.objects.get(id=employee_id, is_active=True)
                    if (request.user.department and target_employee.department and
                        request.user.department == target_employee.department):
                        return view_func(request, *args, **kwargs)
                    if target_employee in request.user.get_subordinates():
                        return view_func(request, *args, **kwargs)
                except User.DoesNotExist:
                    pass
            return view_func(request, *args, **kwargs)
        
        else:
            employee_id = kwargs.get('employee_id') or request.GET.get('employee_id')
            if employee_id and int(employee_id) == request.user.id:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("Attendance access denied")
    
    return _wrapped_view


def expense_access_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        
        if not request.user.is_active:
            return redirect('accounts:login')
        
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        
        if not request.user.role:
            employee_id = kwargs.get('employee_id') or request.GET.get('employee_id')
            if employee_id and int(employee_id) == request.user.id:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("Expense access denied")
        
        role_name = request.user.role.name
        
        if role_name in ['SUPER_ADMIN', 'HR_ADMIN', 'ACCOUNTANT']:
            return view_func(request, *args, **kwargs)
        
        elif role_name in ['HR_MANAGER', 'DEPARTMENT_MANAGER']:
            return view_func(request, *args, **kwargs)
        
        else:
            employee_id = kwargs.get('employee_id') or request.GET.get('employee_id')
            if employee_id and int(employee_id) == request.user.id:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("Expense access denied")
    
    return _wrapped_view


def report_access_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        
        if not request.user.is_active:
            return redirect('accounts:login')
        
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        
        if not request.user.role:
            raise PermissionDenied("Report access denied")
        
        role_name = request.user.role.name
        
        if role_name in ['SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER', 'PAYROLL_MANAGER', 
                        'ACCOUNTANT', 'AUDITOR', 'DEPARTMENT_MANAGER']:
            return view_func(request, *args, **kwargs)
        
        raise PermissionDenied("Report access denied")
    
    return _wrapped_view


def account_not_locked_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_account_locked():
            messages.error(request, 'Your account is temporarily locked due to multiple failed login attempts.')
            return redirect('accounts:login')
        return view_func(request, *args, **kwargs)
    
    return _wrapped_view


def password_change_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if (request.user.is_authenticated and 
            request.user.must_change_password and 
            request.resolver_match.url_name not in ['change_password', 'logout']):
            messages.warning(request, 'You must change your password before continuing.')
            return redirect('accounts:change_password')
        return view_func(request, *args, **kwargs)
    
    return _wrapped_view


def has_module_access(user, module_name):
    if user.is_superuser:
        return True
    
    if not user.role:
        return module_name in ['profile']
    
    role_name = user.role.name
    
    module_permissions = {
        'employees': ['SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER', 'DEPARTMENT_MANAGER'],
        'attendance': ['SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER', 'DEPARTMENT_MANAGER'],
        'payroll': ['SUPER_ADMIN', 'HR_ADMIN', 'PAYROLL_MANAGER', 'ACCOUNTANT'],
        'expenses': ['SUPER_ADMIN', 'HR_ADMIN', 'ACCOUNTANT', 'HR_MANAGER', 'DEPARTMENT_MANAGER'],
        'reports': ['SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER', 'PAYROLL_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'DEPARTMENT_MANAGER'],
        'settings': ['SUPER_ADMIN', 'HR_ADMIN'],
        'audit': ['SUPER_ADMIN', 'HR_ADMIN', 'AUDITOR']
    }
    
    return role_name in module_permissions.get(module_name, [])

class BasePermissionMixin:
    def has_permission(self, user, *args, **kwargs):
        return user.is_authenticated and user.is_active

    def handle_no_permission(self, request):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        messages.error(request, 'You do not have permission to access this resource.')
        return redirect('accounts:dashboard')


class SuperAdminRequiredMixin(BasePermissionMixin, UserPassesTestMixin):
    def test_func(self):
        return (self.request.user.is_authenticated and 
                self.request.user.is_active and
                (self.request.user.is_superuser or 
                 (self.request.user.role and self.request.user.role.name == 'SUPER_ADMIN')))


class HRAdminRequiredMixin(BasePermissionMixin, UserPassesTestMixin):
    def test_func(self):
        if not self.request.user.is_authenticated or not self.request.user.is_active:
            return False
        
        if self.request.user.is_superuser:
            return True
        
        if self.request.user.role:
            return self.request.user.role.name in ['SUPER_ADMIN', 'HR_ADMIN']
        
        return False


class HRManagerRequiredMixin(BasePermissionMixin, UserPassesTestMixin):
    def test_func(self):
        if not self.request.user.is_authenticated or not self.request.user.is_active:
            return False
        
        if self.request.user.is_superuser:
            return True
        
        if self.request.user.role:
            return self.request.user.role.name in ['SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER']
        
        return False


class DepartmentManagerRequiredMixin(BasePermissionMixin, UserPassesTestMixin):
    def test_func(self):
        if not self.request.user.is_authenticated or not self.request.user.is_active:
            return False
        
        if self.request.user.is_superuser:
            return True
        
        if self.request.user.role:
            return self.request.user.role.name in [
                'SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER', 'DEPARTMENT_MANAGER'
            ]
        
        return False


class PayrollManagerRequiredMixin(BasePermissionMixin, UserPassesTestMixin):
    def test_func(self):
        if not self.request.user.is_authenticated or not self.request.user.is_active:
            return False
        
        if self.request.user.is_superuser:
            return True
        
        if self.request.user.role:
            return self.request.user.role.name in [
                'SUPER_ADMIN', 'HR_ADMIN', 'PAYROLL_MANAGER'
            ]
        
        return False


class AccountantRequiredMixin(BasePermissionMixin, UserPassesTestMixin):
    def test_func(self):
        if not self.request.user.is_authenticated or not self.request.user.is_active:
            return False
        
        if self.request.user.is_superuser:
            return True
        
        if self.request.user.role:
            return self.request.user.role.name in [
                'SUPER_ADMIN', 'HR_ADMIN', 'PAYROLL_MANAGER', 'ACCOUNTANT'
            ]
        
        return False


class AuditorRequiredMixin(BasePermissionMixin, UserPassesTestMixin):
    def test_func(self):
        if not self.request.user.is_authenticated or not self.request.user.is_active:
            return False
        
        if self.request.user.is_superuser:
            return True
        
        if self.request.user.role:
            return self.request.user.role.name in [
                'SUPER_ADMIN', 'HR_ADMIN', 'AUDITOR'
            ]
        
        return False


class EmployeeAccessMixin(BasePermissionMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_active

    def get_accessible_employees(self, user):
        if user.is_superuser:
            return User.objects.filter(is_active=True)
        
        if not user.role:
            return User.objects.filter(id=user.id)
        
        role_name = user.role.name
        
        if role_name in ['SUPER_ADMIN', 'HR_ADMIN']:
            return User.objects.filter(is_active=True)
        
        elif role_name == 'HR_MANAGER':
            return User.objects.filter(is_active=True)
        
        elif role_name == 'DEPARTMENT_MANAGER':
            if user.department:
                department_employees = user.department.get_all_employees()
                subordinates = user.get_subordinates()
                accessible_ids = set([emp.id for emp in department_employees] + 
                                   [sub.id for sub in subordinates] + [user.id])
                return User.objects.filter(id__in=accessible_ids, is_active=True)
            return User.objects.filter(id=user.id)
        
        elif role_name in ['PAYROLL_MANAGER', 'ACCOUNTANT']:
            return User.objects.filter(is_active=True)
        
        elif role_name == 'AUDITOR':
            return User.objects.filter(is_active=True)
        
        else:
            return User.objects.filter(id=user.id)


class DepartmentAccessMixin(BasePermissionMixin):
    def get_accessible_departments(self, user):
        from .models import Department
        
        if user.is_superuser:
            return Department.objects.filter(is_active=True)
        
        if not user.role:
            if user.department:
                return Department.objects.filter(id=user.department.id)
            return Department.objects.none()
        
        role_name = user.role.name
        
        if role_name in ['SUPER_ADMIN', 'HR_ADMIN', 'HR_MANAGER']:
            return Department.objects.filter(is_active=True)
        
        elif role_name == 'DEPARTMENT_MANAGER':
            if user.department:
                departments = [user.department]
                departments.extend(user.department.sub_departments.filter(is_active=True))
                return Department.objects.filter(
                    id__in=[dept.id for dept in departments]
                )
            return Department.objects.none()
        
        elif role_name in ['PAYROLL_MANAGER', 'ACCOUNTANT', 'AUDITOR']:
            return Department.objects.filter(is_active=True)
        
        else:
            if user.department:
                return Department.objects.filter(id=user.department.id)
            return Department.objects.none()


class RoleBasedAccessMixin(BasePermissionMixin):
    required_roles = []
    
    def test_func(self):
        if not self.request.user.is_authenticated or not self.request.user.is_active:
            return False
        
        if self.request.user.is_superuser:
            return True
        
        if not self.request.user.role:
            return False
        
        return self.request.user.role.name in self.required_roles


class PermissionRequiredMixin(BasePermissionMixin):
    required_permission = None
    
    def test_func(self):
        if not self.request.user.is_authenticated or not self.request.user.is_active:
            return False
        
        if self.request.user.is_superuser:
            return True
        
        if self.required_permission:
            return self.request.user.has_permission(self.required_permission)
        
        return False


class ModuleAccessMixin(BasePermissionMixin):
    required_module = None
    
    def test_func(self):
        if not self.request.user.is_authenticated or not self.request.user.is_active:
            return False
        
        if self.required_module:
            return has_module_access(self.request.user, self.required_module)
        
        return True


class AccountStatusMixin(BasePermissionMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if request.user.is_account_locked():
                messages.error(request, 'Your account is temporarily locked.')
                return redirect('accounts:login')
            
            if (request.user.must_change_password and 
                request.resolver_match.url_name not in ['change_password', 'logout']):
                messages.warning(request, 'You must change your password.')
                return redirect('accounts:change_password')
        
        return super().dispatch(request, *args, **kwargs)


class AuditMixin:
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        
        if request.user.is_authenticated and hasattr(self, 'audit_action'):
            from .models import AuditLog
            AuditLog.log_action(
                user=request.user,
                action=self.audit_action,
                description=f"Accessed {self.__class__.__name__}",
                ip_address=self.get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )
        
        return response
    
    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class DataAccessMixin(BasePermissionMixin):
    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        
        if user.is_superuser:
            return queryset
        
        if not user.role:
            return queryset.filter(created_by=user)
        
        role_name = user.role.name
        
        if role_name in ['SUPER_ADMIN', 'HR_ADMIN']:
            return queryset
        
        elif role_name == 'DEPARTMENT_MANAGER':
            if user.department:
                department_users = user.department.get_all_employees()
                return queryset.filter(
                    Q(created_by__in=department_users) | Q(created_by=user)
                )
            return queryset.filter(created_by=user)
        
        else:
            return queryset.filter(created_by=user)
