from django.contrib.auth.models import AbstractUser, Group, Permission
from django.db import models
from django.core.validators import RegexValidator, EmailValidator
from django.contrib.auth.models import BaseUserManager
from django.utils import timezone
from django.core.exceptions import ValidationError
import uuid
from datetime import timedelta
import secrets
import hashlib


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class ActiveUserManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True, status="ACTIVE")


class CustomUserManager(BaseUserManager):
    def create_user(self, employee_code, email, password=None, **extra_fields):
        if not employee_code:
            raise ValueError("Employee code is required")
        if not email:
            raise ValueError("Email is required")

        email = self.normalize_email(email)

        username = extra_fields.pop("username", employee_code)

        user = self.model(
            employee_code=employee_code,
            email=email,
            username=username,
            **extra_fields,
        )
        user.set_password(password)
        user._skip_validation = True
        user.save(using=self._db)
        return user

    def create_superuser(self, employee_code, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_verified", True)
        extra_fields.setdefault("status", "ACTIVE")

        return self.create_user(employee_code, email, password, **extra_fields)


class Department(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True, null=True)
    manager = models.ForeignKey(
        "CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_departments",
    )
    parent_department = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sub_departments",
    )
    budget = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    location = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        "CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_departments",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "departments"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["code"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        if self.parent_department == self:
            raise ValidationError("Department cannot be its own parent")

        parent = self.parent_department
        while parent:
            if parent == self:
                raise ValidationError("Circular department hierarchy detected")
            parent = parent.parent_department

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def soft_delete(self):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])

    def get_all_employees(self):
        departments = [self.id]

        def get_sub_departments(dept_id):
            sub_depts = Department.objects.filter(
                parent_department_id=dept_id
            ).values_list("id", flat=True)
            for sub_dept in sub_depts:
                departments.append(sub_dept)
                get_sub_departments(sub_dept)

        get_sub_departments(self.id)
        return CustomUser.objects.filter(department_id__in=departments, is_active=True)


class Role(models.Model):
    ROLE_TYPES = [
        ("SUPER_ADMIN", "Super Admin"),
        ("MANAGER", "Manager"),
        ("CASHIER", "Cashier"),
        ("SALESMAN", "Salesman"),
        ("OTHER_STAFF", "Other Staff"),
        ("CLEANER", "Cleaner"),
        ("DRIVER", "Driver"),
        ("ASSISTANT", "Assistant"),
        ("STOREKEEPER", "Storekeeper"),
        ("OFFICE_WORKER", "Office Worker"),
    ]

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=50, choices=ROLE_TYPES, unique=True)
    display_name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    level = models.PositiveIntegerField(default=1)
    can_manage_employees = models.BooleanField(default=False)
    can_view_all_data = models.BooleanField(default=False)
    can_approve_leave = models.BooleanField(default=False)
    can_manage_payroll = models.BooleanField(default=False)
    permissions = models.ManyToManyField(
        Permission, blank=True, related_name="custom_roles"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        "CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_roles",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "roles"
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name

    def soft_delete(self):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])

    def get_permission_codenames(self):
        return list(self.permissions.values_list("codename", flat=True))


