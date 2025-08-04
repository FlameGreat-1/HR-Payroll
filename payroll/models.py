from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from accounts.models import (
    CustomUser,
    Department,
    Role,
    ActiveManager,
    SystemConfiguration,
    AuditLog,
)
from employees.models import EmployeeProfile, Contract
from attendance.models import (
    MonthlyAttendanceSummary,
    Attendance,
    AttendanceLog,
    Holiday,
    LeaveRequest,
    LeaveBalance,
    LeaveType,
    Shift,
    EmployeeShift,
    calculate_role_based_penalties,
    get_employee_work_schedule,
)
from attendance.utils import (
    TimeCalculator,
    EmployeeDataManager,
    MonthlyCalculator,
    AttendanceCalculator,
)
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
import uuid
import calendar
import logging
from .utils import (
    PayrollDataProcessor,
    PayrollValidationHelper,
    PayrollUtilityHelper,
    PayrollCalculator,
    PayrollDeductionCalculator,
    PayrollTaxCalculator,
    PayrollAdvanceCalculator,
    PayrollCacheManager,
    safe_payroll_calculation,
    log_payroll_activity,
)

logger = logging.getLogger(__name__)


class PayrollPeriod(models.Model):
    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("PROCESSING", "Processing"),
        ("COMPLETED", "Completed"),
        ("APPROVED", "Approved"),
        ("PAID", "Paid"),
        ("CANCELLED", "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    year = models.PositiveIntegerField(
        validators=[MinValueValidator(2020), MaxValueValidator(2050)]
    )
    month = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    period_name = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")

    start_date = models.DateField()
    end_date = models.DateField()
    processing_date = models.DateField()
    cutoff_date = models.DateField()

    total_employees = models.PositiveIntegerField(default=0)
    total_working_days = models.PositiveIntegerField(default=0)
    total_gross_salary = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_deductions = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_net_salary = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_epf_employee = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_epf_employer = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_etf_contribution = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )

    role_based_summary = models.JSONField(default=dict, blank=True)
    department_summary = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_payroll_periods",
    )
    approved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_payroll_periods",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "payroll_periods"
        ordering = ["-year", "-month"]
        indexes = [
            models.Index(fields=["year", "month"]),
            models.Index(fields=["status"]),
            models.Index(fields=["processing_date"]),
            models.Index(fields=["is_active"]),
        ]
        unique_together = ["year", "month"]

    def __str__(self):
        return f"Payroll {calendar.month_name[self.month]} {self.year} - {self.status}"
    
    def clean(self):
        is_valid, message = PayrollDataProcessor.validate_payroll_period(
            self.year, self.month
        )
        if not is_valid:
            raise ValidationError(message)

        if not self.start_date:
            raise ValidationError("Start date is required")
    
        if not self.end_date:
            raise ValidationError("End date is required")

        if self.end_date <= self.start_date:
            raise ValidationError("End date must be after start date")

        if self.processing_date and self.processing_date < self.end_date:
            raise ValidationError("Processing date cannot be before period end date")

    def save(self, *args, **kwargs):
        if not self.period_name:
            self.period_name = f"{calendar.month_name[self.month]} {self.year}"

        if not self.start_date:
            self.start_date = date(self.year, self.month, 1)

        if not self.end_date:
            self.end_date = date(
                self.year, self.month, calendar.monthrange(self.year, self.month)[1]
            )

        if not self.processing_date:
            self.processing_date = PayrollUtilityHelper.get_payroll_processing_date()

        if not self.cutoff_date:
            self.cutoff_date = self.end_date

        if not self.total_working_days:
            self.total_working_days = PayrollDataProcessor.get_working_days_in_month(
                self.year, self.month
            )

        self.full_clean()
        super().save(*args, **kwargs)

    def calculate_period_totals(self):
        payslips = self.payslips.filter(status__in=["CALCULATED", "APPROVED"])

        self.total_employees = payslips.count()
        self.total_gross_salary = sum(payslip.gross_salary for payslip in payslips)
        self.total_deductions = sum(payslip.total_deductions for payslip in payslips)
        self.total_net_salary = sum(payslip.net_salary for payslip in payslips)
        self.total_epf_employee = sum(
            payslip.employee_epf_contribution for payslip in payslips
        )
        self.total_epf_employer = sum(
            payslip.employer_epf_contribution for payslip in payslips
        )
        self.total_etf_contribution = sum(
            payslip.etf_contribution for payslip in payslips
        )

        self.calculate_role_based_summary()
        self.calculate_department_summary()

        self.save(
            update_fields=[
                "total_employees",
                "total_gross_salary",
                "total_deductions",
                "total_net_salary",
                "total_epf_employee",
                "total_epf_employer",
                "total_etf_contribution",
                "role_based_summary",
                "department_summary",
            ]
        )

    def calculate_role_based_summary(self):
        from django.db.models import Sum, Count, Avg

        role_summary = {}
        payslips = self.payslips.filter(status__in=["CALCULATED", "APPROVED"])

        for role in Role.objects.filter(is_active=True):
            role_payslips = payslips.filter(employee__role=role)

            if role_payslips.exists():
                role_summary[role.name] = {
                    "employee_count": role_payslips.count(),
                    "total_gross": float(
                        role_payslips.aggregate(Sum("gross_salary"))[
                            "gross_salary__sum"
                        ]
                        or 0
                    ),
                    "total_net": float(
                        role_payslips.aggregate(Sum("net_salary"))["net_salary__sum"]
                        or 0
                    ),
                    "avg_gross": float(
                        role_payslips.aggregate(Avg("gross_salary"))[
                            "gross_salary__avg"
                        ]
                        or 0
                    ),
                    "avg_net": float(
                        role_payslips.aggregate(Avg("net_salary"))["net_salary__avg"]
                        or 0
                    ),
                    "total_overtime": float(
                        role_payslips.aggregate(Sum("regular_overtime"))[
                            "regular_overtime__sum"
                        ]
                        or 0
                    ),
                    "total_penalties": float(
                        role_payslips.aggregate(Sum("late_penalty"))[
                            "late_penalty__sum"
                        ]
                        or 0
                    ),
                    "role_specific_allowances": self.get_role_specific_allowances(
                        role, role_payslips
                    ),
                }

        self.role_based_summary = role_summary

    def get_role_specific_allowances(self, role, payslips):
        role_name = role.name
        allowances = {}

        allowance_types = ["TRANSPORT", "MEAL", "FUEL", "TELEPHONE"]

        for allowance_type in allowance_types:
            setting_key = f"{role_name}_{allowance_type}_ALLOWANCE"
            if SystemConfiguration.get_setting(setting_key):
                field_name = f"{allowance_type.lower()}_allowance"
                allowances[f"{allowance_type.lower()}_allowance"] = float(
                    payslips.aggregate(Sum(field_name))[f"{field_name}__sum"] or 0
                )

        allowances["performance_bonus"] = float(
            payslips.aggregate(Sum("performance_bonus"))["performance_bonus__sum"] or 0
        )
        allowances["attendance_bonus"] = float(
            payslips.aggregate(Sum("attendance_bonus"))["attendance_bonus__sum"] or 0
        )

        return allowances

    def calculate_department_summary(self):
        from django.db.models import Sum, Count

        dept_summary = {}
        payslips = self.payslips.filter(status__in=["CALCULATED", "APPROVED"])

        for dept in Department.objects.filter(is_active=True):
            dept_payslips = payslips.filter(employee__department=dept)

            if dept_payslips.exists():
                dept_summary[dept.name] = {
                    "employee_count": dept_payslips.count(),
                    "total_gross": float(
                        dept_payslips.aggregate(Sum("gross_salary"))[
                            "gross_salary__sum"
                        ]
                        or 0
                    ),
                    "total_net": float(
                        dept_payslips.aggregate(Sum("net_salary"))["net_salary__sum"]
                        or 0
                    ),
                    "total_deductions": float(
                        dept_payslips.aggregate(Sum("total_deductions"))[
                            "total_deductions__sum"
                        ]
                        or 0
                    ),
                    "department_budget_utilization": self.calculate_department_budget_utilization(
                        dept, dept_payslips
                    ),
                }

        self.department_summary = dept_summary

    def calculate_department_budget_utilization(self, department, payslips):
        total_cost = float(
            payslips.aggregate(Sum("gross_salary"))["gross_salary__sum"] or 0
        )

        if department.budget and department.budget > 0:
            utilization = (total_cost / float(department.budget)) * 100
            return round(utilization, 2)

        dept_budget_key = f"{department.name.upper()}_DEPARTMENT_BUDGET"
        dept_budget = Decimal(SystemConfiguration.get_setting(dept_budget_key, "0.00"))

        if dept_budget > 0:
            utilization = (total_cost / float(dept_budget)) * 100
            return round(utilization, 2)

        return 0.0
    
    def mark_as_completed(self, user):
        """Mark payroll period as completed"""
        from .utils import log_payroll_activity
        
        self.status = "COMPLETED"
        self.save(update_fields=["status"])
        
        log_payroll_activity(
            user=user,
            action="PERIOD_COMPLETED",
            details={
                "period_id": str(self.id),
                "year": self.year,
                "month": self.month,
            },
        )

    @property
    def total_epf_contribution(self):
        return self.total_epf_employee + self.total_epf_employer

    def get_role_statistics(self):
        return self.role_based_summary

    def get_department_statistics(self):
        return self.department_summary






