from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from .models import Department, Role, UserSession, PasswordResetToken, AuditLog, SystemConfiguration
from .utils import validate_password_strength, SystemUtilities
from django.utils import timezone
from datetime import datetime, timedelta
import re

User = get_user_model()


class BaseValidationMixin:
    @staticmethod
    def validate_password_field(password):
        is_valid, errors = validate_password_strength(password)
        if not is_valid:
            raise serializers.ValidationError(errors)
        return password

    @staticmethod
    def validate_phone_field(phone):
        if phone:
            phone_regex = re.compile(r'^\+?[1-9]\d{1,14}$')
            if not phone_regex.match(phone):
                raise serializers.ValidationError("Enter a valid phone number.")
        return phone

    @staticmethod
    def validate_age_field(date_of_birth):
        if date_of_birth:
            today = timezone.now().date()
            age = today.year - date_of_birth.year - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
            min_age = int(SystemConfiguration.get_setting('MIN_EMPLOYEE_AGE', '18'))
            max_age = int(SystemConfiguration.get_setting('MAX_EMPLOYEE_AGE', '65'))
            
            if age < min_age:
                raise serializers.ValidationError(f"Employee must be at least {min_age} years old.")
            if age > max_age:
                raise serializers.ValidationError("Please verify the date of birth.")
        return date_of_birth

    @staticmethod
    def validate_hire_date_field(hire_date):
        if hire_date and hire_date > timezone.now().date():
            raise serializers.ValidationError("Hire date cannot be in the future.")
        return hire_date


