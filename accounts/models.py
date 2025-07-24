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
        ("SUPER_ADMIN", "Super Administrator"),
        ("HR_ADMIN", "HR Administrator"),
        ("HR_MANAGER", "HR Manager"),
        ("DEPARTMENT_MANAGER", "Department Manager"),
        ("PAYROLL_MANAGER", "Payroll Manager"),
        ("EMPLOYEE", "Employee"),
        ("ACCOUNTANT", "Accountant"),
        ("AUDITOR", "Auditor"),
    ]

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=50, choices=ROLE_TYPES, unique=True)
    display_name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
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
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other'),
    ]

    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('INACTIVE', 'Inactive'),
        ('SUSPENDED', 'Suspended'),
        ('TERMINATED', 'Terminated'),
    ]

    username = models.CharField(max_length=20, unique=True, null=True, blank=True)
    employee_code = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        validators=[
            RegexValidator(
                r'^[A-Z0-9]{3,20}$',
                'Employee code must be 3-20 characters, alphanumeric uppercase only',
            )
        ],
    )

    first_name = models.CharField(max_length=50, blank=True)
    last_name = models.CharField(max_length=50, blank=True)
    middle_name = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(unique=True, null=True, blank=True, validators=[EmailValidator()])
    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        validators=[
            RegexValidator(r'^\+?[1-9]\d{1,14}$', 'Enter a valid phone number')
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
    country = models.CharField(max_length=100, default='Sri Lanka')

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
        related_name='employees',
    )
    role = models.ForeignKey(
        Role, on_delete=models.SET_NULL, null=True, blank=True, related_name='users'
    )
    job_title = models.CharField(max_length=100, blank=True, null=True)
    hire_date = models.DateField(null=True, blank=True)
    termination_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')

    manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subordinates',
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
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_users',
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()
    active = ActiveUserManager()

    USERNAME_FIELD = 'employee_code'
    REQUIRED_FIELDS = ['email', 'first_name', 'last_name']

    class Meta:
        db_table = 'users'
        ordering = ['employee_code']
        indexes = [
            models.Index(fields=['employee_code']),
            models.Index(fields=['email']),
            models.Index(fields=['status']),
            models.Index(fields=['department']),
            models.Index(fields=['is_active']),
            models.Index(fields=['hire_date']),
        ]

    def __str__(self):
        if self.employee_code:
            return f"{self.employee_code} - {self.get_full_name()}"
        return self.username or f"User {self.id}"

    def save(self, *args, **kwargs):
        if (not self.pk and 
            not self.employee_code and 
            not self.first_name and 
            not self.last_name and 
            not self.email and
            getattr(self, 'username', None) in [None, '', 'AnonymousUser']):
            super(AbstractUser, self).save(*args, **kwargs)
            return
        
        if getattr(self, '_skip_validation', False):
            super(AbstractUser, self).save(*args, **kwargs)
            return
        
        if self.username == 'AnonymousUser':
            super(AbstractUser, self).save(*args, **kwargs)
            return
        
        self.full_clean()
        
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
        if (not self.employee_code and 
            not self.first_name and 
            not self.last_name and 
            not self.email):
            return
        
        if self.username == 'AnonymousUser':
            return
            
        if self.hire_date and self.hire_date > timezone.now().date():
            raise ValidationError('Hire date cannot be in the future')

        if (
            self.hire_date
            and self.termination_date
            and self.termination_date <= self.hire_date
        ):
            raise ValidationError('Termination date must be after hire date')

        if self.manager == self:
            raise ValidationError('User cannot be their own manager')

    def soft_delete(self):
        self.is_active = False
        self.status = 'TERMINATED'
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_active', 'status', 'deleted_at'])

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
        return self.role and self.role.name in ['HR_ADMIN', 'SUPER_ADMIN']

    @property
    def is_department_manager(self):
        return self.role and self.role.name == 'DEPARTMENT_MANAGER'

    def is_account_locked(self):
        if self.account_locked_until:
            return timezone.now() < self.account_locked_until
        return False

    def lock_account(self, duration_minutes=None):
        if duration_minutes is None:
            try:
                duration_minutes = int(SystemConfiguration.get_setting('ACCOUNT_LOCKOUT_DURATION', '30'))
            except:
                duration_minutes = 30
        self.account_locked_until = timezone.now() + timedelta(minutes=duration_minutes)
        self.save(update_fields=['account_locked_until'])

    def unlock_account(self):
        self.account_locked_until = None
        self.failed_login_attempts = 0
        self.save(update_fields=['account_locked_until', 'failed_login_attempts'])

    def increment_failed_login(self):
        try:
            max_attempts = int(SystemConfiguration.get_setting('MAX_LOGIN_ATTEMPTS', '5'))
        except:
            max_attempts = 5
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            self.lock_account()
        self.save(update_fields=['failed_login_attempts'])

    def reset_failed_login(self):
        if self.failed_login_attempts > 0:
            self.failed_login_attempts = 0
            self.save(update_fields=['failed_login_attempts'])

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
                manager_id=manager_id, 
                is_active=True
            ).values_list('id', flat=True)
            
            for sub_id in direct_subs:
                subordinate_ids.append(sub_id)
                collect_subordinates(sub_id)
        
        collect_subordinates(self.id)
        return CustomUser.objects.filter(id__in=subordinate_ids)

    def can_manage_user(self, target_user):
        if self.is_superuser:
            return True

        if self.role and self.role.name == 'HR_ADMIN':
            return True

        if self.role and self.role.name == 'DEPARTMENT_MANAGER':
            if self.department and target_user.department == self.department:
                return True

        if target_user.manager == self:
            return True

        return False

    def is_password_expired(self, days=None):
        if days is None:
            try:
                days = int(SystemConfiguration.get_setting('PASSWORD_EXPIRY_DAYS', '90'))
            except:
                days = 90
            
        if not self.password_changed_at:
            return True

        expiry_date = self.password_changed_at + timedelta(days=days)
        return timezone.now() > expiry_date

class UserSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name='sessions'
    )
    session_key_hash = models.CharField(max_length=64, unique=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField()
    login_time = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    logout_time = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'user_sessions'
        ordering = ['-login_time']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['session_key_hash']),
            models.Index(fields=['login_time']),
        ]

    def __str__(self):
        return f"{self.user.employee_code} - {self.login_time}"

    def save(self, *args, **kwargs):
        if hasattr(self, '_session_key') and not self.session_key_hash:
            self.session_key_hash = hashlib.sha256(self._session_key.encode()).hexdigest()
        super().save(*args, **kwargs)

    def is_expired(self, timeout_minutes=None):
        if not self.is_active:
            return True

        if timeout_minutes is None:
            try:
                timeout_minutes = int(SystemConfiguration.get_setting('SESSION_TIMEOUT', '30'))
            except:
                timeout_minutes = 30

        expiry_time = self.last_activity + timedelta(minutes=timeout_minutes)
        return timezone.now() > expiry_time

    def terminate_session(self):
        self.is_active = False
        self.logout_time = timezone.now()
        self.save(update_fields=['is_active', 'logout_time'])

class PasswordResetToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name='password_reset_tokens'
    )
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    is_used = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField()

    class Meta:
        db_table = 'password_reset_tokens'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['user', 'is_used']),
            models.Index(fields=['expires_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = self.generate_token()
        if not self.expires_at:
            try:
                expiry_hours = int(SystemConfiguration.get_setting('PASSWORD_RESET_EXPIRY_HOURS', '24'))
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
        self.save(update_fields=['is_used', 'used_at'])

class AuditLog(models.Model):
    ACTION_TYPES = [
        ('LOGIN', 'User Login'),
        ('LOGOUT', 'User Logout'),
        ('PASSWORD_CHANGE', 'Password Change'),
        ('PROFILE_UPDATE', 'Profile Update'),
        ('PERMISSION_CHANGE', 'Permission Change'),
        ('ACCOUNT_LOCK', 'Account Lock'),
        ('ACCOUNT_UNLOCK', 'Account Unlock'),
        ('DATA_EXPORT', 'Data Export'),
        ('SYSTEM_ACCESS', 'System Access'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, related_name='audit_logs'
    )
    action = models.CharField(max_length=50, choices=ACTION_TYPES)
    description = models.TextField()
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    additional_data = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', 'action']),
            models.Index(fields=['timestamp']),
            models.Index(fields=['action']),
        ]

    def __str__(self):
        user_display = self.user.employee_code if self.user else 'Unknown'
        return f"{user_display} - {self.action} - {self.timestamp}"

    @classmethod
    def log_action(
        cls,
        user,
        action,
        description,
        ip_address,
        user_agent=None,
        additional_data=None,
    ):
        return cls.objects.create(
            user=user,
            action=action,
            description=description,
            ip_address=ip_address,
            user_agent=user_agent or '',
            additional_data=additional_data or {},
        )

class SystemConfiguration(models.Model):
    key = models.CharField(max_length=100, unique=True, db_index=True)
    value = models.TextField(blank=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey('CustomUser', on_delete=models.SET_NULL, null=True, blank=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = 'system_configurations'
        ordering = ['key']
        verbose_name = 'System Configuration'
        verbose_name_plural = 'System Configurations'

    def __str__(self):
        return f"{self.key}: {self.value[:50]}{'...' if len(self.value) > 50 else ''}"

    @classmethod
    def get_setting(cls, key, default=None):
        try:
            setting = cls.objects.get(key=key.upper(), is_active=True)
            return setting.value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set_setting(cls, key, value, description=None, user=None):
        key = key.upper()
        setting, created = cls.objects.get_or_create(
            key=key,
            defaults={
                'value': str(value),
                'description': description or f'Configuration setting for {key}',
                'updated_by': user,
                'is_active': True
            }
        )

        if not created:
            setting.value = str(value)
            if description:
                setting.description = description
            if user:
                setting.updated_by = user
            setting.save(update_fields=['value', 'description', 'updated_by', 'updated_at'])

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
        return value.lower() in ['true', '1', 'yes', 'on', 'enabled']

    @classmethod
    def is_enabled(cls, key):
        return cls.get_bool_setting(key, False)

    @classmethod
    def get_list_setting(cls, key, default=None, separator=','):
        if default is None:
            default = []

        value = cls.get_setting(key, '')
        if not value:
            return default

        return [item.strip() for item in value.split(separator) if item.strip()]

    @classmethod
    def get_all_settings(cls, active_only=True):
        queryset = cls.objects.all()
        if active_only:
            queryset = queryset.filter(is_active=True)

        return dict(queryset.values_list('key', 'value'))

    @classmethod
    def get_settings_by_prefix(cls, prefix, active_only=True):
        queryset = cls.objects.filter(key__startswith=prefix.upper())
        if active_only:
            queryset = queryset.filter(is_active=True)

        return dict(queryset.values_list('key', 'value'))

    @classmethod
    def delete_setting(cls, key):
        try:
            setting = cls.objects.get(key=key.upper())
            setting.delete()
            return True
        except cls.DoesNotExist:
            return False

    @classmethod
    def deactivate_setting(cls, key, user=None):
        try:
            setting = cls.objects.get(key=key.upper())
            setting.is_active = False
            if user:
                setting.updated_by = user
            setting.save(update_fields=['is_active', 'updated_by', 'updated_at'])
            return True
        except cls.DoesNotExist:
            return False

    @classmethod
    def activate_setting(cls, key, user=None):
        try:
            setting = cls.objects.get(key=key.upper())
            setting.is_active = True
            if user:
                setting.updated_by = user
            setting.save(update_fields=['is_active', 'updated_by', 'updated_at'])
            return True
        except cls.DoesNotExist:
            return False

    @classmethod
    def get_company_info(cls):
        return {
            'name': cls.get_setting('COMPANY_NAME', 'HR Payroll System'),
            'address': cls.get_setting('COMPANY_ADDRESS', ''),
            'phone': cls.get_setting('COMPANY_PHONE', ''),
            'email': cls.get_setting('COMPANY_EMAIL', ''),
        }

    @classmethod
    def get_security_settings(cls):
        return {
            'password_expiry_days': cls.get_int_setting('PASSWORD_EXPIRY_DAYS', 90),
            'password_expiry_warning_days': cls.get_int_setting('PASSWORD_EXPIRY_WARNING_DAYS', 7),
            'max_login_attempts': cls.get_int_setting('MAX_LOGIN_ATTEMPTS', 5),
            'account_lockout_duration': cls.get_int_setting('ACCOUNT_LOCKOUT_DURATION', 30),
            'session_timeout': cls.get_int_setting('SESSION_TIMEOUT', 30),
            'max_concurrent_sessions': cls.get_int_setting('MAX_CONCURRENT_SESSIONS', 3),
            'security_alert_threshold': cls.get_int_setting('SECURITY_ALERT_THRESHOLD', 10),
        }

    @classmethod
    def get_system_settings(cls):
        return {
            'audit_log_retention_days': cls.get_int_setting('AUDIT_LOG_RETENTION_DAYS', 365),
            'max_upload_size_mb': cls.get_int_setting('MAX_UPLOAD_SIZE_MB', 10),
            'allowed_file_types': cls.get_list_setting('ALLOWED_FILE_TYPES', ['pdf', 'doc', 'docx', 'jpg', 'jpeg', 'png', 'xlsx', 'xls']),
            'email_notifications_enabled': cls.get_bool_setting('EMAIL_NOTIFICATIONS_ENABLED', True),
            'system_maintenance_mode': cls.get_bool_setting('SYSTEM_MAINTENANCE_MODE', False),
        }

    @classmethod
    def get_hr_settings(cls):
        return {
            'working_hours_per_day': cls.get_float_setting('WORKING_HOURS_PER_DAY', 8.0),
            'working_days_per_week': cls.get_int_setting('WORKING_DAYS_PER_WEEK', 5),
            'overtime_rate': cls.get_float_setting('OVERTIME_RATE', 1.5),
            'late_penalty_rate': cls.get_float_setting('LATE_PENALTY_RATE', 0.0),
            'min_employee_age': cls.get_int_setting('MIN_EMPLOYEE_AGE', 18),
            'max_employee_age': cls.get_int_setting('MAX_EMPLOYEE_AGE', 65),
        }

    @classmethod
    def reset_to_defaults(cls, user=None):
        default_settings = [
            ('COMPANY_NAME', 'HR Payroll System', 'Company name displayed in the system'),
            ('COMPANY_ADDRESS', '', 'Company physical address'),
            ('COMPANY_PHONE', '', 'Company contact phone number'),
            ('COMPANY_EMAIL', '', 'Company contact email address'),
            ('WORKING_HOURS_PER_DAY', '8.00', 'Standard working hours per day'),
            ('WORKING_DAYS_PER_WEEK', '5', 'Standard working days per week'),
            ('OVERTIME_RATE', '1.50', 'Overtime pay rate multiplier'),
            ('LATE_PENALTY_RATE', '0.00', 'Late arrival penalty rate per hour'),
            ('PASSWORD_EXPIRY_DAYS', '90', 'Password expiration period in days'),
            ('PASSWORD_EXPIRY_WARNING_DAYS', '7', 'Days before password expiry to show warning'),
            ('MAX_LOGIN_ATTEMPTS', '5', 'Maximum failed login attempts before account lock'),
            ('ACCOUNT_LOCKOUT_DURATION', '30', 'Account lockout duration in minutes'),
            ('SESSION_TIMEOUT', '30', 'Session timeout in minutes'),
            ('MAX_CONCURRENT_SESSIONS', '3', 'Maximum concurrent sessions per user'),
            ('AUDIT_LOG_RETENTION_DAYS', '365', 'Audit log retention period in days'),
            ('SECURITY_ALERT_THRESHOLD', '10', 'Security events threshold for alerts'),
            ('MIN_EMPLOYEE_AGE', '18', 'Minimum employee age requirement'),
            ('MAX_EMPLOYEE_AGE', '65', 'Maximum employee age limit'),
            ('MAX_UPLOAD_SIZE_MB', '10', 'Maximum file upload size in MB'),
            ('ALLOWED_FILE_TYPES', 'pdf,doc,docx,jpg,jpeg,png,xlsx,xls', 'Allowed file upload types'),
            ('EMAIL_NOTIFICATIONS_ENABLED', 'true', 'Enable email notifications'),
            ('SYSTEM_MAINTENANCE_MODE', 'false', 'System maintenance mode flag'),
        ]

        for key, value, description in default_settings:
            cls.set_setting(key, value, description, user)

    def clean(self):
        if self.key:
            self.key = self.key.upper()

        if self.key in ['MAX_LOGIN_ATTEMPTS', 'SESSION_TIMEOUT', 'MAX_CONCURRENT_SESSIONS']:
            try:
                int_value = int(self.value)
                if int_value < 1:
                    raise ValidationError(f'{self.key} must be a positive integer')
            except ValueError:
                raise ValidationError(f'{self.key} must be a valid integer')

        if self.key in ['OVERTIME_RATE', 'LATE_PENALTY_RATE']:
            try:
                float_value = float(self.value)
                if float_value < 0:
                    raise ValidationError(f'{self.key} must be a non-negative number')
            except ValueError:
                raise ValidationError(f'{self.key} must be a valid number')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