class CustomUser(AbstractUser):
    GENDER_CHOICES = [
        ("M", "Male"),
        ("F", "Female"),
        ("O", "Other"),
    ]

    STATUS_CHOICES = [
        ("ACTIVE", "Active"),
        ("INACTIVE", "Inactive"),
        ("SUSPENDED", "Suspended"),
        ("TERMINATED", "Terminated"),
    ]

    username = models.CharField(max_length=20, unique=True, null=True, blank=True)
    employee_code = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        validators=[
            RegexValidator(
                r"^[A-Z0-9]{3,20}$",
                "Employee code must be 3-20 characters, alphanumeric uppercase only",
            )
        ],
    )

    first_name = models.CharField(max_length=50, blank=True)
    last_name = models.CharField(max_length=50, blank=True)
    middle_name = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(
        unique=True, null=True, blank=True, validators=[EmailValidator()]
    )
    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        validators=[
            RegexValidator(r"^\+?[1-9]\d{1,14}$", "Enter a valid phone number")
        ],
    )
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(
        max_length=1, choices=GENDER_CHOICES, blank=True, null=True
    )

    address_line1 = models.CharField(max_length=255, blank=True, null=True)
    address_line2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    country = models.CharField(max_length=100, default="Sri Lanka")

    emergency_contact_name = models.CharField(max_length=100, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=15, blank=True, null=True)
    emergency_contact_relationship = models.CharField(
        max_length=50, blank=True, null=True
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees",
    )
    role = models.ForeignKey(
        Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="users"
    )
    job_title = models.CharField(max_length=100, blank=True, null=True)
    hire_date = models.DateField(null=True, blank=True)
    termination_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE")

    manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subordinates",
    )

    is_verified = models.BooleanField(default=False)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    account_locked_until = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    must_change_password = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_users",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()
    active = ActiveUserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email", "first_name", "last_name"]

    class Meta:
        db_table = "users"
        ordering = ["employee_code"]
        indexes = [
            models.Index(fields=["employee_code"]),
            models.Index(fields=["email"]),
            models.Index(fields=["status"]),
            models.Index(fields=["department"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["hire_date"]),
        ]

    def __str__(self):
        if self.employee_code:
            return f"{self.employee_code} - {self.get_full_name()}"
        return self.username or f"User {self.id}"

    def generate_employee_code(self):
        if self.employee_code:
            return self.employee_code

        if self.is_superuser or (self.role and self.role.name == "SUPER_ADMIN"):
            prefix = "ADMIN"
        elif self.role and self.role.name == "MANAGER":
            prefix = "MGR"
        elif self.role and self.role.name == "CASHIER":
            prefix = "CASH"
        elif self.role and self.role.name == "SALESMAN":
            prefix = "SALES"
        elif self.role and self.role.name == "OTHER_STAFF":
            prefix = "STAFF"
        elif self.role and self.role.name == "CLEANER":
            prefix = "CLEAN"
        elif self.role and self.role.name == "DRIVER":
            prefix = "DRV"
        elif self.role and self.role.name == "ASSISTANT":
            prefix = "ASST"
        elif self.role and self.role.name == "STOREKEEPER":
            prefix = "STORE"
        elif self.role and self.role.name == "OFFICE_WORKER":
            prefix = "OFFICE"
        else:
            prefix = "EMP"

        existing_codes = CustomUser.objects.filter(
            employee_code__startswith=prefix
        ).values_list("employee_code", flat=True)

        numbers = []
        for code in existing_codes:
            try:
                number = int(code.replace(prefix, ""))
                numbers.append(number)
            except ValueError:
                continue

        next_number = max(numbers) + 1 if numbers else 1

        return f"{prefix}{next_number:03d}"

    def save(self, *args, **kwargs):
        if (
            not self.pk
            and not self.employee_code
            and not self.first_name
            and not self.last_name
            and not self.email
            and getattr(self, "username", None) in [None, "", "AnonymousUser"]
        ):
            super(AbstractUser, self).save(*args, **kwargs)
            return

        if getattr(self, "_skip_validation", False):
            super(AbstractUser, self).save(*args, **kwargs)
            return

        if self.username == "AnonymousUser":
            super(AbstractUser, self).save(*args, **kwargs)
            return

        self.full_clean()

        if not self.employee_code and (self.role or self.is_superuser):
            self.employee_code = self.generate_employee_code()

        if self.employee_code:
            self.username = self.employee_code

        if self.pk:
            try:
                old_user = CustomUser.objects.get(pk=self.pk)
                if old_user.password != self.password:
                    self.password_changed_at = timezone.now()
                    self.must_change_password = False
            except CustomUser.DoesNotExist:
                pass

        super().save(*args, **kwargs)

    def clean(self):
        if (
            not self.employee_code
            and not self.first_name
            and not self.last_name
            and not self.email
        ):
            return

        if self.username == "AnonymousUser":
            return

        if self.hire_date and self.hire_date > timezone.now().date():
            raise ValidationError("Hire date cannot be in the future")

        if (
            self.hire_date
            and self.termination_date
            and self.termination_date <= self.hire_date
        ):
            raise ValidationError("Termination date must be after hire date")

        if self.manager == self:
            raise ValidationError("User cannot be their own manager")

    def soft_delete(self):
        self.is_active = False
        self.status = "TERMINATED"
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "status", "deleted_at"])

    def get_full_name(self):
        if self.middle_name:
            return f"{self.first_name} {self.middle_name} {self.last_name}"
        return f"{self.first_name} {self.last_name}"

    def get_display_name(self):
        return f"{self.get_full_name()} ({self.employee_code})"

    @property
    def is_manager(self):
        return self.subordinates.filter(is_active=True).exists()

    @property
    def is_hr_admin(self):
        return self.role and self.role.name in ["SUPER_ADMIN"]

    @property
    def is_department_manager(self):
        return self.role and self.role.name == "MANAGER"

    def is_account_locked(self):
        if self.account_locked_until:
            return timezone.now() < self.account_locked_until
        return False

    def lock_account(self, duration_minutes=None):
        if duration_minutes is None:
            try:
                duration_minutes = int(
                    SystemConfiguration.get_setting("ACCOUNT_LOCKOUT_DURATION", "30")
                )
            except:
                duration_minutes = 30
        self.account_locked_until = timezone.now() + timedelta(minutes=duration_minutes)
        self.save(update_fields=["account_locked_until"])

    def unlock_account(self):
        self.account_locked_until = None
        self.failed_login_attempts = 0
        self.save(update_fields=["account_locked_until", "failed_login_attempts"])

    def increment_failed_login(self):
        try:
            max_attempts = int(
                SystemConfiguration.get_setting("MAX_LOGIN_ATTEMPTS", "5")
            )
        except:
            max_attempts = 5
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            self.lock_account()
        self.save(update_fields=["failed_login_attempts"])

    def reset_failed_login(self):
        if self.failed_login_attempts > 0:
            self.failed_login_attempts = 0
            self.save(update_fields=["failed_login_attempts"])

    def has_permission(self, permission_codename):
        if self.is_superuser:
            return True
        if self.role:
            return permission_codename in self.role.get_permission_codenames()
        return False

    def get_subordinates(self):
        subordinate_ids = []

        def collect_subordinates(manager_id):
            direct_subs = CustomUser.objects.filter(
                manager_id=manager_id, is_active=True
            ).values_list("id", flat=True)

            for sub_id in direct_subs:
                subordinate_ids.append(sub_id)
                collect_subordinates(sub_id)

        collect_subordinates(self.id)
        return CustomUser.objects.filter(id__in=subordinate_ids)

    def can_manage_user(self, target_user):
        if self.is_superuser:
            return True

        if self.role and self.role.name == "SUPER_ADMIN":
            return True

        if self.role and self.role.name == "MANAGER":
            if self.department and target_user.department == self.department:
                return True

        if target_user.manager == self:
            return True

        return False

    def is_password_expired(self, days=None):
        if days is None:
            try:
                days = int(
                    SystemConfiguration.get_setting("PASSWORD_EXPIRY_DAYS", "90")
                )
            except:
                days = 90

        if not self.password_changed_at:
            return True

        expiry_date = self.password_changed_at + timedelta(days=days)
        return timezone.now() > expiry_date

class UserSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="sessions"
    )
    session_key_hash = models.CharField(max_length=64, unique=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField()
    login_time = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    logout_time = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    device_type = models.CharField(max_length=50, blank=True, null=True)
    location = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "user_sessions"
        ordering = ["-login_time"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["session_key_hash"]),
            models.Index(fields=["login_time"]),
        ]

    def __str__(self):
        return f"{self.user.employee_code} - {self.login_time}"

    def save(self, *args, **kwargs):
        if hasattr(self, "_session_key") and not self.session_key_hash:
            self.session_key_hash = hashlib.sha256(
                self._session_key.encode()
            ).hexdigest()
        super().save(*args, **kwargs)

    def is_expired(self, timeout_minutes=None):
        if not self.is_active:
            return True

        if timeout_minutes is None:
            try:
                timeout_minutes = int(
                    SystemConfiguration.get_setting("SESSION_TIMEOUT_MINUTES", "30")
                )
            except:
                timeout_minutes = 30

        if self.last_activity:
            expiry_time = self.last_activity + timedelta(minutes=timeout_minutes)
            return timezone.now() > expiry_time

        return False

    def terminate(self):
        self.is_active = False
        self.logout_time = timezone.now()
        self.save(update_fields=["is_active", "logout_time"])

    def terminate_session(self):
        self.terminate()

    def get_duration(self):
        end_time = self.logout_time or timezone.now()
        return end_time - self.login_time

    @classmethod
    def cleanup_expired_sessions(cls):
        try:
            timeout_minutes = int(
                SystemConfiguration.get_setting("SESSION_TIMEOUT_MINUTES", "30")
            )
        except:
            timeout_minutes = 30

        cutoff_time = timezone.now() - timedelta(minutes=timeout_minutes)
        expired_sessions = cls.objects.filter(
            last_activity__lt=cutoff_time, is_active=True
        )

        count = expired_sessions.count()
        expired_sessions.update(is_active=False, logout_time=timezone.now())

        return count

