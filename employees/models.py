from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from accounts.models import CustomUser, ActiveManager
from decimal import Decimal
import uuid
from datetime import date


class EmployeeProfile(models.Model):
    """Extended employee information beyond basic user data"""

    EMPLOYMENT_STATUS_CHOICES = [
        ("PROBATION", "Probation"),
        ("CONFIRMED", "Confirmed"),
        ("CONTRACT", "Contract"),
        ("INTERN", "Intern"),
        ("CONSULTANT", "Consultant"),
    ]

    GRADE_LEVELS = [
        ("ENTRY", "Entry Level"),
        ("JUNIOR", "Junior"),
        ("SENIOR", "Senior"),
        ("LEAD", "Lead"),
        ("MANAGER", "Manager"),
        ("DIRECTOR", "Director"),
        ("EXECUTIVE", "Executive"),
    ]

    MARITAL_STATUS_CHOICES = [
        ("SINGLE", "Single"),
        ("MARRIED", "Married"),
        ("DIVORCED", "Divorced"),
        ("WIDOWED", "Widowed"),
    ]

    id = models.AutoField(primary_key=True)
    user = models.OneToOneField(
        CustomUser, on_delete=models.CASCADE, related_name="employee_profile"
    )

    # Professional Details
    employee_id = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        help_text="Unique employee identifier (different from employee_code)",
    )
    employment_status = models.CharField(
        max_length=20, choices=EMPLOYMENT_STATUS_CHOICES, default="PROBATION"
    )
    grade_level = models.CharField(max_length=20, choices=GRADE_LEVELS, default="ENTRY")
    basic_salary = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    probation_end_date = models.DateField(null=True, blank=True)
    confirmation_date = models.DateField(null=True, blank=True)

    # Financial Details
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    bank_account_number = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        validators=[RegexValidator(r"^[0-9]{8,20}$", "Enter a valid account number")],
    )
    bank_branch = models.CharField(max_length=100, blank=True, null=True)
    tax_identification_number = models.CharField(
        max_length=20, blank=True, null=True, unique=True
    )

    # Personal Details (extending accounts)
    marital_status = models.CharField(
        max_length=20, choices=MARITAL_STATUS_CHOICES, blank=True, null=True
    )
    spouse_name = models.CharField(max_length=100, blank=True, null=True)
    number_of_children = models.PositiveIntegerField(default=0)

    # Work Details
    work_location = models.CharField(max_length=255, blank=True, null=True)
    reporting_time = models.TimeField(default="09:00:00")
    shift_hours = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=8.00,
        validators=[
            MinValueValidator(Decimal("1.00")),
            MaxValueValidator(Decimal("24.00")),
        ],
    )

    # System Fields
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_employee_profiles",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "employee_profiles"
        ordering = ["employee_id"]
        indexes = [
            models.Index(fields=["employee_id"]),
            models.Index(fields=["employment_status"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["basic_salary"]),
        ]

    def __str__(self):
        return f"{self.employee_id} - {self.user.get_full_name()}"

    def clean(self):
        # Validate probation end date
        if self.employment_status == "PROBATION" and not self.probation_end_date:
            raise ValidationError("Probation end date is required for probation status")

        if self.probation_end_date and self.probation_end_date <= timezone.now().date():
            if self.employment_status == "PROBATION":
                raise ValidationError("Probation end date cannot be in the past")

        # Validate confirmation date
        if self.confirmation_date and self.user.hire_date:
            if self.confirmation_date < self.user.hire_date:
                raise ValidationError("Confirmation date cannot be before hire date")

    def save(self, *args, **kwargs):
        if not self.employee_id:
            self.employee_id = self.generate_employee_id()
        self.full_clean()
        super().save(*args, **kwargs)

    def generate_employee_id(self):
        """Generate unique employee ID"""
        prefix = "EMP"
        existing_ids = EmployeeProfile.objects.filter(
            employee_id__startswith=prefix
        ).values_list("employee_id", flat=True)

        numbers = []
        for emp_id in existing_ids:
            try:
                number = int(emp_id.replace(prefix, ""))
                numbers.append(number)
            except ValueError:
                continue

        next_number = max(numbers) + 1 if numbers else 1
        return f"{prefix}{next_number:04d}"

    def soft_delete(self):
        """Soft delete employee profile"""
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])

    @property
    def is_on_probation(self):
        """Check if employee is currently on probation"""
        if self.employment_status != "PROBATION":
            return False
        if not self.probation_end_date:
            return True
        return timezone.now().date() <= self.probation_end_date

    @property
    def years_of_service(self):
        """Calculate years of service"""
        if not self.user.hire_date:
            return 0
        today = timezone.now().date()
        return (today - self.user.hire_date).days / 365.25


class Education(models.Model):
    """Employee education records"""

    EDUCATION_LEVELS = [
        ("HIGH_SCHOOL", "High School"),
        ("DIPLOMA", "Diploma"),
        ("BACHELOR", "Bachelor's Degree"),
        ("MASTER", "Master's Degree"),
        ("DOCTORATE", "Doctorate"),
        ("CERTIFICATE", "Professional Certificate"),
        ("OTHER", "Other"),
    ]

    id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="education_records"
    )
    education_level = models.CharField(max_length=20, choices=EDUCATION_LEVELS)
    qualification = models.CharField(max_length=200)
    institution = models.CharField(max_length=200)
    field_of_study = models.CharField(max_length=100, blank=True, null=True)
    start_year = models.PositiveIntegerField(
        validators=[MinValueValidator(1950), MaxValueValidator(2030)]
    )
    completion_year = models.PositiveIntegerField(
        validators=[MinValueValidator(1950), MaxValueValidator(2030)]
    )
    grade_gpa = models.CharField(max_length=20, blank=True, null=True)
    certificate_file = models.FileField(
        upload_to="certificates/%Y/%m/", null=True, blank=True
    )
    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_education_records",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    # System Fields
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_education_records",
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "employee_education"
        ordering = ["-completion_year", "education_level"]
        indexes = [
            models.Index(fields=["employee", "education_level"]),
            models.Index(fields=["completion_year"]),
            models.Index(fields=["is_verified"]),
        ]

    def __str__(self):
        return f"{self.employee.get_full_name()} - {self.qualification}"

    def clean(self):
        if self.completion_year < self.start_year:
            raise ValidationError("Completion year cannot be before start year")

        if self.completion_year > timezone.now().year:
            raise ValidationError("Completion year cannot be in the future")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def verify_education(self, verified_by_user):
        """Mark education record as verified"""
        self.is_verified = True
        self.verified_by = verified_by_user
        self.verified_at = timezone.now()
        self.save(update_fields=["is_verified", "verified_by", "verified_at"])


