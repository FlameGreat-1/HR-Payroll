from rest_framework import serializers
from django.contrib.auth import get_user_model
from accounts.models import Department, Role
from employees.models import EmployeeProfile
from attendance.models import MonthlyAttendanceSummary
from .models import (
    PayrollPeriod,
    Payslip,
    SalaryAdvance,
    PayrollDepartmentSummary,
    PayrollBankTransfer,
)
from decimal import Decimal

User = get_user_model()


class PayrollPeriodSerializer(serializers.ModelSerializer):
    period_name = serializers.CharField(read_only=True)
    total_epf_contribution = serializers.DecimalField(
        max_digits=15, decimal_places=2, read_only=True
    )
    role_based_summary = serializers.JSONField(read_only=True)
    department_summary = serializers.JSONField(read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.get_full_name", read_only=True
    )
    approved_by_name = serializers.CharField(
        source="approved_by.get_full_name", read_only=True
    )

    class Meta:
        model = PayrollPeriod
        fields = [
            "id",
            "year",
            "month",
            "period_name",
            "status",
            "start_date",
            "end_date",
            "processing_date",
            "cutoff_date",
            "total_employees",
            "total_working_days",
            "total_gross_salary",
            "total_deductions",
            "total_net_salary",
            "total_epf_employee",
            "total_epf_employer",
            "total_epf_contribution",
            "total_etf_contribution",
            "role_based_summary",
            "department_summary",
            "is_active",
            "created_at",
            "updated_at",
            "created_by_name",
            "approved_by_name",
            "approved_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "approved_at"]

    def validate(self, data):
        if data.get("end_date") and data.get("start_date"):
            if data["end_date"] <= data["start_date"]:
                raise serializers.ValidationError("End date must be after start date")
        return data


class PayslipSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )
    employee_code = serializers.CharField(
        source="employee.employee_code", read_only=True
    )
    employee_role = serializers.CharField(source="employee.role.name", read_only=True)
    division = serializers.CharField(source="employee.department.name", read_only=True)
    total_allowances = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    total_overtime_pay = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    total_epf_contribution = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    calculated_by_name = serializers.CharField(
        source="calculated_by.get_full_name", read_only=True
    )
    approved_by_name = serializers.CharField(
        source="approved_by.get_full_name", read_only=True
    )

    class Meta:
        model = Payslip
        fields = [
            "id",
            "reference_number",
            "status",
            "sr_no",
            "employee_name",
            "employee_code",
            "employee_role",
            "division",
            "working_days",
            "attended_days",
            "basic_salary",
            "ot_basic",
            "bonus_1",
            "bonus_2",
            "transport_allowance",
            "telephone_allowance",
            "fuel_allowance",
            "meal_allowance",
            "attendance_bonus",
            "performance_bonus",
            "interim_allowance",
            "education_allowance",
            "religious_pay",
            "friday_salary",
            "friday_overtime",
            "regular_overtime",
            "total_allowances",
            "total_overtime_pay",
            "gross_salary",
            "working_day_meals",
            "leave_days",
            "leave_deduction",
            "late_penalty",
            "advance_deduction",
            "lunch_violation_penalty",
            "epf_salary_base",
            "employee_epf_contribution",
            "employer_epf_contribution",
            "total_epf_contribution",
            "etf_contribution",
            "income_tax",
            "total_deductions",
            "net_salary",
            "fuel_per_day",
            "meal_per_day",
            "role_based_calculations",
            "attendance_breakdown",
            "penalty_breakdown",
            "created_at",
            "updated_at",
            "calculated_by_name",
            "approved_by_name",
            "approved_at",
        ]
        read_only_fields = [
            "id",
            "reference_number",
            "total_allowances",
            "total_overtime_pay",
            "total_epf_contribution",
            "created_at",
            "updated_at",
            "approved_at",
        ]


class SalaryAdvanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )
    employee_code = serializers.CharField(
        source="employee.employee_code", read_only=True
    )
    requested_by_name = serializers.CharField(
        source="requested_by.get_full_name", read_only=True
    )
    approved_by_name = serializers.CharField(
        source="approved_by.get_full_name", read_only=True
    )
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = SalaryAdvance
        fields = [
            "id",
            "reference_number",
            "advance_type",
            "status",
            "employee_name",
            "employee_code",
            "amount",
            "outstanding_amount",
            "monthly_deduction",
            "installments",
            "reason",
            "purpose_details",
            "requested_date",
            "approved_date",
            "disbursement_date",
            "completion_date",
            "employee_basic_salary",
            "max_allowed_percentage",
            "advance_count_this_year",
            "is_overdue",
            "created_at",
            "updated_at",
            "requested_by_name",
            "approved_by_name",
        ]
        read_only_fields = [
            "id",
            "reference_number",
            "outstanding_amount",
            "employee_basic_salary",
            "advance_count_this_year",
            "is_overdue",
            "created_at",
            "updated_at",
            "approved_date",
            "disbursement_date",
            "completion_date",
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Advance amount must be greater than zero"
            )
        return value

    def validate_installments(self, value):
        if value < 1 or value > 12:
            raise serializers.ValidationError("Installments must be between 1 and 12")
        return value


class PayrollDepartmentSummarySerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    period_name = serializers.CharField(
        source="payroll_period.period_name", read_only=True
    )

    class Meta:
        model = PayrollDepartmentSummary
        fields = [
            "id",
            "department_name",
            "period_name",
            "employee_count",
            "total_basic_salary",
            "total_allowances",
            "total_overtime_pay",
            "total_gross_salary",
            "total_deductions",
            "total_net_salary",
            "total_epf_employee",
            "total_epf_employer",
            "total_etf_contribution",
            "average_salary",
            "department_budget",
            "budget_utilization_percentage",
            "role_breakdown",
            "performance_metrics",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PayrollConfigurationSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.get_full_name", read_only=True
    )
    updated_by_name = serializers.CharField(
        source="updated_by.get_full_name", read_only=True
    )

    class Meta:
        model = PayrollConfiguration
        fields = [
            "id",
            "configuration_type",
            "role_name",
            "department_name",
            "configuration_key",
            "configuration_value",
            "value_type",
            "description",
            "is_active",
            "effective_from",
            "effective_to",
            "created_at",
            "updated_at",
            "created_by_name",
            "updated_by_name",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, data):
        if data.get("effective_to") and data.get("effective_from"):
            if data["effective_to"] <= data["effective_from"]:
                raise serializers.ValidationError(
                    "Effective to date must be after effective from date"
                )
        return data


class PayrollBankTransferSerializer(serializers.ModelSerializer):
    period_name = serializers.CharField(
        source="payroll_period.period_name", read_only=True
    )
    created_by_name = serializers.CharField(
        source="created_by.get_full_name", read_only=True
    )

    class Meta:
        model = PayrollBankTransfer
        fields = [
            "id",
            "batch_reference",
            "status",
            "period_name",
            "total_employees",
            "total_amount",
            "bank_file_path",
            "bank_file_format",
            "generated_at",
            "sent_at",
            "processed_at",
            "bank_response",
            "error_details",
            "created_at",
            "updated_at",
            "created_by_name",
        ]
        read_only_fields = [
            "id",
            "batch_reference",
            "total_employees",
            "total_amount",
            "bank_file_path",
            "generated_at",
            "sent_at",
            "processed_at",
            "created_at",
            "updated_at",
        ]


class PayrollAuditLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )

    class Meta:
        model = PayrollAuditLog
        fields = [
            "id",
            "action_type",
            "user_name",
            "employee_name",
            "description",
            "old_values",
            "new_values",
            "additional_data",
            "ip_address",
            "user_agent",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# Nested Serializers for Complex Operations
class PayslipCalculationRequestSerializer(serializers.Serializer):
    payslip_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text="List of payslip IDs to calculate. If empty, calculates all draft payslips.",
    )
    force_recalculation = serializers.BooleanField(
        default=False,
        help_text="Force recalculation even if payslip is already calculated",
    )


class SalaryAdvanceRequestSerializer(serializers.Serializer):
    employee_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    advance_type = serializers.ChoiceField(choices=SalaryAdvance.ADVANCE_TYPES)
    reason = serializers.CharField(max_length=500)
    installments = serializers.IntegerField(min_value=1, max_value=12)


class PayrollReportRequestSerializer(serializers.Serializer):
    REPORT_FORMATS = [("EXCEL", "Excel"), ("PDF", "PDF"), ("CSV", "CSV")]

    format_type = serializers.ChoiceField(choices=REPORT_FORMATS, default="EXCEL")
    include_summary = serializers.BooleanField(default=True)
    department_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text="Filter by specific departments",
    )
    role_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text="Filter by specific roles",
    )


class PayrollDashboardSerializer(serializers.Serializer):
    current_period = PayrollPeriodSerializer(read_only=True)
    recent_periods = PayrollPeriodSerializer(many=True, read_only=True)
    system_statistics = serializers.JSONField(read_only=True)
    pending_actions = serializers.JSONField(read_only=True)
    financial_overview = serializers.JSONField(read_only=True)