class SystemConfiguration(models.Model):
    SETTING_TYPES = [
        ("SYSTEM", "System Setting"),
        ("SECURITY", "Security Setting"),
        ("NOTIFICATION", "Notification Setting"),
        ("ATTENDANCE", "Attendance Setting"),
        ("PAYROLL", "Payroll Setting"),
        ("LEAVE", "Leave Setting"),
        ("DEVICE", "Device Setting"),
        ("CALCULATION", "Calculation Setting"),
        ("VALIDATION", "Validation Setting"),
        ("INTEGRATION", "Integration Setting"),
    ]

    id = models.AutoField(primary_key=True)
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    setting_type = models.CharField(max_length=20, choices=SETTING_TYPES, default="SYSTEM")
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_encrypted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_configurations",
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "system_configurations"
        ordering = ["key"]
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["setting_type"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.key}: {self.value[:50]}"

    def save(self, *args, **kwargs):
        self.key = self.key.upper()
        super().save(*args, **kwargs)

    @classmethod
    def get_setting(cls, key, default=None):
        try:
            setting = cls.objects.get(key=key.upper(), is_active=True)
            return setting.value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set_setting(cls, key, value, setting_type="SYSTEM", description=None, user=None):
        key = key.upper()
        setting, created = cls.objects.update_or_create(
            key=key,
            defaults={
                "value": str(value),
                "setting_type": setting_type,
                "description": description or f"System setting for {key}",
                "updated_by": user,
                "is_active": True,
            },
        )
        return setting

    @classmethod
    def get_int_setting(cls, key, default=0):
        try:
            value = cls.get_setting(key, str(default))
            return int(value)
        except (ValueError, TypeError):
            return default

    @classmethod
    def get_float_setting(cls, key, default=0.0):
        try:
            value = cls.get_setting(key, str(default))
            return float(value)
        except (ValueError, TypeError):
            return default

    @classmethod
    def get_bool_setting(cls, key, default=False):
        value = cls.get_setting(key, str(default).lower())
        return value.lower() in ["true", "1", "yes", "on", "enabled"]

    @classmethod
    def get_role_reporting_time(cls, role_name):
        role_key = f"{role_name.upper()}_REPORTING_TIME"
        return cls.get_setting(role_key, "08:00:00")

    @classmethod
    def get_role_grace_period(cls, role_name):
        if role_name.upper() == "OTHER_STAFF":
            return cls.get_int_setting("OTHER_STAFF_GRACE_PERIOD_MINUTES", 15)
        return 0

    @classmethod
    def initialize_default_settings(cls):
        default_settings = {
            "WORK_START_TIME": ("08:00:00", "ATTENDANCE", "Standard work start time"),
            "WORK_END_TIME": ("19:00:00", "ATTENDANCE", "Standard work end time"),
            "NET_WORKING_HOURS": ("9.75", "ATTENDANCE", "Net working hours per day"),
            "TOTAL_WORK_DURATION": ("11.00", "ATTENDANCE", "Total work duration including breaks"),
            "LUNCH_BREAK_DURATION": ("75", "ATTENDANCE", "Lunch break duration in minutes"),
            "MANAGER_REPORTING_TIME": ("08:00:00", "ATTENDANCE", "Manager reporting time"),
            "CASHIER_REPORTING_TIME": ("08:00:00", "ATTENDANCE", "Cashier reporting time"),
            "SALESMAN_REPORTING_TIME": ("08:00:00", "ATTENDANCE", "Salesman reporting time"),
            "CLEANER_REPORTING_TIME": ("08:00:00", "ATTENDANCE", "Cleaner reporting time"),
            "DRIVER_REPORTING_TIME": ("08:00:00", "ATTENDANCE", "Driver reporting time"),
            "ASSISTANT_REPORTING_TIME": ("08:00:00", "ATTENDANCE", "Assistant reporting time"),
            "STOREKEEPER_REPORTING_TIME": ("08:00:00", "ATTENDANCE", "Storekeeper reporting time"),
            "OTHER_STAFF_REPORTING_TIME": ("08:00:00", "ATTENDANCE", "Other staff reporting time"),
            "OTHER_STAFF_GRACE_PERIOD_MINUTES": ("15", "ATTENDANCE", "Other staff grace period"),
            "OFFICE_WORKER_REPORTING_TIME": ("08:30:00", "ATTENDANCE", "Office worker reporting time"),
            "LATE_THRESHOLD_MINUTES": ("1", "ATTENDANCE", "Late threshold in minutes"),
            "EARLY_DEPARTURE_THRESHOLD_MINUTES": ("1", "ATTENDANCE", "Early departure threshold"),
            "OVERTIME_THRESHOLD_TIME": ("19:00:00", "ATTENDANCE", "Overtime threshold time"),
            "HALF_DAY_THRESHOLD_MINUTES": ("35", "ATTENDANCE", "Half day threshold minutes"),
            "MIN_LUNCH_DURATION_MINUTES": ("30", "ATTENDANCE", "Minimum lunch duration"),
            "MAX_LUNCH_DURATION_MINUTES": ("75", "ATTENDANCE", "Maximum lunch duration"),
            "LUNCH_VIOLATION_LIMIT_PER_MONTH": ("3", "ATTENDANCE", "Lunch violation limit per month"),
            "LUNCH_VIOLATION_PENALTY_DAYS": ("1", "ATTENDANCE", "Lunch violation penalty days"),
            "MINIMUM_WORK_HOURS_FULL_DAY": ("9.75", "ATTENDANCE", "Minimum work hours for full day"),
            "MINIMUM_WORK_HOURS_HALF_DAY": ("4.875", "ATTENDANCE", "Minimum work hours for half day"),
            "ANNUAL_LEAVE_DAYS": ("18", "LEAVE", "Annual leave days per year"),
            "MEDICAL_LEAVE_DAYS": ("7", "LEAVE", "Medical leave days per year"),
            "TOTAL_LEAVE_DAYS": ("25", "LEAVE", "Total leave days per year"),
            "UNPAID_LEAVE_THRESHOLD_TIME": ("08:30:00", "LEAVE", "Unpaid leave threshold time"),
            "LEAVE_APPROVAL_REQUIRED": ("true", "LEAVE", "Leave approval required"),
            "MEDICAL_CERTIFICATE_REQUIRED_DAYS": ("3", "LEAVE", "Medical certificate required days"),
            "MIN_LEAVE_NOTICE_DAYS": ("1", "LEAVE", "Minimum leave notice days"),
        }

        created_count = 0
        for key, (value, setting_type, description) in default_settings.items():
            setting, created = cls.objects.get_or_create(
                key=key,
                defaults={
                    "value": value,
                    "setting_type": setting_type,
                    "description": description,
                    "is_active": True,
                },
            )
            if created:
                created_count += 1

        device_settings = {
            "DEVICE_SYNC_INTERVAL_MINUTES": ("5", "DEVICE", "Device synchronization interval"),
            "DEVICE_CONNECTION_TIMEOUT_SECONDS": ("30", "DEVICE", "Device connection timeout"),
            "MAX_DEVICE_RETRY_ATTEMPTS": ("3", "DEVICE", "Maximum device retry attempts"),
            "ATTENDANCE_LOG_RETENTION_DAYS": ("90", "DEVICE", "Attendance log retention days"),
            "MONTHLY_SUMMARY_RETENTION_MONTHS": ("24", "DEVICE", "Monthly summary retention months"),
            "REPORT_RETENTION_DAYS": ("365", "DEVICE", "Report retention days"),
            "AUTO_PROCESS_DEVICE_LOGS": ("true", "DEVICE", "Auto process device logs"),
            "AUTO_GENERATE_DAILY_RECORDS": ("true", "DEVICE", "Auto generate daily records"),
            "AUTO_GENERATE_MONTHLY_SUMMARIES": ("true", "DEVICE", "Auto generate monthly summaries"),
            "LATE_ARRIVAL_NOTIFICATION": ("true", "NOTIFICATION", "Late arrival notification"),
            "EARLY_DEPARTURE_NOTIFICATION": ("true", "NOTIFICATION", "Early departure notification"),
            "MISSING_CHECKOUT_NOTIFICATION": ("true", "NOTIFICATION", "Missing checkout notification"),
            "OVERTIME_NOTIFICATION": ("true", "NOTIFICATION", "Overtime notification"),
            "LATE_NOTIFICATION_THRESHOLD_MINUTES": ("15", "NOTIFICATION", "Late notification threshold"),
            "MISSING_CHECKOUT_NOTIFICATION_TIME": ("20:00:00", "NOTIFICATION", "Missing checkout notification time"),
            "OVERTIME_NOTIFICATION_THRESHOLD_MINUTES": ("30", "NOTIFICATION", "Overtime notification threshold"),
            "MAX_REPORT_RANGE_DAYS": ("365", "SYSTEM", "Maximum report range days"),
            "REPORT_GENERATION_TIMEOUT_MINUTES": ("30", "SYSTEM", "Report generation timeout"),
            "AUTO_CLEANUP_OLD_REPORTS": ("true", "SYSTEM", "Auto cleanup old reports"),
            "PUNCTUALITY_WEIGHT": ("0.4", "CALCULATION", "Punctuality weight in performance"),
            "ATTENDANCE_WEIGHT": ("0.6", "CALCULATION", "Attendance weight in performance"),
            "EXCELLENT_ATTENDANCE_THRESHOLD": ("95.0", "CALCULATION", "Excellent attendance threshold"),
            "GOOD_ATTENDANCE_THRESHOLD": ("85.0", "CALCULATION", "Good attendance threshold"),
            "MAX_DAILY_CHECKINS": ("6", "VALIDATION", "Maximum daily check-ins"),
            "MIN_BREAK_DURATION_MINUTES": ("5", "VALIDATION", "Minimum break duration"),
            "MAX_BREAK_DURATION_MINUTES": ("120", "VALIDATION", "Maximum break duration"),
            "ALLOW_FUTURE_ATTENDANCE": ("false", "VALIDATION", "Allow future attendance"),
            "ALLOW_WEEKEND_OVERTIME": ("true", "VALIDATION", "Allow weekend overtime"),
            "ATTENDANCE_CORRECTION_APPROVAL_REQUIRED": ("true", "VALIDATION", "Attendance correction approval required"),
            "MAX_CORRECTION_DAYS_BACK": ("7", "VALIDATION", "Maximum correction days back"),
            "ALLOW_SELF_CORRECTION": ("false", "VALIDATION", "Allow self correction"),
            "MAX_LOGIN_ATTEMPTS": ("5", "SECURITY", "Maximum login attempts"),
            "ACCOUNT_LOCKOUT_DURATION": ("30", "SECURITY", "Account lockout duration in minutes"),
            "SESSION_TIMEOUT_MINUTES": ("30", "SECURITY", "Session timeout in minutes"),
            "PASSWORD_EXPIRY_DAYS": ("90", "SECURITY", "Password expiry days"),
            "REQUIRE_PASSWORD_CHANGE": ("true", "SECURITY", "Require password change"),
            "MIN_PASSWORD_LENGTH": ("8", "SECURITY", "Minimum password length"),
            "REQUIRE_STRONG_PASSWORD": ("true", "SECURITY", "Require strong password"),
        }

        for key, (value, setting_type, description) in device_settings.items():
            setting, created = cls.objects.get_or_create(
                key=key,
                defaults={
                    "value": value,
                    "setting_type": setting_type,
                    "description": description,
                    "is_active": True,
                },
            )
            if created:
                created_count += 1

        return created_count

class PasswordResetToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="password_reset_tokens"
    )
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    is_used = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField()

    class Meta:
        db_table = "password_reset_tokens"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["user", "is_used"]),
            models.Index(fields=["expires_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = self.generate_token()
        if not self.expires_at:
            try:
                expiry_hours = int(
                    SystemConfiguration.get_setting("PASSWORD_RESET_EXPIRY_HOURS", "24")
                )
            except:
                expiry_hours = 24
            self.expires_at = timezone.now() + timedelta(hours=expiry_hours)
        super().save(*args, **kwargs)

    def generate_token(self):
        return secrets.token_urlsafe(32)

    def is_expired(self):
        return timezone.now() > self.expires_at

    def is_valid(self):
        return not self.is_used and not self.is_expired()

    def use_token(self):
        self.is_used = True
        self.used_at = timezone.now()
        self.save(update_fields=["is_used", "used_at"])

class AuditLog(models.Model):
    ACTION_TYPES = [
        ("CREATE", "Create"),
        ("UPDATE", "Update"),
        ("DELETE", "Delete"),
        ("LOGIN", "Login"),
        ("LOGOUT", "Logout"),
        ("PASSWORD_CHANGE", "Password Change"),
        ("PERMISSION_CHANGE", "Permission Change"),
        ("ATTENDANCE_CHANGE", "Attendance Change"),
        ("LEAVE_CHANGE", "Leave Change"),
        ("PAYROLL_CHANGE", "Payroll Change"),
        ("SYSTEM_CHANGE", "System Change"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=50, choices=ACTION_TYPES)
    model_name = models.CharField(max_length=100, blank=True, null=True)
    object_id = models.CharField(max_length=100, blank=True, null=True)
    object_repr = models.CharField(max_length=200, blank=True, null=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    session_key = models.CharField(max_length=40, blank=True, null=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["user", "timestamp"]),
            models.Index(fields=["action", "timestamp"]),
            models.Index(fields=["model_name", "object_id"]),
            models.Index(fields=["timestamp"]),
        ]

    def __str__(self):
        user_info = self.user.employee_code if self.user else "System"
        return f"{user_info} - {self.action} - {self.timestamp}"

    @classmethod
    def log_action(
        cls,
        user,
        action,
        model_name=None,
        object_id=None,
        object_repr=None,
        changes=None,
        ip_address=None,
        user_agent=None,
        session_key=None,
    ):
        return cls.objects.create(
            user=user,
            action=action,
            model_name=model_name,
            object_id=str(object_id) if object_id else None,
            object_repr=object_repr,
            changes=changes or {},
            ip_address=ip_address,
            user_agent=user_agent,
            session_key=session_key,
        )

    @classmethod
    def cleanup_old_logs(cls, days=365):
        cutoff_date = timezone.now() - timedelta(days=days)
        old_logs = cls.objects.filter(timestamp__lt=cutoff_date)
        count = old_logs.count()
        old_logs.delete()
        return count


class APIKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="api_keys"
    )
    permissions = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    usage_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "api_keys"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["key_hash"]),
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.user.employee_code}"

    def save(self, *args, **kwargs):
        if not self.key_hash and hasattr(self, "_raw_key"):
            self.key_hash = hashlib.sha256(self._raw_key.encode()).hexdigest()
        super().save(*args, **kwargs)

    @classmethod
    def generate_key(cls, user, name, permissions=None, expires_days=None):
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        expires_at = None
        if expires_days:
            expires_at = timezone.now() + timedelta(days=expires_days)

        api_key = cls.objects.create(
            name=name,
            key_hash=key_hash,
            user=user,
            permissions=permissions or [],
            expires_at=expires_at,
        )

        api_key._raw_key = raw_key
        return api_key, raw_key

    def is_expired(self):
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False

    def record_usage(self):
        self.last_used_at = timezone.now()
        self.usage_count += 1
        self.save(update_fields=["last_used_at", "usage_count"])

    def has_permission(self, permission):
        return permission in self.permissions or "all" in self.permissions