class Payslip(models.Model):
    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("CALCULATED", "Calculated"),
        ("APPROVED", "Approved"),
        ("PAID", "Paid"),
        ("CANCELLED", "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll_period = models.ForeignKey(
        PayrollPeriod, on_delete=models.CASCADE, related_name="payslips"
    )
    employee = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="payslips"
    )
    reference_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")

    sr_no = models.PositiveIntegerField(default=0)
    working_days = models.PositiveIntegerField(default=0)
    attended_days = models.PositiveIntegerField(default=0)

    basic_salary = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    ot_basic = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    bonus_1 = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    bonus_2 = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )

    transport_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    telephone_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    fuel_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    meal_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    attendance_bonus = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    performance_bonus = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    interim_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    education_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )

    religious_pay = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    friday_salary = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    friday_overtime = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    regular_overtime = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    friday_work_days = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal("0.0")
    )
    friday_ot_hours = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal("0.0")
    )
    overtime_hours = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )

    gross_salary = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    working_day_meals = models.PositiveIntegerField(default=0)
    leave_days = models.PositiveIntegerField(default=0)
    leave_deduction = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    late_penalty = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    lunch_violation_penalty = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    advance_deduction = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )

    epf_salary_base = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    employee_epf_contribution = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    employer_epf_contribution = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    etf_contribution = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    income_tax = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )

    total_deductions = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    net_salary = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    fuel_per_day = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0.00")
    )
    meal_per_day = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0.00")
    )

    monthly_summary = models.ForeignKey(
        MonthlyAttendanceSummary,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslips",
    )
    role_based_calculations = models.JSONField(default=dict, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    calculated_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calculated_payslips",
    )
    approved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_payslips",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active = ActiveManager()   
    class Meta:
        db_table = "payroll_payslips"
        ordering = [
            "-payroll_period__year",
            "-payroll_period__month",
            "employee__employee_code",
        ]
        indexes = [
            models.Index(fields=["payroll_period", "employee"]),
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["reference_number"]),
            models.Index(fields=["status"]),
            models.Index(fields=["employee"]),  
            models.Index(fields=["created_at"]),  
        ]
        unique_together = ["payroll_period", "employee"]

    def __str__(self):
        return f"Payslip {self.reference_number} - {self.employee.get_full_name()}"

    def save(self, *args, **kwargs):
        if not self.reference_number:
            self.reference_number = (
                PayrollUtilityHelper.generate_payroll_reference_number(
                    self.payroll_period.year,
                    self.payroll_period.month,
                    self.employee.employee_code,
                )
            )

        if not self.monthly_summary:
            self.monthly_summary = PayrollDataProcessor.get_employee_monthly_summary(
                self.employee, self.payroll_period.year, self.payroll_period.month
            )

        if not self.bonus_1:
            self.bonus_1 = Decimal(SystemConfiguration.get_setting("DEFAULT_BONUS_1", "1500.00"))
        if not self.bonus_2:
            self.bonus_2 = Decimal(SystemConfiguration.get_setting("DEFAULT_BONUS_2", "1000.00"))
        if not self.fuel_per_day:
            self.fuel_per_day = Decimal(SystemConfiguration.get_setting("FUEL_PER_DAY", "50.00"))
        if not self.meal_per_day:
            self.meal_per_day = Decimal(SystemConfiguration.get_setting("MEAL_PER_DAY", "350.00"))

        super().save(*args, **kwargs)

    @property
    def employee_role(self):
        return self.employee.role.name if self.employee.role else "OTHER_STAFF"

    @property
    def total_allowances(self):
        return (
            self.transport_allowance
            + self.telephone_allowance
            + self.fuel_allowance
            + self.meal_allowance
            + self.attendance_bonus
            + self.performance_bonus
            + self.interim_allowance
            + self.education_allowance
        )

    @property
    def total_overtime_pay(self):
        return self.regular_overtime + self.friday_overtime

    def calculate_payroll(self):
        self.calculate_basic_components()
        self.calculate_role_specific_allowances()
        self.calculate_overtime_pay()
        self.calculate_deductions()
        self.calculate_totals()
        self.status = "CALCULATED"
        self.save()

        log_payroll_activity(self.calculated_by, 'PAYSLIP_CALCULATED', {
            'payslip_id': str(self.id),
            'employee_code': self.employee.employee_code,
            'gross_salary': float(self.gross_salary),
            'net_salary': float(self.net_salary)
        })

    def calculate_basic_components(self):
        basic_components = PayrollCalculator.calculate_basic_salary_components(
            self.employee, self.monthly_summary
        )
        self.basic_salary = basic_components["basic_salary"]
        self.ot_basic = basic_components["basic_salary"]
        self.working_days = self.monthly_summary.working_days
        self.attended_days = self.monthly_summary.attended_days

        if self.monthly_summary.punctuality_score >= Decimal("98.0"):
            punctuality_bonus = Decimal(SystemConfiguration.get_setting("PUNCTUALITY_BONUS_AMOUNT", "500.00"))
            self.performance_bonus += punctuality_bonus

    def calculate_role_specific_allowances(self):
        allowances = PayrollCalculator.calculate_allowances(
            self.employee, self.monthly_summary
        )
        self.transport_allowance = allowances["transport_allowance"]
        self.telephone_allowance = allowances["telephone_allowance"]
        self.fuel_allowance = allowances["fuel_allowance"]
        self.meal_allowance = allowances["meal_allowance"]
        self.attendance_bonus = allowances["attendance_bonus"]
        self.performance_bonus += allowances["performance_bonus"]

    def calculate_overtime_pay(self):
        basic_components = PayrollCalculator.calculate_basic_salary_components(
            self.employee, self.monthly_summary
        )
        overtime_data = PayrollCalculator.calculate_overtime_pay(
            self.employee, self.monthly_summary, basic_components["hourly_rate"]
        )
        self.regular_overtime = overtime_data["regular_overtime_pay"]
        self.friday_overtime = overtime_data["weekend_overtime_pay"]
        self.overtime_hours = (
            overtime_data["regular_overtime_hours"]
            + overtime_data["weekend_overtime_hours"]
        )

    def calculate_deductions(self):
        basic_components = PayrollCalculator.calculate_basic_salary_components(
            self.employee, self.monthly_summary
        )
        daily_salary = basic_components["daily_salary"]

        absence_deductions = PayrollDeductionCalculator.calculate_absence_deductions(
            self.employee, self.monthly_summary, daily_salary
        )
        self.leave_deduction = absence_deductions["total_absence_deduction"]
        self.leave_days = (
            absence_deductions["absent_days"] + absence_deductions["half_days"]
        )

        penalty_data = PayrollDeductionCalculator.calculate_policy_based_penalties(
            self.employee, self.monthly_summary, daily_salary
        )
        self.late_penalty = penalty_data["total_late_penalty"]

        lunch_penalties = PayrollDeductionCalculator.calculate_lunch_violation_penalties(
            self.employee, self.payroll_period.year, self.payroll_period.month, daily_salary
        )
        self.lunch_violation_penalty = lunch_penalties["penalty_amount"]

        attendance_penalties = PayrollDeductionCalculator.integrate_attendance_penalties(
            self.employee, self.payroll_period.year, self.payroll_period.month, daily_salary
        )

        advance_data = PayrollAdvanceCalculator.calculate_advance_deduction(
            self.employee, self.payroll_period.year, self.payroll_period.month
        )
        self.advance_deduction = advance_data["total_advance_deduction"]

        epf_data = PayrollTaxCalculator.calculate_epf_contributions(
            self.gross_salary, self.basic_salary + self.bonus_1 + self.bonus_2
        )
        self.epf_salary_base = epf_data["epf_salary_base"]
        self.employee_epf_contribution = epf_data["employee_epf_contribution"]
        self.employer_epf_contribution = epf_data["employer_epf_contribution"]

        etf_data = PayrollTaxCalculator.calculate_etf_contribution(self.gross_salary)
        self.etf_contribution = etf_data["etf_contribution"]

    def calculate_totals(self):
        self.gross_salary = (
            self.basic_salary
            + self.bonus_1
            + self.bonus_2
            + self.total_allowances
            + self.total_overtime_pay
            + self.religious_pay
            + self.friday_salary
        )

        self.total_deductions = (
            self.leave_deduction
            + self.late_penalty
            + self.lunch_violation_penalty
            + self.advance_deduction
            + self.employee_epf_contribution
            + self.income_tax
        )

        self.net_salary = self.gross_salary - self.total_deductions
        self.working_day_meals = self.attended_days

    def approve(self, user):
        if self.status != "CALCULATED":
            raise ValidationError("Can only approve calculated payslips")

        self.status = "APPROVED"
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save()

        log_payroll_activity(user, 'PAYSLIP_APPROVED', {
            'payslip_id': str(self.id),
            'employee_code': self.employee.employee_code,
            'gross_salary': float(self.gross_salary),
            'net_salary': float(self.net_salary)
        })


