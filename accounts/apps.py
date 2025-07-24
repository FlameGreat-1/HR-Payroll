from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.core.management.color import no_style
from django.db import connection
from django.core.checks import Error, Warning, register
import logging

logger = logging.getLogger(__name__)


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "HR Account Management System"

    def ready(self):
        try:
            import accounts.signals

            post_migrate.connect(
                self.create_initial_data,
                sender=self,
                dispatch_uid="accounts_create_initial_data",
            )

            register(self.check_required_settings, deploy=True)

            logger.info("Accounts app initialized successfully")

        except Exception as e:
            logger.error(f"Error initializing accounts app: {e}")

    def create_initial_data(self, sender, **kwargs):
        try:
            from django.contrib.auth import get_user_model
            from accounts.models import Role, Department, SystemConfiguration
            from accounts.utils import generate_secure_password
            from django.contrib.auth.models import Permission

            User = get_user_model()

            default_roles = [
                {
                    "name": "SUPER_ADMIN",
                    "display_name": "Super Administrator",
                    "description": "Full system access with all permissions",
                },
                {
                    "name": "HR_ADMIN",
                    "display_name": "HR Administrator",
                    "description": "HR management with employee and payroll access",
                },
                {
                    "name": "HR_MANAGER",
                    "display_name": "HR Manager",
                    "description": "HR operations and employee management",
                },
                {
                    "name": "DEPARTMENT_MANAGER",
                    "display_name": "Department Manager",
                    "description": "Department-level employee management",
                },
                {
                    "name": "PAYROLL_MANAGER",
                    "display_name": "Payroll Manager",
                    "description": "Payroll processing and salary management",
                },
                {
                    "name": "EMPLOYEE",
                    "display_name": "Employee",
                    "description": "Basic employee access to personal information",
                },
                {
                    "name": "AUDITOR",
                    "display_name": "System Auditor",
                    "description": "Read-only access for audit and compliance",
                },
            ]

            for role_data in default_roles:
                role, created = Role.objects.get_or_create(
                    name=role_data["name"],
                    defaults={
                        "display_name": role_data["display_name"],
                        "description": role_data["description"],
                        "is_active": True,
                    },
                )
                if created:
                    logger.info(f"Created default role: {role.display_name}")

            default_departments = [
                {
                    "code": "HR",
                    "name": "Human Resources",
                    "description": "Human Resources Department",
                },
                {
                    "code": "IT",
                    "name": "Information Technology",
                    "description": "IT Department",
                },
                {"code": "FIN", "name": "Finance", "description": "Finance Department"},
                {
                    "code": "OPS",
                    "name": "Operations",
                    "description": "Operations Department",
                },
            ]

            for dept_data in default_departments:
                dept, created = Department.objects.get_or_create(
                    code=dept_data["code"],
                    defaults={
                        "name": dept_data["name"],
                        "description": dept_data["description"],
                        "is_active": True,
                    },
                )
                if created:
                    logger.info(f"Created default department: {dept.name}")

            default_configs = [
                (
                    "COMPANY_NAME",
                    "HR Payroll System",
                    "Company name displayed in the system",
                ),
                ("COMPANY_ADDRESS", "", "Company physical address"),
                ("COMPANY_PHONE", "", "Company contact phone number"),
                ("COMPANY_EMAIL", "", "Company contact email address"),
                ("WORKING_HOURS_PER_DAY", "8.00", "Standard working hours per day"),
                ("WORKING_DAYS_PER_WEEK", "5", "Standard working days per week"),
                ("OVERTIME_RATE", "1.50", "Overtime pay rate multiplier"),
                ("LATE_PENALTY_RATE", "0.00", "Late arrival penalty rate per hour"),
                ("PASSWORD_EXPIRY_DAYS", "90", "Password expiration period in days"),
                (
                    "PASSWORD_EXPIRY_WARNING_DAYS",
                    "7",
                    "Days before password expiry to show warning",
                ),
                (
                    "MAX_LOGIN_ATTEMPTS",
                    "5",
                    "Maximum failed login attempts before account lock",
                ),
                (
                    "ACCOUNT_LOCKOUT_DURATION",
                    "30",
                    "Account lockout duration in minutes",
                ),
                ("SESSION_TIMEOUT", "30", "Session timeout in minutes"),
                (
                    "MAX_CONCURRENT_SESSIONS",
                    "3",
                    "Maximum concurrent sessions per user",
                ),
                (
                    "AUDIT_LOG_RETENTION_DAYS",
                    "365",
                    "Audit log retention period in days",
                ),
                (
                    "SECURITY_ALERT_THRESHOLD",
                    "10",
                    "Security events threshold for alerts",
                ),
                ("MIN_EMPLOYEE_AGE", "18", "Minimum employee age requirement"),
                ("MAX_EMPLOYEE_AGE", "65", "Maximum employee age limit"),
                ("MAX_UPLOAD_SIZE_MB", "10", "Maximum file upload size in MB"),
                (
                    "ALLOWED_FILE_TYPES",
                    "pdf,doc,docx,jpg,jpeg,png,xlsx,xls",
                    "Allowed file upload types",
                ),
                ("EMAIL_NOTIFICATIONS_ENABLED", "true", "Enable email notifications"),
                ("SYSTEM_MAINTENANCE_MODE", "false", "System maintenance mode flag"),
            ]

            for key, value, description in default_configs:
                config, created = SystemConfiguration.objects.get_or_create(
                    key=key,
                    defaults={
                        "value": value,
                        "description": description,
                        "is_active": True,
                    },
                )
                if created:
                    logger.info(f"Created system configuration: {key}")

            if not User.objects.exists():
                try:
                    hr_dept = Department.objects.get(code="HR")
                    super_admin_role = Role.objects.get(name="SUPER_ADMIN")

                    secure_password = generate_secure_password()

                    superuser = User.objects.create(
                        username="ADMIN001",
                        employee_code="ADMIN001",
                        email="admin@company.com",
                        first_name="System",
                        last_name="Administrator",
                        department=hr_dept,
                        role=super_admin_role,
                        is_active=True,
                        is_verified=True,
                        status="ACTIVE",
                        is_staff=True,
                        is_superuser=True,
                        must_change_password=True,
                    )
                    superuser.set_password(secure_password)
                    superuser.save()

                    logger.info(
                        f"Created default superuser: ADMIN001 with secure password: {secure_password}"
                    )

                except Exception as e:
                    logger.error(f"Error creating superuser: {e}")

            self.assign_role_permissions()

            logger.info("Initial data creation completed successfully")

        except Exception as e:
            logger.error(f"Error creating initial data: {e}")

    def assign_role_permissions(self):
        try:
            from accounts.models import Role
            from django.contrib.auth.models import Permission

            super_admin = Role.objects.filter(name="SUPER_ADMIN").first()
            hr_admin = Role.objects.filter(name="HR_ADMIN").first()
            hr_manager = Role.objects.filter(name="HR_MANAGER").first()
            dept_manager = Role.objects.filter(name="DEPARTMENT_MANAGER").first()
            payroll_manager = Role.objects.filter(name="PAYROLL_MANAGER").first()
            auditor = Role.objects.filter(name="AUDITOR").first()

            if super_admin:
                super_admin.permissions.set(Permission.objects.all())
                logger.info("Assigned all permissions to Super Admin role")

            if hr_admin:
                hr_permissions = Permission.objects.filter(
                    content_type__app_label__in=["accounts", "auth"]
                ).exclude(
                    codename__in=[
                        "add_permission",
                        "change_permission",
                        "delete_permission",
                    ]
                )
                hr_admin.permissions.set(hr_permissions)
                logger.info("Assigned HR permissions to HR Admin role")

            if hr_manager:
                hr_manager_permissions = Permission.objects.filter(
                    content_type__app_label="accounts",
                    codename__in=[
                        "view_user",
                        "add_user",
                        "change_user",
                        "view_department",
                        "view_role",
                        "view_auditlog",
                    ],
                )
                hr_manager.permissions.set(hr_manager_permissions)
                logger.info("Assigned permissions to HR Manager role")

            if dept_manager:
                dept_permissions = Permission.objects.filter(
                    content_type__app_label="accounts",
                    codename__in=["view_user", "change_user", "view_department"],
                )
                dept_manager.permissions.set(dept_permissions)
                logger.info("Assigned permissions to Department Manager role")

            if payroll_manager:
                payroll_permissions = Permission.objects.filter(
                    content_type__app_label="accounts",
                    codename__in=["view_user", "view_department"],
                )
                payroll_manager.permissions.set(payroll_permissions)
                logger.info("Assigned permissions to Payroll Manager role")

            if auditor:
                audit_permissions = Permission.objects.filter(
                    codename__startswith="view_"
                )
                auditor.permissions.set(audit_permissions)
                logger.info("Assigned read-only permissions to Auditor role")

        except Exception as e:
            logger.error(f"Error assigning role permissions: {e}")

    def check_required_settings(self, app_configs, **kwargs):
        errors = []
        warnings = []

        try:
            from django.conf import settings

            required_settings = [
                "SECRET_KEY",
                "DATABASES",
                "INSTALLED_APPS",
                "MIDDLEWARE",
                "AUTH_USER_MODEL",
            ]

            for setting in required_settings:
                if not hasattr(settings, setting):
                    errors.append(
                        Error(
                            f"Missing required setting: {setting}",
                            id="accounts.E001",
                        )
                    )

            if hasattr(settings, "INSTALLED_APPS"):
                if "accounts" not in settings.INSTALLED_APPS:
                    errors.append(
                        Error(
                            "'accounts' app not found in INSTALLED_APPS",
                            id="accounts.E002",
                        )
                    )

            if hasattr(settings, "AUTH_USER_MODEL"):
                if settings.AUTH_USER_MODEL != "accounts.User":
                    errors.append(
                        Error(
                            "AUTH_USER_MODEL should be set to 'accounts.User'",
                            id="accounts.E003",
                        )
                    )

            if hasattr(settings, "DATABASES"):
                default_db = settings.DATABASES.get("default", {})
                if default_db.get("ENGINE") != "django.db.backends.postgresql":
                    warnings.append(
                        Warning(
                            "PostgreSQL database engine is recommended for production",
                            id="accounts.W001",
                        )
                    )

            if hasattr(settings, "CACHES"):
                default_cache = settings.CACHES.get("default", {})
                if (
                    default_cache.get("BACKEND")
                    != "django.core.cache.backends.redis.RedisCache"
                ):
                    warnings.append(
                        Warning(
                            "Redis cache backend is recommended for production",
                            id="accounts.W002",
                        )
                    )

        except Exception as e:
            logger.error(f"Error during system checks: {e}")
            errors.append(
                Error(
                    f"System check error: {str(e)}",
                    id="accounts.E999",
                )
            )

        return errors + warnings