def initialize_default_roles():
    default_roles = [
        {
            "name": "SUPER_ADMIN",
            "display_name": "Super Admin",
            "description": "Full system access and administration",
            "level": 10,
            "can_manage_employees": True,
            "can_view_all_data": True,
            "can_approve_leave": True,
            "can_manage_payroll": True,
        },
        {
            "name": "MANAGER",
            "display_name": "Manager",
            "description": "Department management and employee oversight",
            "level": 8,
            "can_manage_employees": True,
            "can_view_all_data": False,
            "can_approve_leave": True,
            "can_manage_payroll": False,
        },
        {
            "name": "CASHIER",
            "display_name": "Cashier",
            "description": "Cash handling and transaction processing",
            "level": 5,
            "can_manage_employees": False,
            "can_view_all_data": False,
            "can_approve_leave": False,
            "can_manage_payroll": False,
        },
        {
            "name": "SALESMAN",
            "display_name": "Salesman",
            "description": "Sales and customer service",
            "level": 4,
            "can_manage_employees": False,
            "can_view_all_data": False,
            "can_approve_leave": False,
            "can_manage_payroll": False,
        },
        {
            "name": "OTHER_STAFF",
            "display_name": "Other Staff",
            "description": "General staff members",
            "level": 3,
            "can_manage_employees": False,
            "can_view_all_data": False,
            "can_approve_leave": False,
            "can_manage_payroll": False,
        },
        {
            "name": "CLEANER",
            "display_name": "Cleaner",
            "description": "Cleaning and maintenance staff",
            "level": 2,
            "can_manage_employees": False,
            "can_view_all_data": False,
            "can_approve_leave": False,
            "can_manage_payroll": False,
        },
        {
            "name": "DRIVER",
            "display_name": "Driver",
            "description": "Transportation and delivery staff",
            "level": 3,
            "can_manage_employees": False,
            "can_view_all_data": False,
            "can_approve_leave": False,
            "can_manage_payroll": False,
        },
        {
            "name": "ASSISTANT",
            "display_name": "Assistant",
            "description": "Administrative and support staff",
            "level": 4,
            "can_manage_employees": False,
            "can_view_all_data": False,
            "can_approve_leave": False,
            "can_manage_payroll": False,
        },
        {
            "name": "STOREKEEPER",
            "display_name": "Storekeeper",
            "description": "Inventory and warehouse management",
            "level": 5,
            "can_manage_employees": False,
            "can_view_all_data": False,
            "can_approve_leave": False,
            "can_manage_payroll": False,
        },
        {
            "name": "OFFICE_WORKER",
            "display_name": "Office Worker",
            "description": "General office and administrative work",
            "level": 4,
            "can_manage_employees": False,
            "can_view_all_data": False,
            "can_approve_leave": False,
            "can_manage_payroll": False,
        },
    ]

    created_count = 0
    for role_data in default_roles:
        role, created = Role.objects.get_or_create(
            name=role_data["name"], defaults=role_data
        )
        if created:
            created_count += 1

    return created_count


def initialize_system():
    roles_created = initialize_default_roles()
    settings_created = SystemConfiguration.initialize_default_settings()

    return {
        "roles_created": roles_created,
        "settings_created": settings_created,
        "total_initialized": roles_created + settings_created,
    }
