from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from accounts.models import CustomUser
from .models import EmployeeProfile, Education, Contract


class EmployeePermissions:

    @staticmethod
    def can_view_employee_profile(user, employee_profile=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if employee_profile and employee_profile.user == user:
            return True

        if user.role and user.role.name == "DEPARTMENT_MANAGER":
            if employee_profile and employee_profile.user.department == user.department:
                return True

        if employee_profile and employee_profile.user.manager == user:
            return True

        return False

    @staticmethod
    def can_edit_employee_profile(user, employee_profile=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if user.role and user.role.name == "DEPARTMENT_MANAGER":
            if employee_profile and employee_profile.user.department == user.department:
                return True

        return False

    @staticmethod
    def can_delete_employee_profile(user, employee_profile=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name == "HR_ADMIN":
            return True

        return False

    @staticmethod
    def can_view_salary_info(user, employee_profile=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in [
            "HR_ADMIN",
            "HR_MANAGER",
            "PAYROLL_MANAGER",
        ]:
            return True

        if employee_profile and employee_profile.user == user:
            return True

        return False

    @staticmethod
    def can_edit_salary_info(user, employee_profile=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "PAYROLL_MANAGER"]:
            return True

        return False

    @staticmethod
    def can_confirm_employee(user, employee_profile=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if user.role and user.role.name == "DEPARTMENT_MANAGER":
            if employee_profile and employee_profile.user.department == user.department:
                return True

        return False


class EducationPermissions:

    @staticmethod
    def can_view_education(user, education=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if education and education.employee == user:
            return True

        if user.role and user.role.name == "DEPARTMENT_MANAGER":
            if education and education.employee.department == user.department:
                return True

        if education and education.employee.manager == user:
            return True

        return False

    @staticmethod
    def can_add_education(user, employee=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if employee and employee == user:
            return True

        return False

    @staticmethod
    def can_edit_education(user, education=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if education and education.employee == user and not education.is_verified:
            return True

        return False

    @staticmethod
    def can_delete_education(user, education=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name == "HR_ADMIN":
            return True

        if education and education.employee == user and not education.is_verified:
            return True

        return False

    @staticmethod
    def can_verify_education(user, education=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return False


class ContractPermissions:

    @staticmethod
    def can_view_contract(user, contract=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if contract and contract.employee == user:
            return True

        if user.role and user.role.name == "DEPARTMENT_MANAGER":
            if contract and contract.employee.department == user.department:
                return True

        if contract and contract.employee.manager == user:
            return True

        return False

    @staticmethod
    def can_create_contract(user, employee=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return False

    @staticmethod
    def can_edit_contract(user, contract=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        if contract and contract.status == "DRAFT":
            if user.role and user.role.name == "DEPARTMENT_MANAGER":
                if contract.employee.department == user.department:
                    return True

        return False

    @staticmethod
    def can_activate_contract(user, contract=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return False

    @staticmethod
    def can_terminate_contract(user, contract=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return False

    @staticmethod
    def can_renew_contract(user, contract=None):
        if user.is_superuser:
            return True

        if user.role and user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return True

        return False


def check_employee_permission(user, permission_type, obj=None):
    permission_map = {
        "view_employee": EmployeePermissions.can_view_employee_profile,
        "edit_employee": EmployeePermissions.can_edit_employee_profile,
        "delete_employee": EmployeePermissions.can_delete_employee_profile,
        "view_salary": EmployeePermissions.can_view_salary_info,
        "edit_salary": EmployeePermissions.can_edit_salary_info,
        "confirm_employee": EmployeePermissions.can_confirm_employee,
        "view_education": EducationPermissions.can_view_education,
        "add_education": EducationPermissions.can_add_education,
        "edit_education": EducationPermissions.can_edit_education,
        "delete_education": EducationPermissions.can_delete_education,
        "verify_education": EducationPermissions.can_verify_education,
        "view_contract": ContractPermissions.can_view_contract,
        "create_contract": ContractPermissions.can_create_contract,
        "edit_contract": ContractPermissions.can_edit_contract,
        "activate_contract": ContractPermissions.can_activate_contract,
        "terminate_contract": ContractPermissions.can_terminate_contract,
        "renew_contract": ContractPermissions.can_renew_contract,
    }

    permission_func = permission_map.get(permission_type)
    if permission_func:
        return permission_func(user, obj)

    return False


def require_employee_permission(permission_type):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            obj = None

            if "pk" in kwargs:
                try:
                    if "employee" in permission_type:
                        obj = EmployeeProfile.objects.get(pk=kwargs["pk"])
                    elif "education" in permission_type:
                        obj = Education.objects.get(pk=kwargs["pk"])
                    elif "contract" in permission_type:
                        obj = Contract.objects.get(pk=kwargs["pk"])
                except (
                    EmployeeProfile.DoesNotExist,
                    Education.DoesNotExist,
                    Contract.DoesNotExist,
                ):
                    raise PermissionDenied("Object not found")

            if not check_employee_permission(request.user, permission_type, obj):
                raise PermissionDenied(
                    "You don't have permission to perform this action"
                )

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def create_employee_permissions():
    employee_content_type = ContentType.objects.get_for_model(EmployeeProfile)
    education_content_type = ContentType.objects.get_for_model(Education)
    contract_content_type = ContentType.objects.get_for_model(Contract)

    permissions = [
        (
            "view_employee_salary",
            "Can view employee salary information",
            employee_content_type,
        ),
        (
            "edit_employee_salary",
            "Can edit employee salary information",
            employee_content_type,
        ),
        (
            "confirm_employee",
            "Can confirm employee from probation",
            employee_content_type,
        ),
        (
            "bulk_update_employees",
            "Can perform bulk updates on employees",
            employee_content_type,
        ),
        ("export_employee_data", "Can export employee data", employee_content_type),
        ("verify_education", "Can verify education records", education_content_type),
        ("activate_contract", "Can activate contracts", contract_content_type),
        ("terminate_contract", "Can terminate contracts", contract_content_type),
        ("renew_contract", "Can renew contracts", contract_content_type),
    ]

    created_permissions = []
    for codename, name, content_type in permissions:
        permission, created = Permission.objects.get_or_create(
            codename=codename, content_type=content_type, defaults={"name": name}
        )
        if created:
            created_permissions.append(permission)

    return created_permissions


def assign_role_permissions():
    from accounts.models import Role

    try:
        hr_admin_role = Role.objects.get(name="HR_ADMIN")
        hr_manager_role = Role.objects.get(name="HR_MANAGER")
        dept_manager_role = Role.objects.get(name="DEPARTMENT_MANAGER")
        payroll_manager_role = Role.objects.get(name="PAYROLL_MANAGER")

        employee_permissions = Permission.objects.filter(
            content_type__app_label="employees"
        )

        hr_admin_role.permissions.add(*employee_permissions)

        hr_manager_permissions = employee_permissions.exclude(
            codename__in=["delete_employeeprofile", "delete_contract"]
        )
        hr_manager_role.permissions.add(*hr_manager_permissions)

        dept_manager_permissions = employee_permissions.filter(
            codename__in=[
                "view_employeeprofile",
                "view_education",
                "view_contract",
                "confirm_employee",
            ]
        )
        dept_manager_role.permissions.add(*dept_manager_permissions)

        payroll_permissions = employee_permissions.filter(
            codename__in=[
                "view_employeeprofile",
                "view_employee_salary",
                "edit_employee_salary",
            ]
        )
        payroll_manager_role.permissions.add(*payroll_permissions)

    except Role.DoesNotExist:
        pass


class EmployeePermissionMixin:
    def has_view_permission(self, request, obj=None):
        if obj:
            return check_employee_permission(request.user, "view_employee", obj)
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj:
            return check_employee_permission(request.user, "edit_employee", obj)
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj:
            return check_employee_permission(request.user, "delete_employee", obj)
        return super().has_delete_permission(request, obj)


class EducationPermissionMixin:
    def has_view_permission(self, request, obj=None):
        if obj:
            return check_employee_permission(request.user, "view_education", obj)
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj:
            return check_employee_permission(request.user, "edit_education", obj)
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj:
            return check_employee_permission(request.user, "delete_education", obj)
        return super().has_delete_permission(request, obj)


class ContractPermissionMixin:
    def has_view_permission(self, request, obj=None):
        if obj:
            return check_employee_permission(request.user, "view_contract", obj)
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj:
            return check_employee_permission(request.user, "edit_contract", obj)
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj:
            return check_employee_permission(request.user, "delete_contract", obj)
        return super().has_delete_permission(request, obj)