class SalaryAdvance(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("ACTIVE", "Active"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    ]

    ADVANCE_TYPES = [
        ("SALARY", "Salary Advance"),
        ("EMERGENCY", "Emergency Advance"),
        ("PURCHASE", "Purchase Advance"),
        ("MEDICAL", "Medical Advance"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="salary_advances"
    )
    advance_type = models.CharField(
        max_length=20, choices=ADVANCE_TYPES, default="SALARY"
    )
    reference_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("100.00"))],
    )
    outstanding_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    monthly_deduction = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    installments = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(12)]
    )

    reason = models.TextField()
    purpose_details = models.JSONField(default=dict, blank=True)

    requested_date = models.DateField(auto_now_add=True)
    approved_date = models.DateField(null=True, blank=True)
    disbursement_date = models.DateField(null=True, blank=True)
    completion_date = models.DateField(null=True, blank=True)

    employee_basic_salary = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    max_allowed_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("50.00")
    )
    advance_count_this_year = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    requested_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_advances",
    )
    approved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_advances",
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "payroll_salary_advances"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["reference_number"]),
            models.Index(fields=["status"]),
            models.Index(fields=["requested_date"]),
        ]

    def __str__(self):
        return f"Advance {self.reference_number} - {self.employee.get_full_name()} - LKR {self.amount}"

    def clean(self):
        if self.amount <= 0:
            raise ValidationError("Advance amount must be greater than zero")

        advance_data = PayrollAdvanceCalculator.calculate_available_advance_amount(
            self.employee
        )

        if self.amount > advance_data["available_amount"]:
            raise ValidationError(
                f"Advance amount exceeds available limit of LKR {advance_data['available_amount']}"
            )

        max_advances_per_year = SystemConfiguration.get_int_setting(
            "MAX_ADVANCES_PER_YEAR", 10
        )
        if advance_data["advance_count_this_year"] >= max_advances_per_year:
            raise ValidationError(
                f"Maximum {max_advances_per_year} advances per year exceeded"
            )

    def save(self, *args, **kwargs):
        if not self.reference_number:
            timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
            self.reference_number = f"ADV{self.employee.employee_code}{timestamp[-6:]}"

        if not self.employee_basic_salary:
            profile = EmployeeDataManager.get_employee_profile(self.employee)
            self.employee_basic_salary = (
                profile.basic_salary if profile else Decimal("0.00")
            )

        if not self.outstanding_amount and self.status == "APPROVED":
            self.outstanding_amount = self.amount

        if not self.monthly_deduction and self.installments > 0:
            self.monthly_deduction = (self.amount / self.installments).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        advance_data = PayrollAdvanceCalculator.calculate_available_advance_amount(
            self.employee
        )
        self.advance_count_this_year = advance_data["advance_count_this_year"]

        self.full_clean()
        super().save(*args, **kwargs)

    def approve(self, user):
        if self.status != "PENDING":
            raise ValidationError("Can only approve pending advances")

        self.status = "APPROVED"
        self.approved_by = user
        self.approved_date = timezone.now().date()
        self.outstanding_amount = self.amount
        self.save()

        log_payroll_activity(
            user,
            "ADVANCE_APPROVED",
            {
                "advance_id": str(self.id),
                "employee_code": self.employee.employee_code,
                "amount": float(self.amount),
            },
        )

    def activate(self, user):
        if self.status != "APPROVED":
            raise ValidationError("Can only activate approved advances")

        self.status = "ACTIVE"
        self.disbursement_date = timezone.now().date()
        self.save()

        log_payroll_activity(
            user,
            "ADVANCE_ACTIVATED",
            {
                "advance_id": str(self.id),
                "employee_code": self.employee.employee_code,
                "disbursement_date": self.disbursement_date.isoformat(),
            },
        )

    def process_monthly_deduction(self, deduction_amount):
        if self.status != "ACTIVE":
            return Decimal("0.00")

        actual_deduction = min(deduction_amount, self.outstanding_amount)
        self.outstanding_amount -= actual_deduction

        if self.outstanding_amount <= 0:
            self.status = "COMPLETED"
            self.completion_date = timezone.now().date()

        self.save()
        return actual_deduction

    @property
    def is_overdue(self):
        if self.status != "ACTIVE":
            return False

        expected_completion_months = self.installments
        months_since_disbursement = 0

        if self.disbursement_date:
            today = timezone.now().date()
            months_since_disbursement = (
                today.year - self.disbursement_date.year
            ) * 12 + (today.month - self.disbursement_date.month)

        return months_since_disbursement > expected_completion_months


class PayslipItem(models.Model):
    ITEM_TYPES = [
        ("EARNING", "Earning"),
        ("DEDUCTION", "Deduction"),
        ("CONTRIBUTION", "Contribution"),
        ("TAX", "Tax"),
        ("BONUS", "Bonus"),
        ("ALLOWANCE", "Allowance"),
        ("PENALTY", "Penalty"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="items")
    item_type = models.CharField(max_length=20, choices=ITEM_TYPES)
    item_code = models.CharField(max_length=50)
    item_name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    rate = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    quantity = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("1.00")
    )

    is_taxable = models.BooleanField(default=True)
    is_epf_applicable = models.BooleanField(default=True)
    is_mandatory = models.BooleanField(default=False)

    calculation_basis = models.CharField(max_length=50, blank=True)
    calculation_data = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = models.Manager()

    class Meta:
        db_table = "payroll_payslip_items"
        ordering = ["item_type", "item_code"]
        indexes = [
            models.Index(fields=["payslip", "item_type"]),
            models.Index(fields=["item_code"]),
            models.Index(fields=["item_type"]),
        ]

    def __str__(self):
        return f"{self.item_name} - LKR {self.amount}"