class DepartmentSerializer(serializers.ModelSerializer):
    manager_name = serializers.CharField(source='manager.get_full_name', read_only=True)
    parent_department_name = serializers.CharField(source='parent_department.name', read_only=True)
    employee_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Department
        fields = [
            'id', 'name', 'code', 'description', 'manager', 'manager_name',
            'parent_department', 'parent_department_name', 'is_active',
            'created_at', 'updated_at', 'employee_count'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_employee_count(self, obj):
        return getattr(obj, '_employee_count', obj.employees.filter(is_active=True).count())
    
    def validate_code(self, value):
        if value:
            value = value.upper()
            if self.instance and self.instance.pk:
                if Department.objects.filter(code=value).exclude(pk=self.instance.pk).exists():
                    raise serializers.ValidationError("Department code already exists.")
            else:
                if Department.objects.filter(code=value).exists():
                    raise serializers.ValidationError("Department code already exists.")
        return value


class RoleSerializer(serializers.ModelSerializer):
    permissions_list = serializers.SerializerMethodField()
    user_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Role
        fields = [
            'id', 'name', 'display_name', 'description', 'permissions',
            'permissions_list', 'is_active', 'created_at', 'updated_at', 'user_count'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_permissions_list(self, obj):
        return list(obj.permissions.values_list('codename', flat=True))
    
    def get_user_count(self, obj):
        return getattr(obj, '_user_count', obj.users.filter(is_active=True).count())


class UserBasicSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    display_name = serializers.CharField(source='get_display_name', read_only=True)
    
    class Meta:
        model = User
        fields = [
            'id', 'employee_code', 'first_name', 'last_name', 'middle_name',
            'full_name', 'display_name', 'email', 'job_title', 'status'
        ]


class UserDetailSerializer(BaseValidationMixin, serializers.ModelSerializer):
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    display_name = serializers.CharField(source='get_display_name', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    role_name = serializers.CharField(source='role.display_name', read_only=True)
    manager_name = serializers.CharField(source='manager.get_full_name', read_only=True)
    age = serializers.SerializerMethodField()
    is_account_locked = serializers.BooleanField(source='is_account_locked', read_only=True)
    is_password_expired = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id', 'employee_code', 'first_name', 'last_name', 'middle_name',
            'full_name', 'display_name', 'email', 'phone_number', 'date_of_birth',
            'age', 'gender', 'address_line1', 'address_line2', 'city', 'state',
            'postal_code', 'country', 'emergency_contact_name', 'emergency_contact_phone',
            'emergency_contact_relationship', 'department', 'department_name',
            'role', 'role_name', 'job_title', 'hire_date', 'termination_date',
            'status', 'manager', 'manager_name', 'is_verified', 'last_login',
            'last_login_ip', 'failed_login_attempts', 'is_account_locked',
            'must_change_password', 'is_password_expired', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'employee_code', 'last_login', 'last_login_ip', 'failed_login_attempts',
            'is_account_locked', 'must_change_password', 'created_at', 'updated_at'
        ]
    
    def get_age(self, obj):
        if obj.date_of_birth:
            today = timezone.now().date()
            return today.year - obj.date_of_birth.year - ((today.month, today.day) < (obj.date_of_birth.month, obj.date_of_birth.day))
        return None
    
    def get_is_password_expired(self, obj):
        return obj.is_password_expired()
    
    def validate_email(self, value):
        if value:
            if self.instance and self.instance.pk:
                if User.objects.filter(email=value).exclude(pk=self.instance.pk).exists():
                    raise serializers.ValidationError("Email address already exists.")
            else:
                if User.objects.filter(email=value).exists():
                    raise serializers.ValidationError("Email address already exists.")
        return value
    
    def validate_phone_number(self, value):
        return self.validate_phone_field(value)
    
    def validate_date_of_birth(self, value):
        return self.validate_age_field(value)
    
    def validate_hire_date(self, value):
        return self.validate_hire_date_field(value)


class UserCreateSerializer(BaseValidationMixin, serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = [
            'employee_code', 'first_name', 'last_name', 'middle_name',
            'email', 'phone_number', 'date_of_birth', 'gender',
            'address_line1', 'address_line2', 'city', 'state',
            'postal_code', 'country', 'emergency_contact_name',
            'emergency_contact_phone', 'emergency_contact_relationship',
            'department', 'role', 'job_title', 'hire_date', 'manager',
            'password', 'password_confirm'
        ]
    
    def validate_employee_code(self, value):
        if value:
            value = value.upper()
            if not re.match(r'^[A-Z0-9]{3,20}$', value):
                raise serializers.ValidationError(
                    "Employee code must be 3-20 characters, alphanumeric uppercase only."
                )
            if User.objects.filter(employee_code=value).exists():
                raise serializers.ValidationError("Employee code already exists.")
        return value
    
    def validate_email(self, value):
        if value and User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email address already exists.")
        return value
    
    def validate_phone_number(self, value):
        return self.validate_phone_field(value)
    
    def validate_date_of_birth(self, value):
        return self.validate_age_field(value)
    
    def validate_hire_date(self, value):
        return self.validate_hire_date_field(value)
    
    def validate_password(self, value):
        return self.validate_password_field(value)
    
    def validate(self, attrs):
        password = attrs.get('password')
        password_confirm = attrs.get('password_confirm')
        
        if password != password_confirm:
            raise serializers.ValidationError("Passwords do not match.")
        
        manager = attrs.get('manager')
        employee_code = attrs.get('employee_code')
        
        if manager and manager.employee_code == employee_code:
            raise serializers.ValidationError("Employee cannot be their own manager.")
        
        return attrs
    
    def create(self, validated_data):
        validated_data.pop('password_confirm')
        password = validated_data.pop('password')
        
        user = User.objects.create_user(
            username=validated_data['employee_code'],
            password=password,
            **validated_data
        )
        
        user.must_change_password = True
        user.password_changed_at = timezone.now()
        user.save()
        
        return user


class UserUpdateSerializer(BaseValidationMixin, serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'middle_name', 'email', 'phone_number',
            'date_of_birth', 'gender', 'address_line1', 'address_line2',
            'city', 'state', 'postal_code', 'country',
            'emergency_contact_name', 'emergency_contact_phone',
            'emergency_contact_relationship', 'department', 'role',
            'job_title', 'manager', 'status'
        ]
    
    def validate_email(self, value):
        if value and self.instance:
            if User.objects.filter(email=value).exclude(id=self.instance.id).exists():
                raise serializers.ValidationError("Email address already exists.")
        return value
    
    def validate_phone_number(self, value):
        return self.validate_phone_field(value)
    
    def validate_date_of_birth(self, value):
        return self.validate_age_field(value)


class PasswordChangeSerializer(BaseValidationMixin, serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, min_length=8)
    new_password_confirm = serializers.CharField(required=True)
    
    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value
    
    def validate_new_password(self, value):
        return self.validate_password_field(value)
    
    def validate(self, attrs):
        new_password = attrs.get('new_password')
        new_password_confirm = attrs.get('new_password_confirm')
        
        if new_password != new_password_confirm:
            raise serializers.ValidationError("New passwords do not match.")
        
        return attrs
    
    def save(self):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        user.save()
        return user


class UserSessionSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    employee_code = serializers.CharField(source='user.employee_code', read_only=True)
    session_duration = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()
    
    class Meta:
        model = UserSession
        fields = [
            'id', 'user', 'user_name', 'employee_code',
            'ip_address', 'user_agent', 'login_time', 'last_activity',
            'logout_time', 'is_active', 'session_duration', 'is_expired'
        ]
        read_only_fields = ['login_time', 'last_activity', 'logout_time']
    
    def get_session_duration(self, obj):
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
    
    def get_is_expired(self, obj):
        return obj.is_expired()

class PasswordResetTokenSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    employee_code = serializers.CharField(source='user.employee_code', read_only=True)
    is_valid = serializers.BooleanField(source='is_valid', read_only=True)
    time_remaining = serializers.SerializerMethodField()
    
    class Meta:
        model = PasswordResetToken
        fields = [
            'id', 'user', 'user_name', 'employee_code',
            'created_at', 'expires_at', 'used_at', 'is_used',
            'is_valid', 'time_remaining', 'ip_address'
        ]
        read_only_fields = ['created_at', 'expires_at', 'used_at', 'is_used']
    
    def get_time_remaining(self, obj):
        if obj.is_used or obj.is_expired():
            return "Expired"
        
        remaining = obj.expires_at - timezone.now()
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"


class AuditLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    employee_code = serializers.CharField(source='user.employee_code', read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    formatted_timestamp = serializers.SerializerMethodField()
    
    class Meta:
        model = AuditLog
        fields = [
            'id', 'user', 'user_name', 'employee_code', 'action',
            'action_display', 'description', 'ip_address', 'user_agent',
            'timestamp', 'formatted_timestamp', 'additional_data'
        ]
        read_only_fields = ['timestamp']
    
    def get_formatted_timestamp(self, obj):
        return obj.timestamp.strftime('%Y-%m-%d %H:%M:%S')


class SystemConfigurationSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.CharField(source='updated_by.get_full_name', read_only=True)
    
    class Meta:
        model = SystemConfiguration
        fields = [
            'id', 'key', 'value', 'description', 'is_active',
            'created_at', 'updated_at', 'updated_by', 'updated_by_name'
        ]
        read_only_fields = ['created_at', 'updated_at']


class LoginSerializer(serializers.Serializer):
    employee_code = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)
    remember_me = serializers.BooleanField(required=False, default=False)
    
    def validate(self, attrs):
        employee_code = attrs.get('employee_code')
        password = attrs.get('password')
        
        if employee_code and password:
            try:
                user = User.objects.get(employee_code=employee_code.upper())
                
                if user.is_account_locked():
                    raise serializers.ValidationError(
                        "Account is temporarily locked due to multiple failed login attempts."
                    )
                
                if not user.is_active:
                    raise serializers.ValidationError("Account has been deactivated.")
                
                if user.status != 'ACTIVE':
                    raise serializers.ValidationError(
                        f"Account status is {user.get_status_display()}. Please contact HR."
                    )
                
                if not user.check_password(password):
                    user.increment_failed_login()
                    raise serializers.ValidationError("Invalid employee code or password.")
                
                user.reset_failed_login()
                attrs['user'] = user
                
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid employee code or password.")
        
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    
    def validate_email(self, value):
        if not User.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError("No active account found with this email address.")
        return value


class PasswordResetConfirmSerializer(BaseValidationMixin, serializers.Serializer):
    token = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, min_length=8)
    new_password_confirm = serializers.CharField(required=True)
    
    def validate_token(self, value):
        try:
            token = PasswordResetToken.objects.get(token=value)
            if not token.is_valid():
                raise serializers.ValidationError("Token is invalid or has expired.")
            self.context['token'] = token
        except PasswordResetToken.DoesNotExist:
            raise serializers.ValidationError("Invalid token.")
        return value
    
    def validate_new_password(self, value):
        return self.validate_password_field(value)
    
    def validate(self, attrs):
        new_password = attrs.get('new_password')
        new_password_confirm = attrs.get('new_password_confirm')
        
        if new_password != new_password_confirm:
            raise serializers.ValidationError("Passwords do not match.")
        
        return attrs
    
    def save(self):
        token = self.context['token']
        user = token.user
        
        user.set_password(self.validated_data['new_password'])
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        user.save()
        
        token.use_token()
        return user


class BulkUserActionSerializer(serializers.Serializer):
    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        allow_empty=False
    )
    action = serializers.ChoiceField(
        choices=[
            ('activate', 'Activate'),
            ('deactivate', 'Deactivate'),
            ('suspend', 'Suspend'),
            ('terminate', 'Terminate'),
            ('reset_password', 'Reset Password'),
            ('unlock_account', 'Unlock Account')
        ],
        required=True
    )
    reason = serializers.CharField(required=False, allow_blank=True)
    
    def validate_user_ids(self, value):
        existing_ids = User.objects.filter(id__in=value).values_list('id', flat=True)
        missing_ids = set(value) - set(existing_ids)
        
        if missing_ids:
            raise serializers.ValidationError(f"Users not found: {list(missing_ids)}")
        
        return value


class UserStatsSerializer(serializers.Serializer):
    total_users = serializers.IntegerField()
    active_users = serializers.IntegerField()
    inactive_users = serializers.IntegerField()
    suspended_users = serializers.IntegerField()
    terminated_users = serializers.IntegerField()
    users_by_department = serializers.DictField()
    users_by_role = serializers.DictField()
    recent_registrations = serializers.IntegerField()
    password_expiry_warnings = serializers.IntegerField()


class DashboardStatsSerializer(serializers.Serializer):
    user_stats = UserStatsSerializer()
    session_stats = serializers.DictField()
    security_stats = serializers.DictField()
    system_stats = serializers.DictField()
    recent_activities = AuditLogSerializer(many=True)


class UserSearchSerializer(serializers.Serializer):
    query = serializers.CharField(required=False, allow_blank=True)
    department_id = serializers.IntegerField(required=False)
    role_id = serializers.IntegerField(required=False)
    status = serializers.ChoiceField(
        choices=User.STATUS_CHOICES,
        required=False
    )
    hire_date_from = serializers.DateField(required=False)
    hire_date_to = serializers.DateField(required=False)
    is_active = serializers.BooleanField(required=False)
    
    def validate(self, attrs):
        hire_date_from = attrs.get('hire_date_from')
        hire_date_to = attrs.get('hire_date_to')
        
        if hire_date_from and hire_date_to and hire_date_from > hire_date_to:
            raise serializers.ValidationError("Hire date from cannot be greater than hire date to.")
        
        return attrs


class ExportUsersSerializer(serializers.Serializer):
    format = serializers.ChoiceField(
        choices=[('excel', 'Excel'), ('csv', 'CSV'), ('pdf', 'PDF')],
        default='excel'
    )
    include_inactive = serializers.BooleanField(default=False)
    department_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=True
    )
    role_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=True
    )
    fields = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True
    )


class ImportUsersSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)
    update_existing = serializers.BooleanField(default=False)
    send_welcome_email = serializers.BooleanField(default=True)
    
    def validate_file(self, value):
        if not value.name.endswith(('.xlsx', '.xls', '.csv')):
            raise serializers.ValidationError(
                "Only Excel (.xlsx, .xls) and CSV files are allowed."
            )
        
        max_size = int(SystemConfiguration.get_setting('MAX_UPLOAD_SIZE_MB', '10'))
        if value.size > max_size * 1024 * 1024:
            raise serializers.ValidationError(f"File size must be less than {max_size}MB.")
        
        return value


class NotificationSerializer(serializers.Serializer):
    recipient_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        allow_empty=False
    )
    subject = serializers.CharField(required=True, max_length=200)
    message = serializers.CharField(required=True)
    send_email = serializers.BooleanField(default=True)
    
    def validate_recipient_ids(self, value):
        existing_ids = User.objects.filter(
            id__in=value,
            is_active=True
        ).values_list('id', flat=True)
        
        missing_ids = set(value) - set(existing_ids)
        if missing_ids:
            raise serializers.ValidationError(f"Active users not found: {list(missing_ids)}")
        
        return value


class SystemHealthSerializer(serializers.Serializer):
    database_status = serializers.CharField()
    cache_status = serializers.CharField()
    email_service_status = serializers.CharField()
    disk_usage = serializers.DictField()
    memory_usage = serializers.DictField()
    active_sessions = serializers.IntegerField()
    failed_login_attempts = serializers.IntegerField()
    system_uptime = serializers.CharField()
    last_backup = serializers.DateTimeField()


