from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from accounts.models import CustomUser, Role
from functools import wraps


class PayrollPermissionMixin:
    def has_payroll_permission(self, user, permission_name):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        if user.role and user.role.can_manage_payroll:
            return True

        return user.has_perm(f"payroll.{permission_name}")


class PayrollPermissions:
    VIEW_PAYROLL = "view_payroll"
    ADD_PAYROLL = "add_payroll"
    CHANGE_PAYROLL = "change_payroll"
    DELETE_PAYROLL = "delete_payroll"
    PROCESS_PAYROLL = "process_payroll"
    APPROVE_PAYROLL = "approve_payroll"
    EXPORT_PAYROLL = "export_payroll"
    VIEW_ALL_PAYROLL = "view_all_payroll"
    MANAGE_SALARY_ADVANCE = "manage_salary_advance"
    VIEW_PAYROLL_REPORTS = "view_payroll_reports"


class PayrollAccessControl:
    @staticmethod
    def can_view_payroll(user, employee=None):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        if user.role and user.role.can_manage_payroll:
            return True

        if employee and user == employee:
            return True

        if user.role and user.role.can_view_all_data:
            return True

        return False

    @staticmethod
    def can_process_payroll(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        if user.role and user.role.can_manage_payroll:
            return True

        return user.has_perm("payroll.process_payroll")

    @staticmethod
    def can_approve_payroll(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        if user.role and user.role.name in ["SUPER_ADMIN", "MANAGER"]:
            return True

        return user.has_perm("payroll.approve_payroll")

    @staticmethod
    def can_manage_salary_advance(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        if user.role and user.role.can_manage_payroll:
            return True

        return user.has_perm("payroll.manage_salary_advance")

    @staticmethod
    def can_export_payroll(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        if user.role and user.role.can_manage_payroll:
            return True

        return user.has_perm("payroll.export_payroll")

    @staticmethod
    def can_view_payroll_reports(user):
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        if user.role and user.role.can_view_all_data:
            return True

        return user.has_perm("payroll.view_payroll_reports")

    @staticmethod
    def get_accessible_employees(user):
        if not user or not user.is_authenticated:
            return CustomUser.objects.none()

        if user.is_superuser:
            return CustomUser.active.all()

        if user.role and user.role.can_view_all_data:
            return CustomUser.active.all()

        if user.role and user.role.name == "MANAGER":
            subordinates = user.get_subordinates()
            return CustomUser.active.filter(
                models.Q(id=user.id)
                | models.Q(id__in=subordinates.values_list("id", flat=True))
            )

        return CustomUser.active.filter(id=user.id)


def require_payroll_permission(permission_name):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not PayrollAccessControl.can_process_payroll(request.user):
                raise PermissionDenied(
                    "You don't have permission to access payroll data"
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def require_payroll_approval_permission(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not PayrollAccessControl.can_approve_payroll(request.user):
            raise PermissionDenied("You don't have permission to approve payroll")
        return view_func(request, *args, **kwargs)

    return wrapper


def require_payroll_export_permission(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not PayrollAccessControl.can_export_payroll(request.user):
            raise PermissionDenied("You don't have permission to export payroll data")
        return view_func(request, *args, **kwargs)

    return wrapper


class PayrollPermissionManager:
    @staticmethod
    def create_payroll_permissions():
        payroll_permissions = [
            ("view_payroll", "Can view payroll"),
            ("add_payroll", "Can add payroll"),
            ("change_payroll", "Can change payroll"),
            ("delete_payroll", "Can delete payroll"),
            ("process_payroll", "Can process payroll"),
            ("approve_payroll", "Can approve payroll"),
            ("export_payroll", "Can export payroll"),
            ("view_all_payroll", "Can view all payroll data"),
            ("manage_salary_advance", "Can manage salary advances"),
            ("view_payroll_reports", "Can view payroll reports"),
        ]

        created_permissions = []
        for codename, name in payroll_permissions:
            try:
                content_type = ContentType.objects.get(
                    app_label="payroll", model="payrollperiod"
                )
                permission, created = Permission.objects.get_or_create(
                    codename=codename,
                    content_type=content_type,
                    defaults={"name": name},
                )
                if created:
                    created_permissions.append(permission)
            except ContentType.DoesNotExist:
                continue

        return created_permissions

    @staticmethod
    def assign_permissions_to_role(role_name, permission_codenames):
        try:
            role = Role.objects.get(name=role_name.upper())
            permissions = Permission.objects.filter(
                codename__in=permission_codenames, content_type__app_label="payroll"
            )
            role.permissions.add(*permissions)
            return True
        except Role.DoesNotExist:
            return False

    @staticmethod
    def setup_default_payroll_permissions():
        PayrollPermissionManager.create_payroll_permissions()

        admin_permissions = [
            "view_payroll",
            "add_payroll",
            "change_payroll",
            "delete_payroll",
            "process_payroll",
            "approve_payroll",
            "export_payroll",
            "view_all_payroll",
            "manage_salary_advance",
            "view_payroll_reports",
        ]

        manager_permissions = [
            "view_payroll",
            "process_payroll",
            "approve_payroll",
            "export_payroll",
            "view_payroll_reports",
        ]

        PayrollPermissionManager.assign_permissions_to_role(
            "SUPER_ADMIN", admin_permissions
        )
        PayrollPermissionManager.assign_permissions_to_role(
            "MANAGER", manager_permissions
        )
