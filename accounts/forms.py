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
from .models import Department, Role, AuditLog, SystemConfiguration, CustomUser
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
                    self.request, employee_code=username, password=password  
            
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
        model = CustomUser
        fields = [
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
            try:
                min_age = int(SystemConfiguration.get_setting("MIN_EMPLOYEE_AGE", "18"))
                max_age = int(SystemConfiguration.get_setting("MAX_EMPLOYEE_AGE", "65"))
            except:
                min_age = 18
                max_age = 65

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
        model = CustomUser
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
        model = CustomUser
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
            "emergency_contact_relationship": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number")
        if phone:
            phone_regex = re.compile(r"^\+?[1-9]\d{1,14}$")
            if not phone_regex.match(phone):
                raise ValidationError("Enter a valid phone number.")
        return phone


class BulkEmployeeUploadForm(forms.Form):
    file = forms.FileField(
        label="Upload Employee File",
        widget=forms.FileInput(
            attrs={"class": "form-control", "accept": ".xlsx,.xls,.csv"}
        ),
        help_text="Upload Excel or CSV file with employee data",
    )

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if file:
            if not file.name.endswith((".xlsx", ".xls", ".csv")):
                raise ValidationError(
                    "Only Excel (.xlsx, .xls) and CSV files are allowed."
                )
            if file.size > 5 * 1024 * 1024:  # 5MB limit
                raise ValidationError("File size cannot exceed 5MB.")
        return file


class UserSearchForm(forms.Form):
    search_query = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Search employees...",
                "autocomplete": "off",
            }
        ),
        label="Search",
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
        choices=[("", "All Status")] + CustomUser.STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class BulkEmployeeUploadForm(forms.Form):
    file = forms.FileField(
        label="Upload Employee File",
        widget=forms.FileInput(
            attrs={"class": "form-control", "accept": ".xlsx,.xls,.csv"}
        ),
        help_text="Upload Excel or CSV file with employee data",
    )

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if file:
            if not file.name.endswith((".xlsx", ".xls", ".csv")):
                raise ValidationError(
                    "Only Excel (.xlsx, .xls) and CSV files are allowed."
                )
            if file.size > 5 * 1024 * 1024:  # 5MB limit
                raise ValidationError("File size cannot exceed 5MB.")
        return file


class AdvancedUserFilterForm(forms.Form):
    hire_date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        label="Hired From",
    )
    hire_date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        label="Hired To",
    )
    is_active = forms.ChoiceField(
        choices=[("", "All"), ("true", "Active"), ("false", "Inactive")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Account Status",
    )
    is_verified = forms.ChoiceField(
        choices=[("", "All"), ("true", "Verified"), ("false", "Unverified")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Verification Status",
    )


class SystemConfigurationForm(forms.ModelForm):
    class Meta:
        model = SystemConfiguration
        fields = ["key", "value", "description", "is_active"]
        widgets = {
            "key": forms.TextInput(attrs={"class": "form-control"}),
            "value": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_key(self):
        key = self.cleaned_data.get("key")
        if key:
            key = key.upper().replace(" ", "_")
        return key