class Contract(models.Model):
    """Employee contracts"""

    CONTRACT_TYPES = [
        ("PERMANENT", "Permanent Employment"),
        ("FIXED_TERM", "Fixed Term Contract"),
        ("PROBATION", "Probation Contract"),
        ("INTERNSHIP", "Internship"),
        ("CONSULTANT", "Consultant Agreement"),
        ("PART_TIME", "Part Time"),
    ]

    CONTRACT_STATUS = [
        ("DRAFT", "Draft"),
        ("ACTIVE", "Active"),
        ("EXPIRED", "Expired"),
        ("TERMINATED", "Terminated"),
        ("RENEWED", "Renewed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="contracts"
    )
    contract_number = models.CharField(max_length=50, unique=True)
    contract_type = models.CharField(max_length=20, choices=CONTRACT_TYPES)
    status = models.CharField(max_length=20, choices=CONTRACT_STATUS, default="DRAFT")

    # Contract Dates
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    signed_date = models.DateField(null=True, blank=True)

    # Contract Details
    job_title = models.CharField(max_length=100)
    department = models.ForeignKey(
        "accounts.Department", on_delete=models.PROTECT, related_name="contracts"
    )
    reporting_manager = models.ForeignKey(
        CustomUser,
        on_delete=models.PROTECT,
        related_name="managed_contracts",
        null=True,
        blank=True,
    )

    # Salary Information
    basic_salary = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    salary_breakdown = models.JSONField(default=dict, blank=True)

    # Terms and Conditions
    terms_and_conditions = models.TextField()
    benefits = models.TextField(blank=True, null=True)
    working_hours = models.DecimalField(max_digits=4, decimal_places=2, default=8.00)
    probation_period_months = models.PositiveIntegerField(default=0)
    notice_period_days = models.PositiveIntegerField(default=30)

    # Contract Files
    contract_file = models.FileField(
        upload_to="contracts/%Y/%m/", null=True, blank=True
    )

    # System Fields
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_contracts",
    )
    terminated_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="terminated_contracts",
    )
    termination_date = models.DateTimeField(null=True, blank=True)
    termination_reason = models.TextField(blank=True, null=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "employee_contracts"
        ordering = ["-start_date"]
        indexes = [
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["contract_type"]),
            models.Index(fields=["start_date", "end_date"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.contract_number} - {self.employee.get_full_name()}"

    def clean(self):
        # Validate contract dates
        if self.end_date and self.end_date <= self.start_date:
            raise ValidationError("End date must be after start date")

        if self.signed_date and self.signed_date > timezone.now().date():
            raise ValidationError("Signed date cannot be in the future")

        # Check for overlapping contracts
        overlapping_contracts = Contract.objects.filter(
            employee=self.employee,
            status="ACTIVE",
            start_date__lte=self.end_date or date(2099, 12, 31),
            end_date__gte=self.start_date,
        ).exclude(pk=self.pk)

        if overlapping_contracts.exists():
            raise ValidationError(
                "Contract dates overlap with existing active contract: "
                f"{overlapping_contracts.first().contract_number}"
            )

    def save(self, *args, **kwargs):
        if not self.contract_number:
            self.contract_number = self.generate_contract_number()
        self.full_clean()
        super().save(*args, **kwargs)

    def generate_contract_number(self):
        """Generate unique contract number"""
        year = timezone.now().year
        prefix = f"CON{year}"

        existing_numbers = Contract.objects.filter(
            contract_number__startswith=prefix
        ).values_list("contract_number", flat=True)

        numbers = []
        for contract_num in existing_numbers:
            try:
                number = int(contract_num.replace(prefix, ""))
                numbers.append(number)
            except ValueError:
                continue

        next_number = max(numbers) + 1 if numbers else 1
        return f"{prefix}{next_number:04d}"

    def activate_contract(self):
        """Activate the contract"""
        self.status = "ACTIVE"
        self.save(update_fields=["status"])

    def terminate_contract(self, terminated_by, reason=None):
        """Terminate the contract"""
        self.status = "TERMINATED"
        self.terminated_by = terminated_by
        self.termination_date = timezone.now()
        self.termination_reason = reason
        self.is_active = False
        self.save(
            update_fields=[
                "status",
                "terminated_by",
                "termination_date",
                "termination_reason",
                "is_active",
            ]
        )

    @property
    def is_expired(self):
        """Check if contract is expired"""
        if not self.end_date:
            return False
        return timezone.now().date() > self.end_date

    @property
    def days_remaining(self):
        """Calculate days remaining in contract"""
        if not self.end_date:
            return None
        today = timezone.now().date()
        if today > self.end_date:
            return 0
        return (self.end_date - today).days

    @property
    def contract_duration_days(self):
        """Calculate total contract duration in days"""
        if not self.end_date:
            return None
        return (self.end_date - self.start_date).days