class AuditReportSerializer(serializers.Serializer):
    start_date = serializers.DateTimeField(required=True)
    end_date = serializers.DateTimeField(required=True)
    user_filter = serializers.CharField(required=False, allow_blank=True)
    action_filter = serializers.ChoiceField(
        choices=AuditLog.ACTION_TYPES,
        required=False
    )
    export_format = serializers.ChoiceField(
        choices=[('json', 'JSON'), ('excel', 'Excel'), ('pdf', 'PDF')],
        default='json'
    )
    
    def validate(self, attrs):
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')
        
        if start_date and end_date and start_date >= end_date:
            raise serializers.ValidationError("Start date must be before end date.")
        
        if end_date and end_date > timezone.now():
            raise serializers.ValidationError("End date cannot be in the future.")
        
        return attrs


class ProfileSerializer(BaseValidationMixin, serializers.ModelSerializer):
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    age = serializers.SerializerMethodField()
    years_of_service = serializers.SerializerMethodField()
    department_name = serializers.CharField(source='department.name', read_only=True)
    role_name = serializers.CharField(source='role.display_name', read_only=True)
    manager_name = serializers.CharField(source='manager.get_full_name', read_only=True)
    
    class Meta:
        model = User
        fields = [
            'id', 'employee_code', 'first_name', 'last_name', 'middle_name',
            'full_name', 'email', 'phone_number', 'date_of_birth', 'age',
            'gender', 'address_line1', 'address_line2', 'city', 'state',
            'postal_code', 'country', 'emergency_contact_name',
            'emergency_contact_phone', 'emergency_contact_relationship',
            'department_name', 'role_name', 'job_title', 'hire_date',
            'years_of_service', 'manager_name'
        ]
        read_only_fields = ['employee_code', 'hire_date']
    
    def get_age(self, obj):
        if obj.date_of_birth:
            today = timezone.now().date()
            return today.year - obj.date_of_birth.year - ((today.month, today.day) < (obj.date_of_birth.month, obj.date_of_birth.day))
        return None
    
    def get_years_of_service(self, obj):
        if obj.hire_date:
            today = timezone.now().date()
            years = today.year - obj.hire_date.year
            if (today.month, today.day) < (obj.hire_date.month, obj.hire_date.day):
                years -= 1
            return years
        return None
    
    def validate_phone_number(self, value):
        return self.validate_phone_field(value)


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ['id', 'name', 'codename', 'content_type']


class UserPermissionsSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    permissions = serializers.ListField(child=serializers.CharField())
    role_permissions = serializers.ListField(child=serializers.CharField())
    effective_permissions = serializers.ListField(child=serializers.CharField())
    is_superuser = serializers.BooleanField()
    can_access_modules = serializers.DictField()