class PayrollDepartmentSummary(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll_period = models.ForeignKey(
        PayrollPeriod, on_delete=models.CASCADE, related_name="department_summaries"
    )
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="payroll_summaries"
    )

    employee_count = models.PositiveIntegerField(default=0)
    total_basic_salary = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_allowances = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_overtime_pay = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_gross_salary = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_deductions = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_net_salary = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_epf_employee = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_epf_employer = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_etf_contribution = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )

    average_salary = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    department_budget = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    budget_utilization_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )

    role_breakdown = models.JSONField(default=dict, blank=True)
    performance_metrics = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = models.Manager()

    class Meta:
        db_table = "payroll_department_summaries"
        ordering = ["payroll_period", "department__name"]
        indexes = [
            models.Index(fields=["payroll_period", "department"]),
            models.Index(fields=["department"]),
        ]
        unique_together = ["payroll_period", "department"]

    def __str__(self):
        return f"{self.department.name} - {self.payroll_period.period_name}"

    def calculate_summary(self):
        payslips = Payslip.objects.filter(
            payroll_period=self.payroll_period,
            employee__department=self.department,
            status__in=["CALCULATED", "APPROVED"],
        )

        self.employee_count = payslips.count()
        self.total_basic_salary = sum(p.basic_salary for p in payslips)
        self.total_allowances = sum(p.total_allowances for p in payslips)
        self.total_overtime_pay = sum(p.total_overtime_pay for p in payslips)
        self.total_gross_salary = sum(p.gross_salary for p in payslips)
        self.total_deductions = sum(p.total_deductions for p in payslips)
        self.total_net_salary = sum(p.net_salary for p in payslips)
        self.total_epf_employee = sum(p.employee_epf_contribution for p in payslips)
        self.total_epf_employer = sum(p.employer_epf_contribution for p in payslips)
        self.total_etf_contribution = sum(p.etf_contribution for p in payslips)

        self.average_salary = (
            (self.total_gross_salary / self.employee_count)
            if self.employee_count > 0
            else Decimal("0.00")
        )

        if self.department.budget and self.department.budget > 0:
            self.department_budget = self.department.budget
            self.budget_utilization_percentage = (
                self.total_gross_salary / self.department_budget * 100
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            dept_budget_key = f"{self.department.name.upper()}_DEPARTMENT_BUDGET"
            self.department_budget = Decimal(
                SystemConfiguration.get_setting(dept_budget_key, "0.00")
            )
            if self.department_budget > 0:
                self.budget_utilization_percentage = (
                    self.total_gross_salary / self.department_budget * 100
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        self.calculate_role_breakdown(payslips)
        self.calculate_performance_metrics(payslips)
        self.save()

    def calculate_role_breakdown(self, payslips):
        role_data = {}

        for payslip in payslips:
            role_name = payslip.employee_role
            if role_name not in role_data:
                role_data[role_name] = {
                    "count": 0,
                    "total_gross": 0,
                    "total_net": 0,
                    "avg_gross": 0,
                    "avg_net": 0,
                }

            role_data[role_name]["count"] += 1
            role_data[role_name]["total_gross"] += float(payslip.gross_salary)
            role_data[role_name]["total_net"] += float(payslip.net_salary)

        for role_name in role_data:
            count = role_data[role_name]["count"]
            role_data[role_name]["avg_gross"] = (
                role_data[role_name]["total_gross"] / count
            )
            role_data[role_name]["avg_net"] = role_data[role_name]["total_net"] / count

        self.role_breakdown = role_data

    def calculate_performance_metrics(self, payslips):
        total_attendance_percentage = 0
        total_punctuality_score = 0
        overtime_employees = 0
        penalty_employees = 0
        lunch_violation_employees = 0
        total_lunch_penalties = Decimal("0.00")

        for payslip in payslips:
            if payslip.monthly_summary:
                total_attendance_percentage += float(
                    payslip.monthly_summary.attendance_percentage
                )
                total_punctuality_score += float(
                    payslip.monthly_summary.punctuality_score
                )

            if payslip.total_overtime_pay > 0:
                overtime_employees += 1

            if payslip.late_penalty > 0:
                penalty_employees += 1

            if payslip.lunch_violation_penalty > 0:
                lunch_violation_employees += 1
                total_lunch_penalties += payslip.lunch_violation_penalty

        count = len(payslips)
        self.performance_metrics = {
            "avg_attendance_percentage": (
                (total_attendance_percentage / count) if count > 0 else 0
            ),
            "avg_punctuality_score": (
                (total_punctuality_score / count) if count > 0 else 0
            ),
            "overtime_employees_percentage": (
                (overtime_employees / count * 100) if count > 0 else 0
            ),
            "penalty_employees_percentage": (
                (penalty_employees / count * 100) if count > 0 else 0
            ),
            "lunch_violation_percentage": (
                (lunch_violation_employees / count * 100) if count > 0 else 0
            ),
            "total_lunch_penalties": float(total_lunch_penalties),
            "department_efficiency_score": self.calculate_efficiency_score(),
            "policy_compliance_score": self.calculate_policy_compliance_score(),
        }

    def calculate_efficiency_score(self):
        if self.employee_count == 0:
            return 0

        attendance_weight = Decimal(
            SystemConfiguration.get_setting("ATTENDANCE_WEIGHT", "0.4")
        )
        punctuality_weight = Decimal(
            SystemConfiguration.get_setting("PUNCTUALITY_WEIGHT", "0.3")
        )
        productivity_weight = Decimal("1.0") - attendance_weight - punctuality_weight

        attendance_score = self.performance_metrics.get("avg_attendance_percentage", 0)
        punctuality_score = self.performance_metrics.get("avg_punctuality_score", 0)

        productivity_score = 100
        if self.performance_metrics.get("penalty_employees_percentage", 0) > 0:
            productivity_score -= self.performance_metrics[
                "penalty_employees_percentage"
            ]

        efficiency_score = (
            attendance_score * float(attendance_weight)
            + punctuality_score * float(punctuality_weight)
            + productivity_score * float(productivity_weight)
        )

        return round(efficiency_score, 2)

    def calculate_policy_compliance_score(self):
        if self.employee_count == 0:
            return 100.0

        penalty_percentage = self.performance_metrics.get(
            "penalty_employees_percentage", 0
        )
        lunch_violation_percentage = self.performance_metrics.get(
            "lunch_violation_percentage", 0
        )

        compliance_score = (
            100.0 - (penalty_percentage * 0.6) - (lunch_violation_percentage * 0.4)
        )
        return max(0.0, round(compliance_score, 2))


class PayrollBankTransfer(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("GENERATED", "Generated"),
        ("SENT", "Sent to Bank"),
        ("PROCESSED", "Processed by Bank"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll_period = models.ForeignKey(
        PayrollPeriod, on_delete=models.CASCADE, related_name="bank_transfers"
    )
    batch_reference = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")

    total_employees = models.PositiveIntegerField(default=0)
    total_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )

    bank_file_path = models.CharField(max_length=500, blank=True)
    bank_file_format = models.CharField(max_length=20, default="CSV")

    generated_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    bank_response = models.JSONField(default=dict, blank=True)
    error_details = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_bank_transfers",
    )

    objects = models.Manager()

    class Meta:
        db_table = "payroll_bank_transfers"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["payroll_period"]),
            models.Index(fields=["batch_reference"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Bank Transfer {self.batch_reference} - LKR {self.total_amount}"

    def save(self, *args, **kwargs):
        if not self.batch_reference:
            timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
            self.batch_reference = f"BT{self.payroll_period.year}{self.payroll_period.month:02d}{timestamp[-6:]}"

        if not self.bank_file_format:
            self.bank_file_format = SystemConfiguration.get_setting(
                "BANK_FILE_FORMAT", "CSV"
            )

        super().save(*args, **kwargs)

    def generate_bank_file(self):
        payslips = self.payroll_period.payslips.filter(
            status="APPROVED", net_salary__gt=0
        )

        validation_errors = []
        bank_data = []

        for payslip in payslips:
            profile = EmployeeDataManager.get_employee_profile(payslip.employee)

            if not profile:
                validation_errors.append(
                    f"{payslip.employee.employee_code}: Missing employee profile"
                )
                continue

            if not profile.bank_account_number:
                validation_errors.append(
                    f"{payslip.employee.employee_code}: No bank account number"
                )
                continue

            if not profile.bank_code:
                validation_errors.append(
                    f"{payslip.employee.employee_code}: No bank code"
                )
                continue

            bank_data.append(
                {
                    "employee_code": payslip.employee.employee_code,
                    "employee_name": payslip.employee.get_full_name(),
                    "account_number": profile.bank_account_number,
                    "bank_code": profile.bank_code
                    or SystemConfiguration.get_setting("DEFAULT_BANK_CODE", "DEFAULT"),
                    "branch_code": profile.bank_branch_code
                    or SystemConfiguration.get_setting(
                        "DEFAULT_BRANCH_CODE", "DEFAULT"
                    ),
                    "amount": float(payslip.net_salary),
                    "reference": payslip.reference_number,
                }
            )

        if validation_errors:
            self.mark_as_failed(f"Validation errors: {'; '.join(validation_errors)}")
            return None

        self.total_employees = len(bank_data)
        self.total_amount = sum(Decimal(str(item["amount"])) for item in bank_data)

        try:
            file_path = self.create_bank_file(bank_data)
            self.mark_as_generated(file_path)
            return file_path
        except Exception as e:
            self.mark_as_failed(str(e))
            return None

    def create_bank_file(self, bank_data):
        file_format = SystemConfiguration.get_setting("BANK_FILE_FORMAT", "CSV")
        bank_file_encoding = SystemConfiguration.get_setting(
            "BANK_FILE_ENCODING", "utf-8"
        )

        if file_format.upper() == "CSV":
            return self.create_csv_file(bank_data, bank_file_encoding)
        elif file_format.upper() == "XML":
            return self.create_xml_file(bank_data, bank_file_encoding)
        else:
            raise ValidationError(f"Unsupported bank file format: {file_format}")

    def create_csv_file(self, bank_data, encoding):
        import csv
        import os
        from django.conf import settings

        file_name = f"{self.batch_reference}.csv"
        file_path = os.path.join(
            settings.MEDIA_ROOT, "payroll", "bank_transfers", file_name
        )

        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        with open(file_path, "w", newline="", encoding=encoding) as csvfile:
            fieldnames = [
                "employee_code",
                "employee_name",
                "account_number",
                "bank_code",
                "branch_code",
                "amount",
                "reference",
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            writer.writeheader()
            for row in bank_data:
                writer.writerow(row)

        return file_path

    def create_xml_file(self, bank_data, encoding):
        import xml.etree.ElementTree as ET
        import os
        from django.conf import settings

        file_name = f"{self.batch_reference}.xml"
        file_path = os.path.join(
            settings.MEDIA_ROOT, "payroll", "bank_transfers", file_name
        )

        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        root = ET.Element("BankTransfer")
        root.set("batch_reference", self.batch_reference)
        root.set("total_amount", str(self.total_amount))
        root.set("total_employees", str(self.total_employees))

        for item in bank_data:
            employee_elem = ET.SubElement(root, "Employee")
            for key, value in item.items():
                elem = ET.SubElement(employee_elem, key)
                elem.text = str(value)

        tree = ET.ElementTree(root)
        tree.write(file_path, encoding=encoding, xml_declaration=True)

        return file_path

    def mark_as_generated(self, file_path):
        self.status = "GENERATED"
        self.bank_file_path = file_path
        self.generated_at = timezone.now()
        self.save()

        log_payroll_activity(
            self.created_by,
            "BANK_TRANSFER_GENERATED",
            {
                "transfer_id": str(self.id),
                "batch_reference": self.batch_reference,
                "total_amount": float(self.total_amount),
                "total_employees": self.total_employees,
            },
        )

    def mark_as_failed(self, error_message):
        self.status = "FAILED"
        self.error_details = error_message
        self.save()

        log_payroll_activity(
            self.created_by,
            "BANK_TRANSFER_FAILED",
            {
                "transfer_id": str(self.id),
                "batch_reference": self.batch_reference,
                "error": error_message,
            },
        )


class PayrollManager(models.Manager):
    def get_active_periods(self):
        return self.filter(
            is_active=True, status__in=["DRAFT", "PROCESSING", "COMPLETED", "APPROVED"]
        )

    def get_current_period(self):
        from .utils import get_current_payroll_period

        current_year, current_month = get_current_payroll_period()
        try:
            return self.get(year=current_year, month=current_month)
        except self.model.DoesNotExist:
            return None

    def create_period(self, year, month, user):
        if self.filter(year=year, month=month).exists():
            raise ValidationError(
                f"Payroll period for {year}-{month:02d} already exists"
            )

        active_employees = CustomUser.active.filter(status="ACTIVE")
        missing_summaries = []

        for employee in active_employees:
            try:
                MonthlyAttendanceSummary.generate_for_employee_month(
                    employee, year, month
                )
            except Exception as e:
                missing_summaries.append(f"{employee.employee_code}: {str(e)}")

        if missing_summaries:
            logger.warning(
                f"Missing attendance summaries: {'; '.join(missing_summaries)}"
            )

        period = self.create(
            year=year,
            month=month,
            created_by=user,
            total_working_days=PayrollDataProcessor.get_working_days_in_month(
                year, month
            ),
        )

        log_payroll_activity(
            user,
            "PERIOD_CREATED",
            {
                "period_id": str(period.id),
                "year": year,
                "month": month,
                "total_employees": active_employees.count(),
                "missing_summaries_count": len(missing_summaries),
            },
        )

        return period


class PayslipManager(models.Manager):
    def get_employee_payslips(self, employee, year=None, month=None):
        queryset = self.filter(employee=employee)

        if year:
            queryset = queryset.filter(payroll_period__year=year)
        if month:
            queryset = queryset.filter(payroll_period__month=month)

        return queryset.order_by("-payroll_period__year", "-payroll_period__month")

    def get_department_payslips(self, department, payroll_period):
        return self.filter(
            employee__department=department, payroll_period=payroll_period
        )

    def get_role_payslips(self, role, payroll_period):
        return self.filter(employee__role=role, payroll_period=payroll_period)

    def bulk_calculate(self, payroll_period, employees=None):
        if employees is None:
            employees = CustomUser.active.filter(status="ACTIVE")

        calculated_payslips = []
        failed_employees = []

        for employee in employees:
            is_valid, message = PayrollValidationHelper.validate_employee_for_payroll(
                employee, payroll_period.year, payroll_period.month
            )

            if not is_valid:
                failed_employees.append(f"{employee.employee_code}: {message}")
                continue

            try:
                payslip, created = self.get_or_create(
                    payroll_period=payroll_period,
                    employee=employee,
                    defaults={"calculated_by": payroll_period.created_by},
                )

                if created or payslip.status == "DRAFT":
                    payslip.calculate_payroll()
                    calculated_payslips.append(payslip)

            except Exception as e:
                logger.error(
                    f"Error calculating payroll for {employee.employee_code}: {str(e)}"
                )
                failed_employees.append(f"{employee.employee_code}: {str(e)}")
                continue

        if failed_employees:
            logger.warning(
                f"Failed to calculate payroll for: {'; '.join(failed_employees)}"
            )

        log_payroll_activity(
            payroll_period.created_by,
            "BULK_PAYROLL_CALCULATED",
            {
                "period_id": str(payroll_period.id),
                "total_employees": len(employees),
                "calculated_count": len(calculated_payslips),
                "failed_count": len(failed_employees),
            },
        )

        return calculated_payslips

    def bulk_approve(self, payroll_period, user, employees=None):
        if employees is None:
            payslips = self.filter(payroll_period=payroll_period, status="CALCULATED")
        else:
            payslips = self.filter(
                payroll_period=payroll_period,
                employee__in=employees,
                status="CALCULATED",
            )

        approved_count = 0
        failed_approvals = []

        for payslip in payslips:
            try:
                payslip.approve(user)
                approved_count += 1
            except Exception as e:
                failed_approvals.append(f"{payslip.employee.employee_code}: {str(e)}")

        log_payroll_activity(
            user,
            "BULK_PAYROLL_APPROVED",
            {
                "period_id": str(payroll_period.id),
                "approved_count": approved_count,
                "failed_count": len(failed_approvals),
            },
        )

        return approved_count, failed_approvals


class SalaryAdvanceManager(models.Manager):
    def get_employee_advances(self, employee, year=None):
        queryset = self.filter(employee=employee)

        if year:
            queryset = queryset.filter(requested_date__year=year)

        return queryset.order_by("-requested_date")

    def get_active_advances(self, employee):
        return self.filter(employee=employee, status="ACTIVE", outstanding_amount__gt=0)

    def get_pending_approvals(self):
        return self.filter(status="PENDING").order_by("requested_date")

    def get_overdue_advances(self):
        active_advances = self.filter(status="ACTIVE", outstanding_amount__gt=0)
        overdue_advances = []

        for advance in active_advances:
            if advance.is_overdue:
                overdue_advances.append(advance)

        return overdue_advances

    def bulk_approve(self, advance_ids, user):
        advances = self.filter(id__in=advance_ids, status="PENDING")
        approved_count = 0
        failed_approvals = []

        for advance in advances:
            try:
                advance.approve(user)
                approved_count += 1
            except Exception as e:
                failed_approvals.append(f"{advance.employee.employee_code}: {str(e)}")

        log_payroll_activity(
            user,
            "BULK_ADVANCES_APPROVED",
            {
                "approved_count": approved_count,
                "failed_count": len(failed_approvals),
                "advance_ids": [str(aid) for aid in advance_ids],
            },
        )

        return approved_count, failed_approvals


PayrollPeriod.add_to_class("objects", PayrollManager())
Payslip.add_to_class("objects", PayslipManager())
SalaryAdvance.add_to_class("objects", SalaryAdvanceManager())


def create_payroll_permissions():
    from django.contrib.contenttypes.models import ContentType
    from django.contrib.auth.models import Permission

    payroll_content_type, created = ContentType.objects.get_or_create(
        app_label="payroll", model="payrollperiod"
    )

    permissions = [
        ("view_payroll_dashboard", "Can view payroll dashboard"),
        ("process_payroll", "Can process payroll"),
        ("approve_payroll", "Can approve payroll"),
        ("export_payroll", "Can export payroll data"),
        ("manage_salary_advances", "Can manage salary advances"),
        ("view_payroll_reports", "Can view payroll reports"),
        ("configure_payroll", "Can configure payroll settings"),
        ("bulk_approve_payroll", "Can bulk approve payroll"),
        ("generate_bank_transfers", "Can generate bank transfers"),
        ("view_department_summaries", "Can view department summaries"),
    ]

    created_permissions = []
    for codename, name in permissions:
        permission, created = Permission.objects.get_or_create(
            codename=codename,
            content_type=payroll_content_type,
            defaults={"name": name},
        )
        if created:
            created_permissions.append(permission)

    return created_permissions


def setup_payroll_system_configurations():
    payroll_settings = {
        "MANAGER_TRANSPORT_ALLOWANCE": (
            "4000.00",
            "PAYROLL",
            "Transport allowance for managers",
        ),
        "CASHIER_TRANSPORT_ALLOWANCE": (
            "2500.00",
            "PAYROLL",
            "Transport allowance for cashiers",
        ),
        "SALESMAN_TRANSPORT_ALLOWANCE": (
            "2500.00",
            "PAYROLL",
            "Transport allowance for salesmen",
        ),
        "OTHER_STAFF_TRANSPORT_ALLOWANCE": (
            "2000.00",
            "PAYROLL",
            "Transport allowance for other staff",
        ),
        "CLEANER_TRANSPORT_ALLOWANCE": (
            "1500.00",
            "PAYROLL",
            "Transport allowance for cleaners",
        ),
        "DRIVER_TRANSPORT_ALLOWANCE": (
            "3000.00",
            "PAYROLL",
            "Transport allowance for drivers",
        ),
        "ASSISTANT_TRANSPORT_ALLOWANCE": (
            "2000.00",
            "PAYROLL",
            "Transport allowance for assistants",
        ),
        "STOREKEEPER_TRANSPORT_ALLOWANCE": (
            "2000.00",
            "PAYROLL",
            "Transport allowance for storekeepers",
        ),
        "OFFICE_WORKER_TRANSPORT_ALLOWANCE": (
            "2000.00",
            "PAYROLL",
            "Transport allowance for office workers",
        ),
        "MANAGER_MEAL_ALLOWANCE": ("2000.00", "PAYROLL", "Meal allowance for managers"),
        "CASHIER_MEAL_ALLOWANCE": ("1500.00", "PAYROLL", "Meal allowance for cashiers"),
        "SALESMAN_MEAL_ALLOWANCE": (
            "1500.00",
            "PAYROLL",
            "Meal allowance for salesmen",
        ),
        "OTHER_STAFF_MEAL_ALLOWANCE": (
            "1500.00",
            "PAYROLL",
            "Meal allowance for other staff",
        ),
        "MANAGER_TELEPHONE_ALLOWANCE": (
            "1000.00",
            "PAYROLL",
            "Telephone allowance for managers",
        ),
        "CASHIER_TELEPHONE_ALLOWANCE": (
            "500.00",
            "PAYROLL",
            "Telephone allowance for cashiers",
        ),
        "SALESMAN_TELEPHONE_ALLOWANCE": (
            "750.00",
            "PAYROLL",
            "Telephone allowance for salesmen",
        ),
        "SALESMAN_FUEL_ALLOWANCE": (
            "2000.00",
            "PAYROLL",
            "Fuel allowance for salesmen",
        ),
        "DRIVER_FUEL_ALLOWANCE": ("3000.00", "PAYROLL", "Fuel allowance for drivers"),
        "DEFAULT_TRANSPORT_ALLOWANCE": (
            "2000.00",
            "PAYROLL",
            "Default transport allowance",
        ),
        "DEFAULT_MEAL_ALLOWANCE": ("1500.00", "PAYROLL", "Default meal allowance"),
        "DEFAULT_TELEPHONE_ALLOWANCE": (
            "500.00",
            "PAYROLL",
            "Default telephone allowance",
        ),
        "DEFAULT_FUEL_ALLOWANCE": ("0.00", "PAYROLL", "Default fuel allowance"),
        "ATTENDANCE_BONUS_THRESHOLD": (
            "95.0",
            "PAYROLL",
            "Minimum attendance percentage for bonus",
        ),
        "ATTENDANCE_BONUS_AMOUNT": ("1000.00", "PAYROLL", "Attendance bonus amount"),
        "PUNCTUALITY_BONUS_THRESHOLD": (
            "98.0",
            "PAYROLL",
            "Minimum punctuality score for bonus",
        ),
        "PUNCTUALITY_BONUS_AMOUNT": ("500.00", "PAYROLL", "Punctuality bonus amount"),
        "EPF_EMPLOYEE_RATE": ("8.0", "PAYROLL", "Employee EPF contribution rate"),
        "EPF_EMPLOYER_RATE": ("12.0", "PAYROLL", "Employer EPF contribution rate"),
        "ETF_RATE": ("3.0", "PAYROLL", "ETF contribution rate"),
        "DEFAULT_BONUS_1": ("1500.00", "PAYROLL", "Default bonus 1 amount"),
        "DEFAULT_BONUS_2": ("1000.00", "PAYROLL", "Default bonus 2 amount"),
        "FUEL_PER_DAY": ("50.00", "PAYROLL", "Fuel allowance per day"),
        "MEAL_PER_DAY": ("350.00", "PAYROLL", "Meal allowance per day"),
        "BANK_FILE_FORMAT": ("CSV", "PAYROLL", "Bank transfer file format"),
        "BANK_FILE_ENCODING": ("utf-8", "PAYROLL", "Bank transfer file encoding"),
        "DEFAULT_BANK_CODE": ("DEFAULT", "PAYROLL", "Default bank code"),
        "DEFAULT_BRANCH_CODE": ("DEFAULT", "PAYROLL", "Default branch code"),
        "MAX_ADVANCES_PER_YEAR": ("10", "PAYROLL", "Maximum salary advances per year"),
        "SALARY_ADVANCE_MAX_PERCENTAGE": (
            "50.0",
            "PAYROLL",
            "Maximum salary advance percentage",
        ),
        "ATTENDANCE_WEIGHT": (
            "0.4",
            "PAYROLL",
            "Attendance weight in efficiency calculation",
        ),
        "PUNCTUALITY_WEIGHT": (
            "0.3",
            "PAYROLL",
            "Punctuality weight in efficiency calculation",
        ),
        "PAYROLL_PROCESSING_DAY": (
            "25",
            "PAYROLL",
            "Day of month for payroll processing",
        ),
        "MAX_PAYROLL_AMOUNT": ("1000000.00", "PAYROLL", "Maximum payroll amount"),
        "PAYROLL_ROUNDING_PRECISION": (
            "2",
            "PAYROLL",
            "Payroll amount rounding precision",
        ),
    }

    created_settings = []
    for key, (value, category, description) in payroll_settings.items():
        setting, created = SystemConfiguration.objects.get_or_create(
            key=key,
            defaults={
                "value": value,
                "category": category,
                "description": description,
                "setting_type": (
                    "DECIMAL"
                    if "." in value and value.replace(".", "").isdigit()
                    else "TEXT"
                ),
            },
        )
        if created:
            created_settings.append(setting)

    return created_settings


def initialize_payroll_system():
    try:
        created_permissions = create_payroll_permissions()
        created_settings = setup_payroll_system_configurations()

        logger.info(f"Payroll system initialized successfully:")
        logger.info(f"- Created {len(created_permissions)} new permissions")
        logger.info(f"- Created {len(created_settings)} new system configurations")

        setup_default_role_permissions()

        return {
            "permissions_created": len(created_permissions),
            "settings_created": len(created_settings),
            "status": "success",
        }
    except Exception as e:
        logger.error(f"Error initializing payroll system: {str(e)}")
        return {"status": "error", "error": str(e)}


def setup_default_role_permissions():
    try:
        from django.contrib.auth.models import Permission

        role_permissions = {
            "SUPER_ADMIN": [
                "view_payroll_dashboard",
                "process_payroll",
                "approve_payroll",
                "export_payroll",
                "manage_salary_advances",
                "view_payroll_reports",
                "configure_payroll",
                "bulk_approve_payroll",
                "generate_bank_transfers",
                "view_department_summaries",
            ],
            "MANAGER": [
                "view_payroll_dashboard",
                "view_payroll_reports",
                "manage_salary_advances",
                "view_department_summaries",
            ],
            "CASHIER": [
                "view_payroll_dashboard",
            ],
            "OTHER_STAFF": [],
        }

        for role_name, permission_codenames in role_permissions.items():
            try:
                role = Role.objects.get(name=role_name)
                permissions = Permission.objects.filter(
                    codename__in=permission_codenames
                )
                role.permissions.add(*permissions)
                logger.info(
                    f"Added {len(permissions)} payroll permissions to {role_name}"
                )
            except Role.DoesNotExist:
                logger.warning(
                    f"Role {role_name} not found, skipping permission assignment"
                )

    except Exception as e:
        logger.error(f"Error setting up default role permissions: {str(e)}")


def validate_payroll_system_integrity():
    validation_results = {
        "status": "success",
        "errors": [],
        "warnings": [],
        "checks_performed": [],
    }

    try:
        validation_results["checks_performed"].append(
            "Checking SystemConfiguration settings"
        )
        required_settings = [
            "NET_WORKING_HOURS",
            "OVERTIME_RATE_MULTIPLIER",
            "EPF_EMPLOYEE_RATE",
            "EPF_EMPLOYER_RATE",
            "ETF_RATE",
            "DEFAULT_TRANSPORT_ALLOWANCE",
            "DEFAULT_MEAL_ALLOWANCE",
            "ATTENDANCE_BONUS_THRESHOLD",
            "PUNCTUALITY_BONUS_THRESHOLD",
        ]

        for setting in required_settings:
            if not SystemConfiguration.get_setting(setting):
                validation_results["errors"].append(
                    f"Missing required setting: {setting}"
                )

        validation_results["checks_performed"].append("Checking Role configurations")
        active_roles = Role.objects.filter(is_active=True)
        for role in active_roles:
            transport_key = f"{role.name}_TRANSPORT_ALLOWANCE"
            if not SystemConfiguration.get_setting(transport_key):
                validation_results["warnings"].append(
                    f"No transport allowance configured for role: {role.name}"
                )

        validation_results["checks_performed"].append(
            "Checking Department configurations"
        )
        active_departments = Department.objects.filter(is_active=True)
        for dept in active_departments:
            if not dept.budget:
                budget_key = f"{dept.name.upper()}_DEPARTMENT_BUDGET"
                if not SystemConfiguration.get_setting(budget_key):
                    validation_results["warnings"].append(
                        f"No budget configured for department: {dept.name}"
                    )

        validation_results["checks_performed"].append("Checking Employee profiles")
        active_employees = CustomUser.active.filter(status="ACTIVE")
        employees_without_profiles = []

        for employee in active_employees:
            profile = EmployeeDataManager.get_employee_profile(employee)
            if not profile:
                employees_without_profiles.append(employee.employee_code)

        if employees_without_profiles:
            validation_results["warnings"].append(
                f"Employees without profiles: {', '.join(employees_without_profiles)}"
            )

        validation_results["checks_performed"].append(
            "Checking Attendance system integration"
        )
        try:
            from attendance.models import MonthlyAttendanceSummary
            from attendance.utils import TimeCalculator

            validation_results["checks_performed"].append(
                "Attendance system integration: OK"
            )
        except ImportError as e:
            validation_results["errors"].append(
                f"Attendance system integration error: {str(e)}"
            )

        if validation_results["errors"]:
            validation_results["status"] = "error"
        elif validation_results["warnings"]:
            validation_results["status"] = "warning"

    except Exception as e:
        validation_results["status"] = "error"
        validation_results["errors"].append(f"System validation error: {str(e)}")

    return validation_results

def calculate_employee_year_to_date(employee_id, year):
    try:
        employee = CustomUser.objects.get(id=employee_id)
        payslips = Payslip.objects.filter(
            employee=employee,
            payroll_period__year=year,
            status__in=["CALCULATED", "APPROVED", "PAID"],
        )

        ytd_data = {
            "employee_code": employee.employee_code,
            "employee_name": employee.get_full_name(),
            "year": year,
            "total_months": payslips.count(),
            "total_gross_salary": sum(p.gross_salary for p in payslips),
            "total_basic_salary": sum(p.basic_salary for p in payslips),
            "total_allowances": sum(p.total_allowances for p in payslips),
            "total_overtime_pay": sum(p.total_overtime_pay for p in payslips),
            "total_deductions": sum(p.total_deductions for p in payslips),
            "total_net_salary": sum(p.net_salary for p in payslips),
            "total_epf_employee": sum(p.employee_epf_contribution for p in payslips),
            "total_epf_employer": sum(p.employer_epf_contribution for p in payslips),
            "total_etf": sum(p.etf_contribution for p in payslips),
            "total_late_penalties": sum(p.late_penalty for p in payslips),
            "total_lunch_violations": sum(p.lunch_violation_penalty for p in payslips),
            "total_advance_deductions": sum(p.advance_deduction for p in payslips),
            "average_monthly_gross": 0,
            "average_monthly_net": 0,
        }

        if ytd_data["total_months"] > 0:
            ytd_data["average_monthly_gross"] = (
                ytd_data["total_gross_salary"] / ytd_data["total_months"]
            )
            ytd_data["average_monthly_net"] = (
                ytd_data["total_net_salary"] / ytd_data["total_months"]
            )

        for key in ytd_data:
            if isinstance(ytd_data[key], Decimal):
                ytd_data[key] = float(ytd_data[key])

        return {"status": "success", "ytd_data": ytd_data}

    except Exception as e:
        logger.error(f"Error calculating YTD data: {str(e)}")
        return {"status": "error", "error": str(e)}


def generate_tax_report(year, report_type="annual"):
    try:
        if report_type == "annual":
            payslips = Payslip.objects.filter(
                payroll_period__year=year, status__in=["APPROVED", "PAID"]
            )
        else:
            return {"status": "error", "error": "Unsupported report type"}

        tax_data = {
            "report_type": report_type,
            "year": year,
            "total_employees": len(set(p.employee_id for p in payslips)),
            "total_gross_salary": sum(p.gross_salary for p in payslips),
            "total_epf_employee": sum(p.employee_epf_contribution for p in payslips),
            "total_epf_employer": sum(p.employer_epf_contribution for p in payslips),
            "total_etf": sum(p.etf_contribution for p in payslips),
            "total_income_tax": sum(p.income_tax for p in payslips),
            "employee_breakdown": [],
        }

        employee_tax_data = {}
        for payslip in payslips:
            emp_id = payslip.employee_id
            if emp_id not in employee_tax_data:
                employee_tax_data[emp_id] = {
                    "employee_code": payslip.employee.employee_code,
                    "employee_name": payslip.employee.get_full_name(),
                    "annual_gross": Decimal("0.00"),
                    "annual_epf_employee": Decimal("0.00"),
                    "annual_epf_employer": Decimal("0.00"),
                    "annual_etf": Decimal("0.00"),
                    "annual_income_tax": Decimal("0.00"),
                }

            employee_tax_data[emp_id]["annual_gross"] += payslip.gross_salary
            employee_tax_data[emp_id][
                "annual_epf_employee"
            ] += payslip.employee_epf_contribution
            employee_tax_data[emp_id][
                "annual_epf_employer"
            ] += payslip.employer_epf_contribution
            employee_tax_data[emp_id]["annual_etf"] += payslip.etf_contribution
            employee_tax_data[emp_id]["annual_income_tax"] += payslip.income_tax

        for emp_data in employee_tax_data.values():
            for key in emp_data:
                if isinstance(emp_data[key], Decimal):
                    emp_data[key] = float(emp_data[key])
            tax_data["employee_breakdown"].append(emp_data)

        for key in tax_data:
            if isinstance(tax_data[key], Decimal):
                tax_data[key] = float(tax_data[key])

        return {"status": "success", "tax_report": tax_data}

    except Exception as e:
        logger.error(f"Error generating tax report: {str(e)}")
        return {"status": "error", "error": str(e)}


def process_monthly_advance_deductions(year, month):
    try:
        active_advances = SalaryAdvance.objects.filter(
            status="ACTIVE", outstanding_amount__gt=0
        )

        processed_advances = []
        total_deductions = Decimal("0.00")

        for advance in active_advances:
            try:
                payslip = Payslip.objects.get(
                    employee=advance.employee,
                    payroll_period__year=year,
                    payroll_period__month=month,
                    status__in=["CALCULATED", "APPROVED"],
                )

                deduction_amount = advance.process_monthly_deduction(
                    advance.monthly_deduction
                )
                total_deductions += deduction_amount

                processed_advances.append(
                    {
                        "advance_id": str(advance.id),
                        "employee_code": advance.employee.employee_code,
                        "deduction_amount": float(deduction_amount),
                        "remaining_balance": float(advance.outstanding_amount),
                        "status": advance.status,
                    }
                )

            except Payslip.DoesNotExist:
                logger.warning(
                    f"No payslip found for advance {advance.id} in {year}-{month}"
                )
                continue
            except Exception as e:
                logger.error(f"Error processing advance {advance.id}: {str(e)}")
                continue

        return {
            "status": "success",
            "processed_count": len(processed_advances),
            "total_deductions": float(total_deductions),
            "advances": processed_advances,
        }

    except Exception as e:
        logger.error(f"Error processing monthly advance deductions: {str(e)}")
        return {"status": "error", "error": str(e)}


def validate_employee_payroll_eligibility(employee_id, year, month):
    try:
        employee = CustomUser.objects.get(id=employee_id)

        validation_result = {
            "employee_code": employee.employee_code,
            "employee_name": employee.get_full_name(),
            "is_eligible": True,
            "issues": [],
            "warnings": [],
        }

        is_valid, message = PayrollValidationHelper.validate_employee_for_payroll(
            employee, year, month
        )

        if not is_valid:
            validation_result["is_eligible"] = False
            validation_result["issues"].append(message)

        profile = EmployeeDataManager.get_employee_profile(employee)
        if not profile:
            validation_result["issues"].append("Employee profile not found")
            validation_result["is_eligible"] = False
        else:
            if not profile.basic_salary or profile.basic_salary <= 0:
                validation_result["issues"].append("Invalid basic salary")
                validation_result["is_eligible"] = False

            if not profile.bank_account_number:
                validation_result["warnings"].append("No bank account number")

        monthly_summary = PayrollDataProcessor.get_employee_monthly_summary(
            employee, year, month
        )

        if not monthly_summary:
            validation_result["warnings"].append("No attendance summary found")
        else:
            if monthly_summary.working_days == 0:
                validation_result["warnings"].append("No working days recorded")

            if monthly_summary.attended_days == 0:
                validation_result["warnings"].append("No attendance recorded")

        active_advances = SalaryAdvance.objects.filter(
            employee=employee, status="ACTIVE", outstanding_amount__gt=0
        ).count()

        if active_advances > 0:
            validation_result["warnings"].append(
                f"{active_advances} active salary advances"
            )

        return {"status": "success", "validation": validation_result}

    except Exception as e:
        logger.error(f"Error validating employee eligibility: {str(e)}")
        return {"status": "error", "error": str(e)}


def generate_payroll_comparison_report(year1, month1, year2, month2):
    try:
        period1 = PayrollPeriod.objects.get(year=year1, month=month1)
        period2 = PayrollPeriod.objects.get(year=year2, month=month2)

        comparison_data = {
            "period1": f"{year1}-{month1:02d}",
            "period2": f"{year2}-{month2:02d}",
            "period1_data": {
                "total_employees": period1.total_employees,
                "total_gross": float(period1.total_gross_salary),
                "total_net": float(period1.total_net_salary),
                "total_deductions": float(period1.total_deductions),
            },
            "period2_data": {
                "total_employees": period2.total_employees,
                "total_gross": float(period2.total_gross_salary),
                "total_net": float(period2.total_net_salary),
                "total_deductions": float(period2.total_deductions),
            },
            "changes": {},
        }

        comparison_data["changes"]["employee_change"] = (
            period2.total_employees - period1.total_employees
        )
        comparison_data["changes"]["gross_change"] = float(
            period2.total_gross_salary - period1.total_gross_salary
        )
        comparison_data["changes"]["net_change"] = float(
            period2.total_net_salary - period1.total_net_salary
        )
        comparison_data["changes"]["deduction_change"] = float(
            period2.total_deductions - period1.total_deductions
        )

        if period1.total_gross_salary > 0:
            comparison_data["changes"]["gross_percentage"] = float(
                (period2.total_gross_salary - period1.total_gross_salary)
                / period1.total_gross_salary
                * 100
            )

        if period1.total_net_salary > 0:
            comparison_data["changes"]["net_percentage"] = float(
                (period2.total_net_salary - period1.total_net_salary)
                / period1.total_net_salary
                * 100
            )

        return {"status": "success", "comparison": comparison_data}

    except Exception as e:
        logger.error(f"Error generating comparison report: {str(e)}")
        return {"status": "error", "error": str(e)}

class PayrollSystemMeta:
    VERSION = "1.0.0"
    LAST_UPDATED = "2024-01-01"
    SUPPORTED_FEATURES = [
        "policy_based_penalties",
        "role_based_allowances",
        "department_summaries",
        "bank_transfers",
        "salary_advances",
        "attendance_integration",
        "system_configuration",
        "audit_logging",
    ]

    @classmethod
    def get_system_info(cls):
        return {
            "version": cls.VERSION,
            "last_updated": cls.LAST_UPDATED,
            "features": cls.SUPPORTED_FEATURES,
            "models": [
                "PayrollPeriod",
                "Payslip",
                "SalaryAdvance",
                "PayslipItem",
                "PayrollDepartmentSummary",
                "PayrollBankTransfer",
            ],
        }


def initialize_payroll_system():
    try:
        created_settings = setup_payroll_system_configurations()

        logger.info(f"Payroll system initialized successfully:")
        logger.info(f"- Created {len(created_settings)} new system configurations")

        return {"settings_created": len(created_settings), "status": "success"}
    except Exception as e:
        logger.error(f"Error initializing payroll system: {str(e)}")
        return {"status": "error", "error": str(e)}
