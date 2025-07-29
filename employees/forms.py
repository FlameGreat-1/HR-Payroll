from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.admin.widgets import AdminDateWidget, AdminTimeWidget
from django.contrib.auth import get_user_model
from accounts.models import CustomUser, Department
from .models import EmployeeProfile, Education, Contract
from decimal import Decimal
import datetime


User = get_user_model()


class EmployeeProfileForm(forms.ModelForm):

    class Meta:
        model = EmployeeProfile
        fields = [
            "user",
            "employee_id",
            "employment_status",
            "grade_level",
            "basic_salary",
            "probation_end_date",
            "confirmation_date",
            "bank_name",
            "bank_account_number",
            "bank_branch",
            "tax_identification_number",
            "marital_status",
            "spouse_name",
            "number_of_children",
            "work_location",
            "reporting_time",
            "shift_hours",
            "is_active",
        ]
        widgets = {
            "probation_end_date": AdminDateWidget(),
            "confirmation_date": AdminDateWidget(),
            "reporting_time": AdminTimeWidget(),
            "basic_salary": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "class": "form-control"}
            ),
            "shift_hours": forms.NumberInput(
                attrs={
                    "step": "0.25",
                    "min": "1.00",
                    "max": "24.00",
                    "class": "form-control",
                }
            ),
            "number_of_children": forms.NumberInput(
                attrs={"min": "0", "class": "form-control"}
            ),
            "bank_account_number": forms.TextInput(
                attrs={
                    "placeholder": "Enter 8-20 digit account number",
                    "class": "form-control",
                }
            ),
            "tax_identification_number": forms.TextInput(
                attrs={"placeholder": "Enter unique tax ID", "class": "form-control"}
            ),
            "spouse_name": forms.TextInput(
                attrs={
                    "placeholder": "Enter spouse name if married",
                    "class": "form-control",
                }
            ),
            "work_location": forms.TextInput(
                attrs={
                    "placeholder": "Enter work location/office",
                    "class": "form-control",
                }
            ),
        }
        help_texts = {
            "employee_id": "Unique employee identifier (auto-generated if empty)",
            "basic_salary": "Monthly basic salary amount",
            "probation_end_date": "Required for probation status employees",
            "confirmation_date": "Date when employee was confirmed",
            "bank_account_number": "Bank account number for salary payments",
            "tax_identification_number": "Unique tax identification number",
            "shift_hours": "Standard working hours per day",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["user"].queryset = CustomUser.active.all()

        if not self.instance.pk:
            # SAFE VERSION - Check before accessing
            if "employee_id" in self.fields:
                self.fields["employee_id"].required = False
                self.fields[
                    "employee_id"
                ].help_text += " (Leave empty for auto-generation)"
            else:
                print("WARNING: employee_id field not found in form fields!")

        self._setup_conditional_fields()

        for field_name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"

    def _setup_conditional_fields(self):
        if self.instance.pk and self.instance.employment_status == "PROBATION":
            self.fields["probation_end_date"].required = True

        if self.data.get("employment_status") == "PROBATION":
            self.fields["probation_end_date"].required = True

    def clean_user(self):
        user = self.cleaned_data.get("user")
        if user:
            existing_profile = EmployeeProfile.objects.filter(user=user)
            if self.instance.pk:
                existing_profile = existing_profile.exclude(pk=self.instance.pk)

            if existing_profile.exists():
                raise ValidationError(
                    f"User {user.get_full_name()} already has an employee profile."
                )
        return user

    def clean_basic_salary(self):
        salary = self.cleaned_data.get("basic_salary")
        if salary and salary <= 0:
            raise ValidationError("Basic salary must be greater than zero.")

        if salary and salary > Decimal("1000000.00"):
            raise ValidationError("Basic salary seems unusually high. Please verify.")

        return salary

    def clean_probation_end_date(self):
        probation_end_date = self.cleaned_data.get("probation_end_date")
        employment_status = self.cleaned_data.get("employment_status")
        user = self.cleaned_data.get("user")

        if employment_status == "PROBATION":
            if not probation_end_date:
                raise ValidationError(
                    "Probation end date is required for probation status."
                )

            if probation_end_date <= timezone.now().date():
                raise ValidationError("Probation end date must be in the future.")

            if user and user.hire_date:
                if probation_end_date <= user.hire_date:
                    raise ValidationError("Probation end date must be after hire date.")

        return probation_end_date

    def clean_confirmation_date(self):
        confirmation_date = self.cleaned_data.get("confirmation_date")
        user = self.cleaned_data.get("user")

        if confirmation_date:
            if confirmation_date > timezone.now().date():
                raise ValidationError("Confirmation date cannot be in the future.")

            if user and user.hire_date:
                if confirmation_date < user.hire_date:
                    raise ValidationError(
                        "Confirmation date cannot be before hire date."
                    )

        return confirmation_date

    def clean_tax_identification_number(self):
        tax_id = self.cleaned_data.get("tax_identification_number")
        if tax_id:
            existing_tax_id = EmployeeProfile.objects.filter(
                tax_identification_number=tax_id
            )
            if self.instance.pk:
                existing_tax_id = existing_tax_id.exclude(pk=self.instance.pk)

            if existing_tax_id.exists():
                raise ValidationError(
                    "This tax identification number is already in use."
                )

        return tax_id

    def clean_spouse_name(self):
        spouse_name = self.cleaned_data.get("spouse_name")
        marital_status = self.cleaned_data.get("marital_status")

        if marital_status == "MARRIED" and not spouse_name:
            raise ValidationError("Spouse name is required for married employees.")

        if marital_status != "MARRIED" and spouse_name:
            return None

        return spouse_name

    def clean(self):
        cleaned_data = super().clean()

        employment_status = cleaned_data.get("employment_status")
        probation_end_date = cleaned_data.get("probation_end_date")
        confirmation_date = cleaned_data.get("confirmation_date")

        if (
            probation_end_date
            and confirmation_date
            and confirmation_date <= probation_end_date
        ):
            raise ValidationError(
                {
                    "confirmation_date": "Confirmation date must be after probation end date."
                }
            )

        return cleaned_data

class EducationForm(forms.ModelForm):

    class Meta:
        model = Education
        fields = [
            "employee",
            "education_level",
            "qualification",
            "institution",
            "field_of_study",
            "start_year",
            "completion_year",
            "grade_gpa",
            "certificate_file",
            "is_active",
        ]
        widgets = {
            "start_year": forms.NumberInput(
                attrs={
                    "min": "1950",
                    "max": str(timezone.now().year),
                    "class": "form-control",
                }
            ),
            "completion_year": forms.NumberInput(
                attrs={
                    "min": "1950",
                    "max": str(timezone.now().year),
                    "class": "form-control",
                }
            ),
            "qualification": forms.TextInput(
                attrs={
                    "placeholder": "e.g., Bachelor of Science in Computer Science",
                    "class": "form-control",
                }
            ),
            "institution": forms.TextInput(
                attrs={
                    "placeholder": "e.g., University of Colombo",
                    "class": "form-control",
                }
            ),
            "field_of_study": forms.TextInput(
                attrs={
                    "placeholder": "e.g., Computer Science, Engineering",
                    "class": "form-control",
                }
            ),
            "grade_gpa": forms.TextInput(
                attrs={
                    "placeholder": "e.g., 3.8 GPA, First Class, 85%",
                    "class": "form-control",
                }
            ),
        }
        help_texts = {
            "qualification": "Full name of the degree/certificate obtained",
            "institution": "Name of the educational institution",
            "field_of_study": "Major or field of study",
            "grade_gpa": "Grade, GPA, or percentage obtained",
            "certificate_file": "Upload scanned copy of certificate (PDF, JPG, PNG)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["employee"].queryset = CustomUser.active.all()

        for field_name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"

    def clean_start_year(self):
        start_year = self.cleaned_data.get("start_year")
        current_year = timezone.now().year

        if start_year and start_year > current_year:
            raise ValidationError("Start year cannot be in the future.")

        return start_year

    def clean_completion_year(self):
        completion_year = self.cleaned_data.get("completion_year")
        current_year = timezone.now().year

        if completion_year and completion_year > current_year:
            raise ValidationError("Completion year cannot be in the future.")

        return completion_year

    def clean(self):
        cleaned_data = super().clean()
        start_year = cleaned_data.get("start_year")
        completion_year = cleaned_data.get("completion_year")

        if start_year and completion_year:
            if completion_year < start_year:
                raise ValidationError(
                    {"completion_year": "Completion year cannot be before start year."}
                )

            duration = completion_year - start_year
            if duration > 15:
                raise ValidationError(
                    {
                        "completion_year": "Education duration seems unusually long. Please verify."
                    }
                )

        return cleaned_data


class ContractForm(forms.ModelForm):

    class Meta:
        model = Contract
        fields = [
            "employee",
            "contract_number",
            "contract_type",
            "status",
            "start_date",
            "end_date",
            "signed_date",
            "job_title",
            "department",
            "reporting_manager",
            "basic_salary",
            "terms_and_conditions",
            "benefits",
            "working_hours",
            "probation_period_months",
            "notice_period_days",
            "contract_file",
            "is_active",
        ]
        widgets = {
            "start_date": AdminDateWidget(),
            "end_date": AdminDateWidget(),
            "signed_date": AdminDateWidget(),
            "basic_salary": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "class": "form-control"}
            ),
            "working_hours": forms.NumberInput(
                attrs={
                    "step": "0.25",
                    "min": "1.00",
                    "max": "24.00",
                    "class": "form-control",
                }
            ),
            "probation_period_months": forms.NumberInput(
                attrs={"min": "0", "max": "24", "class": "form-control"}
            ),
            "notice_period_days": forms.NumberInput(
                attrs={"min": "0", "max": "365", "class": "form-control"}
            ),
            "terms_and_conditions": forms.Textarea(
                attrs={
                    "rows": 6,
                    "placeholder": "Enter detailed terms and conditions...",
                    "class": "form-control",
                }
            ),
            "benefits": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Enter employee benefits...",
                    "class": "form-control",
                }
            ),
            "job_title": forms.TextInput(
                attrs={
                    "placeholder": "e.g., Senior Software Engineer",
                    "class": "form-control",
                }
            ),
        }
        help_texts = {
            "contract_number": "Unique contract identifier (auto-generated if empty)",
            "end_date": "Leave empty for permanent contracts",
            "basic_salary": "Monthly basic salary amount",
            "working_hours": "Standard working hours per day",
            "probation_period_months": "Probation period in months (0 for no probation)",
            "notice_period_days": "Notice period required for termination",
            "contract_file": "Upload signed contract document (PDF recommended)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["employee"].queryset = CustomUser.active.all()
        self.fields["department"].queryset = Department.active.all()
        self.fields["reporting_manager"].queryset = CustomUser.active.all()

        if not self.instance.pk:
            self.fields["contract_number"].required = False
            self.fields[
                "contract_number"
            ].help_text += " (Leave empty for auto-generation)"

        self._setup_conditional_fields()

        for field_name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"

    def _setup_conditional_fields(self):
        contract_type = self.data.get("contract_type") or (
            self.instance.contract_type if self.instance.pk else None
        )

        if contract_type in ["FIXED_TERM", "INTERNSHIP", "CONSULTANT"]:
            self.fields["end_date"].required = True

    def clean_employee(self):
        employee = self.cleaned_data.get("employee")
        if employee and not employee.is_active:
            raise ValidationError("Cannot create contract for inactive employee.")
        return employee

    def clean_start_date(self):
        start_date = self.cleaned_data.get("start_date")

        if start_date:
            if start_date > timezone.now().date() + datetime.timedelta(days=365):
                raise ValidationError(
                    "Start date cannot be more than 1 year in the future."
                )

        return start_date

    def clean_end_date(self):
        end_date = self.cleaned_data.get("end_date")
        contract_type = self.cleaned_data.get("contract_type")

        if contract_type in ["FIXED_TERM", "INTERNSHIP", "CONSULTANT"] and not end_date:
            raise ValidationError(
                f"End date is required for {contract_type} contracts."
            )

        return end_date

    def clean_signed_date(self):
        signed_date = self.cleaned_data.get("signed_date")

        if signed_date and signed_date > timezone.now().date():
            raise ValidationError("Signed date cannot be in the future.")

        return signed_date

    def clean_reporting_manager(self):
        reporting_manager = self.cleaned_data.get("reporting_manager")
        employee = self.cleaned_data.get("employee")

        if reporting_manager and employee:
            if reporting_manager == employee:
                raise ValidationError("Employee cannot be their own reporting manager.")

        return reporting_manager

    def clean_basic_salary(self):
        salary = self.cleaned_data.get("basic_salary")
        if salary and salary <= 0:
            raise ValidationError("Basic salary must be greater than zero.")

        if salary and salary > Decimal("10000000.00"):
            raise ValidationError("Basic salary seems unusually high. Please verify.")

        return salary

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        signed_date = cleaned_data.get("signed_date")
        employee = cleaned_data.get("employee")

        if start_date and end_date:
            if end_date <= start_date:
                raise ValidationError(
                    {"end_date": "End date must be after start date."}
                )

            duration_days = (end_date - start_date).days
            if duration_days > 3650:
                raise ValidationError(
                    {"end_date": "Contract duration cannot exceed 10 years."}
                )

        if signed_date and start_date:
            if signed_date > start_date:
                raise ValidationError(
                    {"signed_date": "Signed date should not be after start date."}
                )

        if employee and start_date and end_date:
            overlapping_contracts = Contract.objects.filter(
                employee=employee,
                status="ACTIVE",
                start_date__lte=end_date,
                end_date__gte=start_date,
            )
            if self.instance.pk:
                overlapping_contracts = overlapping_contracts.exclude(
                    pk=self.instance.pk
                )

            if overlapping_contracts.exists():
                raise ValidationError(
                    f"Contract dates overlap with existing active contract: "
                    f"{overlapping_contracts.first().contract_number}"
                )

        return cleaned_data


class BulkEmployeeImportForm(forms.Form):

    csv_file = forms.FileField(
        label="CSV File",
        help_text="Upload CSV file with employee data",
        widget=forms.FileInput(attrs={"accept": ".csv", "class": "form-control"}),
    )
    update_existing = forms.BooleanField(
        required=False,
        initial=False,
        label="Update Existing Records",
        help_text="Check to update existing employee profiles if employee_id matches",
    )

    def clean_csv_file(self):
        csv_file = self.cleaned_data.get("csv_file")

        if csv_file:
            if not csv_file.name.endswith(".csv"):
                raise ValidationError("File must be a CSV file.")

            if csv_file.size > 5 * 1024 * 1024:
                raise ValidationError("File size cannot exceed 5MB.")

        return csv_file


class EmployeeSearchForm(forms.Form):

    search_query = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search by name, employee ID, or email...",
                "class": "form-control",
            }
        ),
    )
    department = forms.ModelChoiceField(
        queryset=Department.active.all(),
        required=False,
        empty_label="All Departments",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    employment_status = forms.ChoiceField(
        choices=[("", "All Status")] + EmployeeProfile.EMPLOYMENT_STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    grade_level = forms.ChoiceField(
        choices=[("", "All Grades")] + EmployeeProfile.GRADE_LEVELS,
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    hire_date_from = forms.DateField(
        required=False, widget=AdminDateWidget(attrs={"class": "form-control"})
    )
    hire_date_to = forms.DateField(
        required=False, widget=AdminDateWidget(attrs={"class": "form-control"})
    )
    is_active = forms.ChoiceField(
        choices=[("", "All"), ("true", "Active"), ("false", "Inactive")],
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )


class ContractRenewalForm(forms.ModelForm):

    class Meta:
        model = Contract
        fields = [
            "contract_type",
            "start_date",
            "end_date",
            "basic_salary",
            "terms_and_conditions",
            "benefits",
            "working_hours",
            "probation_period_months",
            "notice_period_days",
        ]
        widgets = {
            "start_date": AdminDateWidget(),
            "end_date": AdminDateWidget(),
            "basic_salary": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "class": "form-control"}
            ),
            "working_hours": forms.NumberInput(
                attrs={
                    "step": "0.25",
                    "min": "1.00",
                    "max": "24.00",
                    "class": "form-control",
                }
            ),
            "terms_and_conditions": forms.Textarea(
                attrs={"rows": 6, "class": "form-control"}
            ),
            "benefits": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
        }

    def __init__(self, original_contract, *args, **kwargs):
        self.original_contract = original_contract
        super().__init__(*args, **kwargs)

        for field_name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"

    def clean_start_date(self):
        start_date = self.cleaned_data.get("start_date")

        if start_date and self.original_contract.end_date:
            if start_date <= self.original_contract.end_date:
                raise ValidationError(
                    "New contract start date must be after previous contract end date."
                )

        return start_date

    def save(self, commit=True):
        new_contract = super().save(commit=False)
        new_contract.employee = self.original_contract.employee
        new_contract.department = self.original_contract.department
        new_contract.reporting_manager = self.original_contract.reporting_manager
        new_contract.job_title = self.original_contract.job_title
        new_contract.status = "DRAFT"

        if commit:
            new_contract.save()

        return new_contract
