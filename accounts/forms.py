from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.validators import RegexValidator, EmailValidator
from .models import Department, Role, AuditLog, SystemConfiguration
from .utils import validate_password_strength
import re
from datetime import datetime, timedelta

User = get_user_model()


def validate_password_field(password):
    is_valid, errors = validate_password_strength(password)
    if not is_valid:
        raise ValidationError(errors)
    return password


class CustomLoginForm(AuthenticationForm):
    username = forms.CharField(
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Employee Code",
                "autofocus": True,
                "autocomplete": "username",
            }
        ),
        label="Employee Code",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Password",
                "autocomplete": "current-password",
            }
        ),
        label="Password",
    )
    remember_me = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Remember me",
    )

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.request = request
        self.user_cache = None

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username and password:
            try:
                user = User.objects.get(employee_code=username)

                if user.is_account_locked():
                    raise ValidationError(
                        "Your account is temporarily locked due to multiple failed login attempts. Please try again later.",
                        code="account_locked",
                    )

                if not user.is_active:
                    raise ValidationError(
                        "This account has been deactivated.", code="inactive"
                    )

                if user.status != "ACTIVE":
                    raise ValidationError(
                        f"Account status is {user.get_status_display()}. Please contact HR.",
                        code="invalid_status",
                    )

                self.user_cache = authenticate(
                    self.request, username=username, password=password
                )

                if self.user_cache is None:
                    user.increment_failed_login()
                    raise ValidationError(
                        "Invalid employee code or password.", code="invalid_login"
                    )
                else:
                    user.reset_failed_login()
                    self.confirm_login_allowed(self.user_cache)

            except User.DoesNotExist:
                raise ValidationError(
                    "Invalid employee code or password.", code="invalid_login"
                )

        return self.cleaned_data

    def get_user(self):
        return self.user_cache


class EmployeeRegistrationForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Enter password"}
        ),
        help_text="Password must be at least 8 characters long and contain uppercase, lowercase, number, and special character.",
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Confirm password"}
        ),
    )

    class Meta:
        model = User
        fields = [
            "employee_code",
            "first_name",
            "last_name",
            "middle_name",
            "email",
            "phone_number",
            "date_of_birth",
            "gender",
            "department",
            "role",
            "job_title",
            "hire_date",
            "manager",
        ]
        widgets = {
            "employee_code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Employee Code (e.g., EMP001)",
                }
            ),
            "first_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "First Name"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Last Name"}
            ),
            "middle_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Middle Name (Optional)"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "Email Address"}
            ),
            "phone_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Phone Number"}
            ),
            "date_of_birth": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "gender": forms.Select(attrs={"class": "form-select"}),
            "department": forms.Select(attrs={"class": "form-select"}),
            "role": forms.Select(attrs={"class": "form-select"}),
            "job_title": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Job Title"}
            ),
            "hire_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "manager": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.active.all()
        self.fields["role"].queryset = Role.active.all()
        self.fields["manager"].queryset = User.active.all()
        self.fields["manager"].empty_label = "Select Manager (Optional)"

    def clean_employee_code(self):
        employee_code = self.cleaned_data.get("employee_code")
        if employee_code:
            employee_code = employee_code.upper()
            if User.objects.filter(employee_code=employee_code).exists():
                raise ValidationError("Employee code already exists.")
        return employee_code

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email:
            if User.objects.filter(email=email).exists():
                raise ValidationError("Email address already exists.")
        return email

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number")
        if phone:
            phone_regex = re.compile(r"^\+?[1-9]\d{1,14}$")
            if not phone_regex.match(phone):
                raise ValidationError("Enter a valid phone number.")
        return phone

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob:
            today = timezone.now().date()
            age = (
                today.year
                - dob.year
                - ((today.month, today.day) < (dob.month, dob.day))
            )
            min_age = int(SystemConfiguration.get_setting("MIN_EMPLOYEE_AGE", "18"))
            max_age = int(SystemConfiguration.get_setting("MAX_EMPLOYEE_AGE", "65"))

            if age < min_age:
                raise ValidationError(f"Employee must be at least {min_age} years old.")
            if age > max_age:
                raise ValidationError("Please verify the date of birth.")
        return dob

    def clean_hire_date(self):
        hire_date = self.cleaned_data.get("hire_date")
        if hire_date:
            if hire_date > timezone.now().date():
                raise ValidationError("Hire date cannot be in the future.")
        return hire_date

    def clean_password1(self):
        password1 = self.cleaned_data.get("password1")
        if password1:
            return validate_password_field(password1)
        return password1

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise ValidationError("Passwords do not match.")
        return password2

    def clean(self):
        cleaned_data = super().clean()
        manager = cleaned_data.get("manager")
        employee_code = cleaned_data.get("employee_code")

        if manager and manager.employee_code == employee_code:
            raise ValidationError("Employee cannot be their own manager.")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.must_change_password = True
        user.password_changed_at = timezone.now()
        if commit:
            user.save()
        return user


class EmployeeUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "middle_name",
            "email",
            "phone_number",
            "date_of_birth",
            "gender",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
            "department",
            "role",
            "job_title",
            "manager",
            "status",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "middle_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control"}),
            "date_of_birth": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "gender": forms.Select(attrs={"class": "form-select"}),
            "address_line1": forms.TextInput(attrs={"class": "form-control"}),
            "address_line2": forms.TextInput(attrs={"class": "form-control"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "state": forms.TextInput(attrs={"class": "form-control"}),
            "postal_code": forms.TextInput(attrs={"class": "form-control"}),
            "country": forms.TextInput(attrs={"class": "form-control"}),
            "emergency_contact_name": forms.TextInput(attrs={"class": "form-control"}),
            "emergency_contact_phone": forms.TextInput(attrs={"class": "form-control"}),
            "emergency_contact_relationship": forms.TextInput(
                attrs={"class": "form-control"}
            ),
            "department": forms.Select(attrs={"class": "form-select"}),
            "role": forms.Select(attrs={"class": "form-select"}),
            "job_title": forms.TextInput(attrs={"class": "form-control"}),
            "manager": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop("current_user", None)
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.active.all()
        self.fields["role"].queryset = Role.active.all()
        self.fields["manager"].queryset = User.active.exclude(
            id=self.instance.id if self.instance else None
        )
        self.fields["manager"].empty_label = "Select Manager (Optional)"

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and self.instance:
            if User.objects.filter(email=email).exclude(id=self.instance.id).exists():
                raise ValidationError("Email address already exists.")
        return email

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number")
        if phone:
            phone_regex = re.compile(r"^\+?[1-9]\d{1,14}$")
            if not phone_regex.match(phone):
                raise ValidationError("Enter a valid phone number.")
        return phone


class CustomPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label="Current Password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Current Password"}
        ),
    )
    new_password1 = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "New Password"}
        ),
        help_text="Password must be at least 8 characters long and contain uppercase, lowercase, number, and special character.",
    )
    new_password2 = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Confirm New Password"}
        ),
    )

    def clean_new_password1(self):
        password1 = self.cleaned_data.get("new_password1")
        if password1:
            return validate_password_field(password1)
        return password1

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        if commit:
            user.save()
        return user


class CustomPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        label="Email Address",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter your email address",
                "autocomplete": "email",
            }
        ),
    )

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email:
            if not User.objects.filter(email=email, is_active=True).exists():
                raise ValidationError(
                    "No active account found with this email address."
                )
        return email


class CustomSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "New Password"}
        ),
        help_text="Password must be at least 8 characters long and contain uppercase, lowercase, number, and special character.",
    )
    new_password2 = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Confirm New Password"}
        ),
    )

    def clean_new_password1(self):
        password1 = self.cleaned_data.get("new_password1")
        if password1:
            return validate_password_field(password1)
        return password1

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        if commit:
            user.save()
        return user


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "code", "description", "manager", "parent_department"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Department Name"}
            ),
            "code": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Department Code"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Department Description",
                }
            ),
            "manager": forms.Select(attrs={"class": "form-select"}),
            "parent_department": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manager"].queryset = User.active.all()
        self.fields["parent_department"].queryset = Department.active.exclude(
            id=self.instance.id if self.instance else None
        )
        self.fields["manager"].empty_label = "Select Manager (Optional)"
        self.fields["parent_department"].empty_label = (
            "Select Parent Department (Optional)"
        )

    def clean_code(self):
        code = self.cleaned_data.get("code")
        if code:
            code = code.upper()
            if self.instance and self.instance.pk:
                if (
                    Department.objects.filter(code=code)
                    .exclude(pk=self.instance.pk)
                    .exists()
                ):
                    raise ValidationError("Department code already exists.")
            else:
                if Department.objects.filter(code=code).exists():
                    raise ValidationError("Department code already exists.")
        return code

    def clean(self):
        cleaned_data = super().clean()
        parent_department = cleaned_data.get("parent_department")

        if parent_department and self.instance:
            if parent_department == self.instance:
                raise ValidationError("Department cannot be its own parent.")

        return cleaned_data


class RoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = ["name", "display_name", "description", "permissions"]
        widgets = {
            "name": forms.Select(attrs={"class": "form-select"}),
            "display_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Display Name"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Role Description",
                }
            ),
            "permissions": forms.CheckboxSelectMultiple(
                attrs={"class": "form-check-input"}
            ),
        }


class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "middle_name",
            "phone_number",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "middle_name": forms.TextInput(attrs={"class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control"}),
            "address_line1": forms.TextInput(attrs={"class": "form-control"}),
            "address_line2": forms.TextInput(attrs={"class": "form-control"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "state": forms.TextInput(attrs={"class": "form-control"}),
            "postal_code": forms.TextInput(attrs={"class": "form-control"}),
            "country": forms.TextInput(attrs={"class": "form-control"}),
            "emergency_contact_name": forms.TextInput(attrs={"class": "form-control"}),
            "emergency_contact_phone": forms.TextInput(attrs={"class": "form-control"}),
            "emergency_contact_relationship": forms.TextInput(
                attrs={"class": "form-control"}
            ),
        }

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number")
        if phone:
            phone_regex = re.compile(r"^\+?[1-9]\d{1,14}$")
            if not phone_regex.match(phone):
                raise ValidationError("Enter a valid phone number.")
        return phone


class BulkEmployeeUploadForm(forms.Form):
    excel_file = forms.FileField(
        label="Excel File",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".xlsx,.xls"}),
        help_text="Upload Excel file with employee data",
    )

    def clean_excel_file(self):
        file = self.cleaned_data.get("excel_file")
        if file:
            if not file.name.endswith((".xlsx", ".xls")):
                raise ValidationError("Only Excel files (.xlsx, .xls) are allowed.")

            max_size = int(SystemConfiguration.get_setting("MAX_UPLOAD_SIZE_MB", "5"))
            if file.size > max_size * 1024 * 1024:
                raise ValidationError(f"File size must be less than {max_size}MB.")

        return file


class UserSearchForm(forms.Form):
    search_query = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Search by name, employee code, or email",
            }
        ),
    )
    department = forms.ModelChoiceField(
        queryset=Department.active.all(),
        required=False,
        empty_label="All Departments",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    role = forms.ModelChoiceField(
        queryset=Role.active.all(),
        required=False,
        empty_label="All Roles",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    status = forms.ChoiceField(
        choices=[("", "All Status")] + User.STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class SystemConfigurationForm(forms.Form):
    company_name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting("COMPANY_NAME", "HR System"),
    )
    company_address = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        initial=lambda: SystemConfiguration.get_setting("COMPANY_ADDRESS", ""),
    )
    company_phone = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting("COMPANY_PHONE", ""),
    )
    company_email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting("COMPANY_EMAIL", ""),
    )
    working_hours_per_day = forms.DecimalField(
        max_digits=4,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting(
            "WORKING_HOURS_PER_DAY", "8.00"
        ),
    )
    working_days_per_week = forms.IntegerField(
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting("WORKING_DAYS_PER_WEEK", "5"),
    )
    overtime_rate = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting("OVERTIME_RATE", "1.50"),
    )
    late_penalty_rate = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting("LATE_PENALTY_RATE", "0.10"),
    )
    password_expiry_days = forms.IntegerField(
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting("PASSWORD_EXPIRY_DAYS", "90"),
    )
    max_login_attempts = forms.IntegerField(
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting("MAX_LOGIN_ATTEMPTS", "5"),
    )
    session_timeout_minutes = forms.IntegerField(
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=lambda: SystemConfiguration.get_setting("SESSION_TIMEOUT", "30"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if hasattr(field, "initial") and callable(field.initial):
                field.initial = field.initial()
