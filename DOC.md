from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.db.models import Sum, Count, Avg
from django.contrib.admin import SimpleListFilter
from django.utils import timezone
from django.http import HttpResponse
from django.shortcuts import redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from accounts.models import CustomUser, Department, Role
from .models import (
    PayrollPeriod,
    Payslip,
    SalaryAdvance,
    PayrollDepartmentSummary,
    PayrollBankTransfer,
)
from .services import PayrollPeriodService, PayslipCalculationService
from .permissions import PayrollAccessControl
from decimal import Decimal


class PayrollPeriodStatusFilter(SimpleListFilter):
    title = "Status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return PayrollPeriod.STATUS_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class PayrollPeriodYearFilter(SimpleListFilter):
    title = "Year"
    parameter_name = "year"

    def lookups(self, request, model_admin):
        years = (
            PayrollPeriod.objects.values_list("year", flat=True)
            .distinct()
            .order_by("-year")
        )
        return [(year, str(year)) for year in years]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(year=self.value())
        return queryset


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = [
        "period_display",
        "status_badge",
        "total_employees",
        "total_gross_display",
        "total_net_display",
        "processing_progress",
        "created_by",
        "created_at",
    ]
    list_filter = [PayrollPeriodStatusFilter, PayrollPeriodYearFilter, "created_at"]
    search_fields = ["year", "month"]
    readonly_fields = [
        "id",
        "period_name",
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
        "total_etf_contribution",
        "role_based_summary",
        "department_summary",
        "created_at",
        "updated_at",
        "approved_at",
    ]
    fieldsets = (
        ("Period Information", {"fields": ("year", "month", "period_name", "status")}),
        (
            "Dates",
            {"fields": ("start_date", "end_date", "processing_date", "cutoff_date")},
        ),
        (
            "Financial Summary",
            {
                "fields": (
                    "total_employees",
                    "total_working_days",
                    "total_gross_salary",
                    "total_deductions",
                    "total_net_salary",
                )
            },
        ),
        (
            "Contributions",
            {
                "fields": (
                    "total_epf_employee",
                    "total_epf_employer",
                    "total_etf_contribution",
                )
            },
        ),
        (
            "Analytics",
            {
                "fields": ("role_based_summary", "department_summary"),
                "classes": ("collapse",),
            },
        ),
        (
            "Audit Information",
            {
                "fields": (
                    "created_by",
                    "approved_by",
                    "created_at",
                    "updated_at",
                    "approved_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )
    actions = ["start_processing", "complete_processing", "approve_periods"]
    ordering = ["-year", "-month"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("created_by", "approved_by")

    def period_display(self, obj):
        return f"{obj.year}-{obj.month:02d} ({obj.period_name})"

    period_display.short_description = "Period"

    def status_badge(self, obj):
        colors = {
            "DRAFT": "#6c757d",
            "PROCESSING": "#007bff",
            "COMPLETED": "#28a745",
            "APPROVED": "#17a2b8",
            "PAID": "#28a745",
            "CANCELLED": "#dc3545",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            colors.get(obj.status, "#6c757d"),
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def total_gross_display(self, obj):
        return f"LKR {obj.total_gross_salary:,.2f}"

    total_gross_display.short_description = "Total Gross"

    def total_net_display(self, obj):
        return f"LKR {obj.total_net_salary:,.2f}"

    total_net_display.short_description = "Total Net"

    def processing_progress(self, obj):
        total = obj.payslips.count()
        calculated = obj.payslips.filter(status__in=["CALCULATED", "APPROVED"]).count()
        percentage = (calculated / total * 100) if total > 0 else 0
        return format_html(
            '<div style="width: 100px; background-color: #e9ecef; border-radius: 3px;"><div style="width: {}%; background-color: #007bff; height: 20px; border-radius: 3px; text-align: center; color: white; font-size: 11px; line-height: 20px;">{}%</div></div>',
            percentage,
            int(percentage),
        )

    processing_progress.short_description = "Progress"

    def start_processing(self, request, queryset):
        for period in queryset:
            try:
                PayrollPeriodService.start_processing(str(period.id), request.user)
                messages.success(request, f"Started processing {period.period_name}")
            except ValidationError as e:
                messages.error(
                    request, f"Error processing {period.period_name}: {str(e)}"
                )

    start_processing.short_description = "Start processing selected periods"

    def complete_processing(self, request, queryset):
        for period in queryset:
            try:
                PayrollPeriodService.complete_processing(str(period.id), request.user)
                messages.success(request, f"Completed processing {period.period_name}")
            except ValidationError as e:
                messages.error(
                    request, f"Error completing {period.period_name}: {str(e)}"
                )

    complete_processing.short_description = "Complete processing selected periods"

    def approve_periods(self, request, queryset):
        for period in queryset:
            try:
                PayrollPeriodService.approve_payroll(str(period.id), request.user)
                messages.success(request, f"Approved {period.period_name}")
            except ValidationError as e:
                messages.error(
                    request, f"Error approving {period.period_name}: {str(e)}"
                )

    approve_periods.short_description = "Approve selected periods"

    def has_add_permission(self, request):
        return PayrollAccessControl.can_process_payroll(request.user)

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_process_payroll(request.user)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status not in ["DRAFT", "CANCELLED"]:
            return False
        return PayrollAccessControl.can_process_payroll(request.user)


class PayslipStatusFilter(SimpleListFilter):
    title = "Status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return Payslip.STATUS_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class PayslipRoleFilter(SimpleListFilter):
    title = "Employee Role"
    parameter_name = "employee_role"

    def lookups(self, request, model_admin):
        roles = Role.objects.filter(is_active=True).values_list("id", "name")
        return [(role_id, role_name) for role_id, role_name in roles]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(employee__role_id=self.value())
        return queryset


class PayslipDepartmentFilter(SimpleListFilter):
    title = "Department"
    parameter_name = "department"

    def lookups(self, request, model_admin):
        departments = Department.objects.filter(is_active=True).values_list(
            "id", "name"
        )
        return [(dept_id, dept_name) for dept_id, dept_name in departments]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(employee__department_id=self.value())
        return queryset


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = [
        "reference_number",
        "employee_info",
        "period_info",
        "status_badge",
        "basic_salary_display",
        "gross_salary_display",
        "net_salary_display",
        "calculation_status",
        "created_at",
    ]
    list_filter = [
        PayslipStatusFilter,
        PayslipRoleFilter,
        PayslipDepartmentFilter,
        "payroll_period__year",
        "payroll_period__month",
        "created_at",
    ]
    search_fields = [
        "reference_number",
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "employee__email",
    ]
    readonly_fields = [
        "id",
        "reference_number",
        "employee_name",
        "employee_code",
        "division",
        "job_title",
        "account_no",
        "employee_role",
        "total_allowances",
        "total_overtime_pay",
        "total_epf_contribution",
        "created_at",
        "updated_at",
        "approved_at",
    ]
    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "payroll_period",
                    "employee",
                    "reference_number",
                    "status",
                    "sr_no",
                )
            },
        ),
        (
            "Employee Details",
            {
                "fields": (
                    "employee_name",
                    "employee_code",
                    "division",
                    "job_title",
                    "account_no",
                    "employee_role",
                )
            },
        ),
        (
            "Attendance & Working Days",
            {
                "fields": (
                    "working_days",
                    "attended_days",
                    "leave_days",
                    "working_day_meals",
                )
            },
        ),
        (
            "Basic Salary & Bonuses",
            {"fields": ("basic_salary", "ot_basic", "bonus_1", "bonus_2")},
        ),
        (
            "Allowances",
            {
                "fields": (
                    "transport_allowance",
                    "telephone_allowance",
                    "fuel_allowance",
                    "meal_allowance",
                    "attendance_bonus",
                    "performance_bonus",
                    "interim_allowance",
                    "education_allowance",
                )
            },
        ),
        (
            "Overtime & Special Pay",
            {
                "fields": (
                    "religious_pay",
                    "friday_salary",
                    "friday_overtime",
                    "regular_overtime",
                    "friday_work_days",
                    "friday_ot_hours",
                    "overtime_hours",
                )
            },
        ),
        (
            "Gross Salary",
            {"fields": ("gross_salary", "total_allowances", "total_overtime_pay")},
        ),
        (
            "Deductions",
            {
                "fields": (
                    "leave_deduction",
                    "late_penalty",
                    "advance_deduction",
                    "lunch_violation_penalty",
                )
            },
        ),
        (
            "EPF & Tax",
            {
                "fields": (
                    "epf_salary_base",
                    "employee_epf_contribution",
                    "employer_epf_contribution",
                    "total_epf_contribution",
                    "etf_contribution",
                    "income_tax",
                )
            },
        ),
        ("Final Calculation", {"fields": ("total_deductions", "net_salary")}),
        ("Per Day Rates", {"fields": ("fuel_per_day", "meal_per_day")}),
        (
            "Calculation Data",
            {
                "fields": (
                    "role_based_calculations",
                    "attendance_breakdown",
                    "penalty_breakdown",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Audit Information",
            {
                "fields": (
                    "calculated_by",
                    "approved_by",
                    "created_at",
                    "updated_at",
                    "approved_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )
    actions = ["calculate_payslips", "approve_payslips", "export_payslips"]
    ordering = [
        "-payroll_period__year",
        "-payroll_period__month",
        "employee__employee_code",
    ]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "employee",
                "employee__role",
                "employee__department",
                "payroll_period",
                "calculated_by",
                "approved_by",
            )
        )

    def employee_info(self, obj):
        return format_html(
            "<strong>{}</strong><br><small>{} - {}</small>",
            obj.employee.get_full_name(),
            obj.employee.employee_code,
            obj.employee.role.name if obj.employee.role else "No Role",
        )

    employee_info.short_description = "Employee"

    def period_info(self, obj):
        return f"{obj.payroll_period.year}-{obj.payroll_period.month:02d}"

    period_info.short_description = "Period"

    def status_badge(self, obj):
        colors = {
            "DRAFT": "#6c757d",
            "CALCULATED": "#007bff",
            "APPROVED": "#28a745",
            "PAID": "#17a2b8",
            "CANCELLED": "#dc3545",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">{}</span>',
            colors.get(obj.status, "#6c757d"),
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def basic_salary_display(self, obj):
        return f"LKR {obj.basic_salary:,.2f}"

    basic_salary_display.short_description = "Basic Salary"

    def gross_salary_display(self, obj):
        return f"LKR {obj.gross_salary:,.2f}"

    gross_salary_display.short_description = "Gross Salary"

    def net_salary_display(self, obj):
        return f"LKR {obj.net_salary:,.2f}"

    net_salary_display.short_description = "Net Salary"

    def calculation_status(self, obj):
        if obj.status == "CALCULATED":
            return format_html('<span style="color: green;">✓ Calculated</span>')
        elif obj.status == "APPROVED":
            return format_html('<span style="color: blue;">✓ Approved</span>')
        else:
            return format_html('<span style="color: orange;">Pending</span>')

    calculation_status.short_description = "Calculation"

    def calculate_payslips(self, request, queryset):
        for payslip in queryset:
            try:
                PayslipCalculationService.calculate_single_payslip(
                    str(payslip.id), request.user
                )
                messages.success(
                    request,
                    f"Calculated payslip for {payslip.employee.get_full_name()}",
                )
            except ValidationError as e:
                messages.error(
                    request,
                    f"Error calculating {payslip.employee.get_full_name()}: {str(e)}",
                )

    calculate_payslips.short_description = "Calculate selected payslips"

    def approve_payslips(self, request, queryset):
        for payslip in queryset:
            try:
                if payslip.status == "CALCULATED":
                    payslip.approve(request.user)
                    messages.success(
                        request,
                        f"Approved payslip for {payslip.employee.get_full_name()}",
                    )
                else:
                    messages.warning(
                        request,
                        f"Cannot approve {payslip.employee.get_full_name()} - not calculated",
                    )
            except ValidationError as e:
                messages.error(
                    request,
                    f"Error approving {payslip.employee.get_full_name()}: {str(e)}",
                )

    approve_payslips.short_description = "Approve selected payslips"

    def export_payslips(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="payslips.csv"'

        import csv

        writer = csv.writer(response)
        writer.writerow(
            [
                "Reference",
                "Employee",
                "Period",
                "Basic Salary",
                "Gross Salary",
                "Net Salary",
                "Status",
            ]
        )

        for payslip in queryset:
            writer.writerow(
                [
                    payslip.reference_number,
                    payslip.employee.get_full_name(),
                    f"{payslip.payroll_period.year}-{payslip.payroll_period.month:02d}",
                    payslip.basic_salary,
                    payslip.gross_salary,
                    payslip.net_salary,
                    payslip.status,
                ]
            )

        return response

    export_payslips.short_description = "Export selected payslips to CSV"

    def has_add_permission(self, request):
        return PayrollAccessControl.can_process_payroll(request.user)

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_process_payroll(request.user)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in ["APPROVED", "PAID"]:
            return False
        return PayrollAccessControl.can_process_payroll(request.user)


class SalaryAdvanceStatusFilter(SimpleListFilter):
    title = "Status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return SalaryAdvance.STATUS_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class SalaryAdvanceTypeFilter(SimpleListFilter):
    title = "Advance Type"
    parameter_name = "advance_type"

    def lookups(self, request, model_admin):
        return SalaryAdvance.ADVANCE_TYPES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(advance_type=self.value())
        return queryset


class SalaryAdvanceEmployeeRoleFilter(SimpleListFilter):
    title = "Employee Role"
    parameter_name = "employee_role"

    def lookups(self, request, model_admin):
        roles = Role.objects.filter(is_active=True).values_list("id", "name")
        return [(role_id, role_name) for role_id, role_name in roles]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(employee__role_id=self.value())
        return queryset


@admin.register(SalaryAdvance)
class SalaryAdvanceAdmin(admin.ModelAdmin):
    list_display = [
        "reference_number",
        "employee_info",
        "advance_type_badge",
        "status_badge",
        "amount_display",
        "outstanding_display",
        "installments",
        "requested_date",
        "overdue_status",
    ]
    list_filter = [
        SalaryAdvanceStatusFilter,
        SalaryAdvanceTypeFilter,
        SalaryAdvanceEmployeeRoleFilter,
        "requested_date",
        "approved_date",
    ]
    search_fields = [
        "reference_number",
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "reason",
    ]
    readonly_fields = [
        "id",
        "reference_number",
        "employee_basic_salary",
        "max_allowed_percentage",
        "advance_count_this_year",
        "is_overdue",
        "created_at",
        "updated_at",
        "approved_date",
        "disbursement_date",
        "completion_date",
    ]
    fieldsets = (
        (
            "Basic Information",
            {"fields": ("employee", "advance_type", "reference_number", "status")},
        ),
        (
            "Financial Details",
            {
                "fields": (
                    "amount",
                    "outstanding_amount",
                    "monthly_deduction",
                    "installments",
                )
            },
        ),
        ("Request Information", {"fields": ("reason", "purpose_details")}),
        (
            "Employee Context",
            {
                "fields": (
                    "employee_basic_salary",
                    "max_allowed_percentage",
                    "advance_count_this_year",
                )
            },
        ),
        (
            "Timeline",
            {
                "fields": (
                    "requested_date",
                    "approved_date",
                    "disbursement_date",
                    "completion_date",
                )
            },
        ),
        ("Status Information", {"fields": ("is_overdue", "is_active")}),
        (
            "Audit Information",
            {
                "fields": ("requested_by", "approved_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )
    actions = [
        "approve_advances",
        "activate_advances",
        "cancel_advances",
        "export_advances",
    ]
    ordering = ["-created_at"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "employee",
                "employee__role",
                "employee__department",
                "requested_by",
                "approved_by",
            )
        )

    def employee_info(self, obj):
        return format_html(
            "<strong>{}</strong><br><small>{} - {}</small>",
            obj.employee.get_full_name(),
            obj.employee.employee_code,
            obj.employee.role.name if obj.employee.role else "No Role",
        )

    employee_info.short_description = "Employee"

    def advance_type_badge(self, obj):
        colors = {
            "SALARY": "#007bff",
            "EMERGENCY": "#dc3545",
            "PURCHASE": "#28a745",
            "MEDICAL": "#ffc107",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">{}</span>',
            colors.get(obj.advance_type, "#6c757d"),
            obj.get_advance_type_display(),
        )

    advance_type_badge.short_description = "Type"

    def status_badge(self, obj):
        colors = {
            "PENDING": "#ffc107",
            "APPROVED": "#17a2b8",
            "ACTIVE": "#28a745",
            "COMPLETED": "#6c757d",
            "CANCELLED": "#dc3545",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">{}</span>',
            colors.get(obj.status, "#6c757d"),
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def amount_display(self, obj):
        return f"LKR {obj.amount:,.2f}"

    amount_display.short_description = "Amount"

    def outstanding_display(self, obj):
        if obj.outstanding_amount > 0:
            return format_html(
                '<span style="color: red;">LKR {}</span>',
                f"{obj.outstanding_amount:,.2f}",
            )
        return format_html('<span style="color: green;">LKR 0.00</span>')

    outstanding_display.short_description = "Outstanding"

    def overdue_status(self, obj):
        if obj.is_overdue:
            return format_html(
                '<span style="color: red; font-weight: bold;">⚠ OVERDUE</span>'
            )
        elif obj.status == "ACTIVE":
            return format_html('<span style="color: green;">✓ Active</span>')
        return format_html('<span style="color: gray;">-</span>')

    overdue_status.short_description = "Overdue"

    def approve_advances(self, request, queryset):
        from .services import SalaryAdvanceService

        for advance in queryset:
            try:
                if advance.status == "PENDING":
                    SalaryAdvanceService.approve_advance(str(advance.id), request.user)
                    messages.success(
                        request, f"Approved advance {advance.reference_number}"
                    )
                else:
                    messages.warning(
                        request,
                        f"Cannot approve {advance.reference_number} - not pending",
                    )
            except ValidationError as e:
                messages.error(
                    request, f"Error approving {advance.reference_number}: {str(e)}"
                )

    approve_advances.short_description = "Approve selected advances"

    def activate_advances(self, request, queryset):
        from .services import SalaryAdvanceService

        for advance in queryset:
            try:
                if advance.status == "APPROVED":
                    SalaryAdvanceService.activate_advance(str(advance.id), request.user)
                    messages.success(
                        request, f"Activated advance {advance.reference_number}"
                    )
                else:
                    messages.warning(
                        request,
                        f"Cannot activate {advance.reference_number} - not approved",
                    )
            except ValidationError as e:
                messages.error(
                    request, f"Error activating {advance.reference_number}: {str(e)}"
                )

    activate_advances.short_description = "Activate selected advances"

    def cancel_advances(self, request, queryset):
        for advance in queryset:
            try:
                if advance.status in ["PENDING", "APPROVED"]:
                    advance.status = "CANCELLED"
                    advance.save()
                    messages.success(
                        request, f"Cancelled advance {advance.reference_number}"
                    )
                else:
                    messages.warning(
                        request,
                        f"Cannot cancel {advance.reference_number} - already processed",
                    )
            except Exception as e:
                messages.error(
                    request, f"Error cancelling {advance.reference_number}: {str(e)}"
                )

    cancel_advances.short_description = "Cancel selected advances"

    def export_advances(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="salary_advances.csv"'

        import csv

        writer = csv.writer(response)
        writer.writerow(
            [
                "Reference",
                "Employee",
                "Type",
                "Amount",
                "Outstanding",
                "Status",
                "Requested Date",
            ]
        )

        for advance in queryset:
            writer.writerow(
                [
                    advance.reference_number,
                    advance.employee.get_full_name(),
                    advance.get_advance_type_display(),
                    advance.amount,
                    advance.outstanding_amount,
                    advance.get_status_display(),
                    advance.requested_date,
                ]
            )

        return response

    export_advances.short_description = "Export selected advances to CSV"

    def has_add_permission(self, request):
        return PayrollAccessControl.can_manage_salary_advance(request.user)

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_manage_salary_advance(request.user)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in ["ACTIVE", "COMPLETED"]:
            return False
        return PayrollAccessControl.can_manage_salary_advance(request.user)


class DepartmentSummaryYearFilter(SimpleListFilter):
    title = "Year"
    parameter_name = "year"

    def lookups(self, request, model_admin):
        years = (
            PayrollDepartmentSummary.objects.values_list(
                "payroll_period__year", flat=True
            )
            .distinct()
            .order_by("-payroll_period__year")
        )
        return [(year, str(year)) for year in years]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(payroll_period__year=self.value())
        return queryset


class DepartmentSummaryDepartmentFilter(SimpleListFilter):
    title = "Department"
    parameter_name = "department"

    def lookups(self, request, model_admin):
        departments = Department.objects.filter(is_active=True).values_list(
            "id", "name"
        )
        return [(dept_id, dept_name) for dept_id, dept_name in departments]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(department_id=self.value())
        return queryset


@admin.register(PayrollDepartmentSummary)
class PayrollDepartmentSummaryAdmin(admin.ModelAdmin):
    list_display = [
        "department_period",
        "employee_count",
        "total_gross_display",
        "total_net_display",
        "average_salary_display",
        "budget_utilization_display",
        "efficiency_score",
        "created_at",
    ]
    list_filter = [
        DepartmentSummaryYearFilter,
        DepartmentSummaryDepartmentFilter,
        "payroll_period__month",
        "created_at",
    ]
    search_fields = [
        "department__name",
        "payroll_period__year",
        "payroll_period__month",
    ]
    readonly_fields = [
        "id",
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
        "budget_utilization_percentage",
        "role_breakdown",
        "performance_metrics",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        ("Summary Information", {"fields": ("payroll_period", "department")}),
        ("Employee Statistics", {"fields": ("employee_count", "average_salary")}),
        (
            "Financial Summary",
            {
                "fields": (
                    "total_basic_salary",
                    "total_allowances",
                    "total_overtime_pay",
                    "total_gross_salary",
                    "total_deductions",
                    "total_net_salary",
                )
            },
        ),
        (
            "Contributions",
            {
                "fields": (
                    "total_epf_employee",
                    "total_epf_employer",
                    "total_etf_contribution",
                )
            },
        ),
        (
            "Budget Analysis",
            {"fields": ("department_budget", "budget_utilization_percentage")},
        ),
        (
            "Analytics",
            {
                "fields": ("role_breakdown", "performance_metrics"),
                "classes": ("collapse",),
            },
        ),
        (
            "Audit Information",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    actions = ["recalculate_summaries", "export_summaries"]
    ordering = ["-payroll_period__year", "-payroll_period__month", "department__name"]

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("payroll_period", "department")
        )

    def department_period(self, obj):
        return format_html(
            "<strong>{}</strong><br><small>{}-{:02d}</small>",
            obj.department.name,
            obj.payroll_period.year,
            obj.payroll_period.month,
        )

    department_period.short_description = "Department & Period"

    def total_gross_display(self, obj):
        return f"LKR {obj.total_gross_salary:,.2f}"

    total_gross_display.short_description = "Total Gross"

    def total_net_display(self, obj):
        return f"LKR {obj.total_net_salary:,.2f}"

    total_net_display.short_description = "Total Net"

    def average_salary_display(self, obj):
        return f"LKR {obj.average_salary:,.2f}"

    average_salary_display.short_description = "Avg Salary"

    def budget_utilization_display(self, obj):
        percentage = obj.budget_utilization_percentage
        color = (
            "#28a745"
            if percentage <= 100
            else "#dc3545" if percentage > 110 else "#ffc107"
        )
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.1f}%</span>',
            color,
            percentage,
        )

    budget_utilization_display.short_description = "Budget Utilization"

    def efficiency_score(self, obj):
        if (
            obj.performance_metrics
            and "department_efficiency_score" in obj.performance_metrics
        ):
            score = obj.performance_metrics["department_efficiency_score"]
            color = (
                "#28a745" if score >= 80 else "#ffc107" if score >= 60 else "#dc3545"
            )
            return format_html(
                '<span style="color: {}; font-weight: bold;">{:.1f}</span>',
                color,
                score,
            )
        return format_html('<span style="color: gray;">-</span>')

    efficiency_score.short_description = "Efficiency Score"

    def recalculate_summaries(self, request, queryset):
        for summary in queryset:
            try:
                summary.calculate_summary()
                messages.success(
                    request, f"Recalculated summary for {summary.department.name}"
                )
            except Exception as e:
                messages.error(
                    request, f"Error recalculating {summary.department.name}: {str(e)}"
                )

    recalculate_summaries.short_description = "Recalculate selected summaries"

    def export_summaries(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="department_summaries.csv"'
        )

        import csv

        writer = csv.writer(response)
        writer.writerow(
            [
                "Department",
                "Period",
                "Employees",
                "Total Gross",
                "Total Net",
                "Average Salary",
                "Budget Utilization",
            ]
        )

        for summary in queryset:
            writer.writerow(
                [
                    summary.department.name,
                    f"{summary.payroll_period.year}-{summary.payroll_period.month:02d}",
                    summary.employee_count,
                    summary.total_gross_salary,
                    summary.total_net_salary,
                    summary.average_salary,
                    f"{summary.budget_utilization_percentage:.1f}%",
                ]
            )

        return response

    export_summaries.short_description = "Export selected summaries to CSV"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_view_payroll_reports(request.user)

    def has_delete_permission(self, request, obj=None):
        return PayrollAccessControl.can_process_payroll(request.user)


class PayrollConfigurationTypeFilter(SimpleListFilter):
    title = "Configuration Type"
    parameter_name = "configuration_type"

    def lookups(self, request, model_admin):
        return PayrollConfiguration.CONFIGURATION_TYPES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(configuration_type=self.value())
        return queryset


class PayrollConfigurationRoleFilter(SimpleListFilter):
    title = "Role"
    parameter_name = "role"

    def lookups(self, request, model_admin):
        roles = Role.objects.filter(is_active=True).values_list("id", "name")
        return [(role_id, role_name) for role_id, role_name in roles]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(role_id=self.value())
        return queryset


class PayrollConfigurationDepartmentFilter(SimpleListFilter):
    title = "Department"
    parameter_name = "department"

    def lookups(self, request, model_admin):
        departments = Department.objects.filter(is_active=True).values_list(
            "id", "name"
        )
        return [(dept_id, dept_name) for dept_id, dept_name in departments]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(department_id=self.value())
        return queryset


@admin.register(PayrollConfiguration)
class PayrollConfigurationAdmin(admin.ModelAdmin):
    list_display = [
        "configuration_key",
        "configuration_type_badge",
        "scope_info",
        "configuration_value_display",
        "value_type_badge",
        "active_status",
        "effective_period",
        "created_by",
        "updated_at",
    ]
    list_filter = [
        PayrollConfigurationTypeFilter,
        PayrollConfigurationRoleFilter,
        PayrollConfigurationDepartmentFilter,
        "value_type",
        "is_active",
        "effective_from",
    ]
    search_fields = [
        "configuration_key",
        "configuration_value",
        "description",
        "role__name",
        "department__name",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    fieldsets = (
        (
            "Configuration Details",
            {
                "fields": (
                    "configuration_type",
                    "configuration_key",
                    "configuration_value",
                    "value_type",
                    "description",
                )
            },
        ),
        ("Scope", {"fields": ("role", "department")}),
        ("Activation", {"fields": ("is_active", "effective_from", "effective_to")}),
        (
            "Audit Information",
            {
                "fields": ("created_by", "updated_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )
    actions = [
        "activate_configurations",
        "deactivate_configurations",
        "duplicate_configurations",
        "export_configurations",
    ]
    ordering = ["configuration_type", "configuration_key"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("role", "department", "created_by", "updated_by")
        )

    def configuration_type_badge(self, obj):
        colors = {
            "SALARY": "#007bff",
            "ALLOWANCE": "#28a745",
            "DEDUCTION": "#dc3545",
            "TAX": "#ffc107",
            "BONUS": "#17a2b8",
            "PENALTY": "#6f42c1",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">{}</span>',
            colors.get(obj.configuration_type, "#6c757d"),
            obj.get_configuration_type_display(),
        )

    configuration_type_badge.short_description = "Type"

    def scope_info(self, obj):
        if obj.role and obj.department:
            return format_html(
                "<strong>Role:</strong> {}<br><strong>Dept:</strong> {}",
                obj.role.name,
                obj.department.name,
            )
        elif obj.role:
            return format_html("<strong>Role:</strong> {}", obj.role.name)
        elif obj.department:
            return format_html("<strong>Dept:</strong> {}", obj.department.name)
        return format_html('<span style="color: gray;">Global</span>')

    scope_info.short_description = "Scope"

    def configuration_value_display(self, obj):
        if obj.value_type == "DECIMAL":
            try:
                value = float(obj.configuration_value)
                return (
                    f"LKR {value:,.2f}"
                    if "ALLOWANCE" in obj.configuration_key
                    or "SALARY" in obj.configuration_key
                    else f"{value:,.2f}"
                )
            except:
                return obj.configuration_value
        elif obj.value_type == "PERCENTAGE":
            return f"{obj.configuration_value}%"
        elif obj.value_type == "BOOLEAN":
            return (
                "✓ Yes"
                if obj.configuration_value.lower() in ["true", "1", "yes"]
                else "✗ No"
            )
        return obj.configuration_value

    configuration_value_display.short_description = "Value"

    def value_type_badge(self, obj):
        colors = {
            "DECIMAL": "#007bff",
            "INTEGER": "#28a745",
            "PERCENTAGE": "#ffc107",
            "BOOLEAN": "#17a2b8",
            "TEXT": "#6c757d",
            "JSON": "#6f42c1",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 1px 4px; border-radius: 2px; font-size: 9px;">{}</span>',
            colors.get(obj.value_type, "#6c757d"),
            obj.value_type,
        )

    value_type_badge.short_description = "Type"

    def active_status(self, obj):
        if obj.is_active:
            return format_html('<span style="color: green;">✓ Active</span>')
        return format_html('<span style="color: red;">✗ Inactive</span>')

    active_status.short_description = "Status"

    def effective_period(self, obj):
        if obj.effective_to:
            return format_html(
                "{}<br><small>to {}</small>",
                obj.effective_from.strftime("%Y-%m-%d"),
                obj.effective_to.strftime("%Y-%m-%d"),
            )
        return format_html(
            "{}<br><small>ongoing</small>", obj.effective_from.strftime("%Y-%m-%d")
        )

    effective_period.short_description = "Effective Period"

    def activate_configurations(self, request, queryset):
        updated = queryset.update(is_active=True)
        messages.success(request, f"Activated {updated} configurations")

    activate_configurations.short_description = "Activate selected configurations"

    def deactivate_configurations(self, request, queryset):
        updated = queryset.update(is_active=False)
        messages.success(request, f"Deactivated {updated} configurations")

    deactivate_configurations.short_description = "Deactivate selected configurations"

    def duplicate_configurations(self, request, queryset):
        for config in queryset:
            try:
                config.pk = None
                config.configuration_key = f"{config.configuration_key}_COPY"
                config.is_active = False
                config.created_by = request.user
                config.updated_by = None
                config.save()
                messages.success(
                    request, f"Duplicated configuration {config.configuration_key}"
                )
            except Exception as e:
                messages.error(request, f"Error duplicating configuration: {str(e)}")

    duplicate_configurations.short_description = "Duplicate selected configurations"

    def export_configurations(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="payroll_configurations.csv"'
        )

        import csv

        writer = csv.writer(response)
        writer.writerow(
            [
                "Type",
                "Key",
                "Value",
                "Value Type",
                "Role",
                "Department",
                "Active",
                "Effective From",
            ]
        )

        for config in queryset:
            writer.writerow(
                [
                    config.get_configuration_type_display(),
                    config.configuration_key,
                    config.configuration_value,
                    config.value_type,
                    config.role.name if config.role else "",
                    config.department.name if config.department else "",
                    config.is_active,
                    config.effective_from,
                ]
            )

        return response

    export_configurations.short_description = "Export selected configurations to CSV"

    def has_add_permission(self, request):
        return PayrollAccessControl.can_configure_payroll(request.user)

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_configure_payroll(request.user)

    def has_delete_permission(self, request, obj=None):
        return PayrollAccessControl.can_configure_payroll(request.user)


class BankTransferStatusFilter(SimpleListFilter):
    title = "Status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return PayrollBankTransfer.STATUS_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class BankTransferYearFilter(SimpleListFilter):
    title = "Year"
    parameter_name = "year"

    def lookups(self, request, model_admin):
        years = (
            PayrollBankTransfer.objects.values_list("payroll_period__year", flat=True)
            .distinct()
            .order_by("-payroll_period__year")
        )
        return [(year, str(year)) for year in years]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(payroll_period__year=self.value())
        return queryset


@admin.register(PayrollBankTransfer)
class PayrollBankTransferAdmin(admin.ModelAdmin):
    list_display = [
        "batch_reference",
        "period_info",
        "status_badge",
        "total_employees",
        "total_amount_display",
        "file_status",
        "processing_timeline",
        "created_by",
        "created_at",
    ]
    list_filter = [
        BankTransferStatusFilter,
        BankTransferYearFilter,
        "payroll_period__month",
        "bank_file_format",
        "created_at",
    ]
    search_fields = ["batch_reference", "payroll_period__year", "payroll_period__month"]
    readonly_fields = [
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
    fieldsets = (
        (
            "Transfer Information",
            {"fields": ("payroll_period", "batch_reference", "status")},
        ),
        ("Financial Summary", {"fields": ("total_employees", "total_amount")}),
        ("File Information", {"fields": ("bank_file_path", "bank_file_format")}),
        (
            "Processing Timeline",
            {"fields": ("generated_at", "sent_at", "processed_at")},
        ),
        (
            "Bank Response",
            {"fields": ("bank_response", "error_details"), "classes": ("collapse",)},
        ),
        (
            "Audit Information",
            {
                "fields": ("created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )
    actions = [
        "generate_transfer_files",
        "mark_as_sent",
        "mark_as_processed",
        "export_transfers",
    ]
    ordering = ["-created_at"]

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("payroll_period", "created_by")
        )

    def period_info(self, obj):
        return format_html(
            "<strong>{}</strong><br><small>{}</small>",
            obj.payroll_period.period_name,
            f"{obj.payroll_period.year}-{obj.payroll_period.month:02d}",
        )

    period_info.short_description = "Period"

    def status_badge(self, obj):
        colors = {
            "PENDING": "#6c757d",
            "GENERATED": "#007bff",
            "SENT": "#ffc107",
            "PROCESSED": "#17a2b8",
            "COMPLETED": "#28a745",
            "FAILED": "#dc3545",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">{}</span>',
            colors.get(obj.status, "#6c757d"),
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def total_amount_display(self, obj):
        return f"LKR {obj.total_amount:,.2f}"

    total_amount_display.short_description = "Total Amount"

    def file_status(self, obj):
        if obj.bank_file_path:
            return format_html(
                '<span style="color: green;">✓ Generated</span><br><small>{}</small>',
                obj.bank_file_format,
            )
        return format_html('<span style="color: gray;">Not Generated</span>')

    file_status.short_description = "File Status"

    def processing_timeline(self, obj):
        timeline = []
        if obj.generated_at:
            timeline.append(f"Generated: {obj.generated_at.strftime('%m/%d %H:%M')}")
        if obj.sent_at:
            timeline.append(f"Sent: {obj.sent_at.strftime('%m/%d %H:%M')}")
        if obj.processed_at:
            timeline.append(f"Processed: {obj.processed_at.strftime('%m/%d %H:%M')}")

        if timeline:
            return format_html("<br>".join(timeline))
        return format_html('<span style="color: gray;">Not Started</span>')

    processing_timeline.short_description = "Timeline"

    def generate_transfer_files(self, request, queryset):
        from .services import BankTransferService

        for transfer in queryset:
            try:
                if transfer.status == "PENDING":
                    BankTransferService.generate_bank_transfer_file(
                        str(transfer.payroll_period.id), request.user
                    )
                    messages.success(
                        request, f"Generated file for {transfer.batch_reference}"
                    )
                else:
                    messages.warning(
                        request,
                        f"Cannot generate file for {transfer.batch_reference} - not pending",
                    )
            except ValidationError as e:
                messages.error(
                    request, f"Error generating {transfer.batch_reference}: {str(e)}"
                )

    generate_transfer_files.short_description = "Generate transfer files"

    def mark_as_sent(self, request, queryset):
        from .services import BankTransferService

        for transfer in queryset:
            try:
                if transfer.status == "GENERATED":
                    BankTransferService.update_transfer_status(
                        str(transfer.id), "SENT", request.user
                    )
                    messages.success(
                        request, f"Marked {transfer.batch_reference} as sent"
                    )
                else:
                    messages.warning(
                        request,
                        f"Cannot mark {transfer.batch_reference} as sent - not generated",
                    )
            except ValidationError as e:
                messages.error(
                    request, f"Error updating {transfer.batch_reference}: {str(e)}"
                )

    mark_as_sent.short_description = "Mark as sent to bank"

    def mark_as_processed(self, request, queryset):
        from .services import BankTransferService

        for transfer in queryset:
            try:
                if transfer.status == "SENT":
                    BankTransferService.update_transfer_status(
                        str(transfer.id), "PROCESSED", request.user
                    )
                    messages.success(
                        request, f"Marked {transfer.batch_reference} as processed"
                    )
                else:
                    messages.warning(
                        request,
                        f"Cannot mark {transfer.batch_reference} as processed - not sent",
                    )
            except ValidationError as e:
                messages.error(
                    request, f"Error updating {transfer.batch_reference}: {str(e)}"
                )

    mark_as_processed.short_description = "Mark as processed by bank"

    def export_transfers(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="bank_transfers.csv"'

        import csv

        writer = csv.writer(response)
        writer.writerow(
            [
                "Batch Reference",
                "Period",
                "Status",
                "Employees",
                "Total Amount",
                "Generated At",
                "Sent At",
            ]
        )

        for transfer in queryset:
            writer.writerow(
                [
                    transfer.batch_reference,
                    f"{transfer.payroll_period.year}-{transfer.payroll_period.month:02d}",
                    transfer.get_status_display(),
                    transfer.total_employees,
                    transfer.total_amount,
                    (
                        transfer.generated_at.strftime("%Y-%m-%d %H:%M")
                        if transfer.generated_at
                        else ""
                    ),
                    (
                        transfer.sent_at.strftime("%Y-%m-%d %H:%M")
                        if transfer.sent_at
                        else ""
                    ),
                ]
            )

        return response

    export_transfers.short_description = "Export selected transfers to CSV"

    def has_add_permission(self, request):
        return PayrollAccessControl.can_export_payroll(request.user)

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_export_payroll(request.user)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in ["SENT", "PROCESSED", "COMPLETED"]:
            return False
        return PayrollAccessControl.can_export_payroll(request.user)


class PayrollAuditLogActionFilter(SimpleListFilter):
    title = "Action Type"
    parameter_name = "action_type"

    def lookups(self, request, model_admin):
        return PayrollAuditLog.ACTION_TYPES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(action_type=self.value())
        return queryset


class PayrollAuditLogUserFilter(SimpleListFilter):
    title = "User"
    parameter_name = "user"

    def lookups(self, request, model_admin):
        users = (
            CustomUser.objects.filter(payroll_audit_logs__isnull=False)
            .distinct()
            .values_list("id", "first_name", "last_name")
        )
        return [
            (user_id, f"{first_name} {last_name}")
            for user_id, first_name, last_name in users
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(user_id=self.value())
        return queryset


class PayrollAuditLogDateFilter(SimpleListFilter):
    title = "Date Range"
    parameter_name = "date_range"

    def lookups(self, request, model_admin):
        return [
            ("today", "Today"),
            ("week", "This Week"),
            ("month", "This Month"),
            ("quarter", "This Quarter"),
        ]

    def queryset(self, request, queryset):
        from datetime import timedelta
        from django.utils import timezone

        if self.value() == "today":
            return queryset.filter(created_at__date=timezone.now().date())
        elif self.value() == "week":
            return queryset.filter(created_at__gte=timezone.now() - timedelta(days=7))
        elif self.value() == "month":
            return queryset.filter(created_at__gte=timezone.now() - timedelta(days=30))
        elif self.value() == "quarter":
            return queryset.filter(created_at__gte=timezone.now() - timedelta(days=90))
        return queryset


@admin.register(PayrollAuditLog)
class PayrollAuditLogAdmin(admin.ModelAdmin):
    list_display = [
        "action_type_badge",
        "user_info",
        "employee_info",
        "description_short",
        "payroll_period_info",
        "ip_address",
        "created_at",
    ]
    list_filter = [
        PayrollAuditLogActionFilter,
        PayrollAuditLogUserFilter,
        PayrollAuditLogDateFilter,
        "created_at",
    ]
    search_fields = [
        "action_type",
        "description",
        "user__first_name",
        "user__last_name",
        "employee__first_name",
        "employee__last_name",
        "ip_address",
    ]
    readonly_fields = [
        "id",
        "action_type",
        "user",
        "employee",
        "payslip",
        "salary_advance",
        "payroll_period",
        "description",
        "old_values",
        "new_values",
        "additional_data",
        "ip_address",
        "user_agent",
        "created_at",
    ]
    fieldsets = (
        ("Action Information", {"fields": ("action_type", "description")}),
        (
            "Context",
            {
                "fields": (
                    "user",
                    "employee",
                    "payslip",
                    "salary_advance",
                    "payroll_period",
                )
            },
        ),
        (
            "Data Changes",
            {
                "fields": ("old_values", "new_values", "additional_data"),
                "classes": ("collapse",),
            },
        ),
        (
            "Technical Information",
            {"fields": ("ip_address", "user_agent"), "classes": ("collapse",)},
        ),
        ("Timestamp", {"fields": ("created_at",)}),
    )
    actions = ["export_audit_logs", "archive_old_logs"]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "user", "employee", "payroll_period", "payslip", "salary_advance"
            )
        )

    def action_type_badge(self, obj):
        colors = {
            "PERIOD_CREATED": "#007bff",
            "PERIOD_PROCESSED": "#17a2b8",
            "PERIOD_APPROVED": "#28a745",
            "PAYSLIP_CALCULATED": "#007bff",
            "PAYSLIP_APPROVED": "#28a745",
            "PAYSLIP_PAID": "#6f42c1",
            "ADVANCE_REQUESTED": "#ffc107",
            "ADVANCE_APPROVED": "#28a745",
            "ADVANCE_DISBURSED": "#17a2b8",
            "CONFIGURATION_CHANGED": "#fd7e14",
            "BANK_TRANSFER_GENERATED": "#20c997",
            "SALARY_ADJUSTMENT": "#e83e8c",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">{}</span>',
            colors.get(obj.action_type, "#6c757d"),
            obj.action_type.replace("_", " "),
        )

    action_type_badge.short_description = "Action"

    def user_info(self, obj):
        if obj.user:
            return format_html(
                "<strong>{}</strong><br><small>{}</small>",
                obj.user.get_full_name(),
                obj.user.email,
            )
        return format_html('<span style="color: gray;">System</span>')

    user_info.short_description = "User"

    def employee_info(self, obj):
        if obj.employee:
            return format_html(
                "<strong>{}</strong><br><small>{}</small>",
                obj.employee.get_full_name(),
                obj.employee.employee_code,
            )
        return format_html('<span style="color: gray;">-</span>')

    employee_info.short_description = "Employee"

    def description_short(self, obj):
        if len(obj.description) > 50:
            return f"{obj.description[:50]}..."
        return obj.description

    description_short.short_description = "Description"

    def payroll_period_info(self, obj):
        if obj.payroll_period:
            return format_html("<strong>{}</strong>", obj.payroll_period.period_name)
        return format_html('<span style="color: gray;">-</span>')

    payroll_period_info.short_description = "Period"

    def export_audit_logs(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="payroll_audit_logs.csv"'
        )

        import csv

        writer = csv.writer(response)
        writer.writerow(
            ["Timestamp", "Action", "User", "Employee", "Description", "IP Address"]
        )

        for log in queryset:
            writer.writerow(
                [
                    log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    log.action_type,
                    log.user.get_full_name() if log.user else "System",
                    log.employee.get_full_name() if log.employee else "",
                    log.description,
                    log.ip_address,
                ]
            )

        return response

    export_audit_logs.short_description = "Export selected logs to CSV"

    def archive_old_logs(self, request, queryset):
        from datetime import timedelta

        cutoff_date = timezone.now() - timedelta(days=365)
        old_logs = queryset.filter(created_at__lt=cutoff_date)
        count = old_logs.count()
        old_logs.delete()
        messages.success(request, f"Archived {count} old audit logs")

    archive_old_logs.short_description = "Archive logs older than 1 year"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return PayrollAccessControl.can_configure_payroll(request.user)


class PayrollReportTypeFilter(SimpleListFilter):
    title = "Report Type"
    parameter_name = "report_type"

    def lookups(self, request, model_admin):
        return PayrollReport.REPORT_TYPES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(report_type=self.value())
        return queryset


class PayrollReportStatusFilter(SimpleListFilter):
    title = "Generation Status"
    parameter_name = "generation_status"

    def lookups(self, request, model_admin):
        return [
            ("PENDING", "Pending"),
            ("GENERATING", "Generating"),
            ("COMPLETED", "Completed"),
            ("FAILED", "Failed"),
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(generation_status=self.value())
        return queryset


class PayrollReportFormatFilter(SimpleListFilter):
    title = "Format"
    parameter_name = "report_format"

    def lookups(self, request, model_admin):
        return PayrollReport.FORMAT_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(report_format=self.value())
        return queryset


@admin.register(PayrollReport)
class PayrollReportAdmin(admin.ModelAdmin):
    list_display = [
        "report_name",
        "report_type_badge",
        "format_badge",
        "period_info",
        "status_badge",
        "file_info",
        "generation_time",
        "generated_by",
        "created_at",
    ]
    list_filter = [
        PayrollReportTypeFilter,
        PayrollReportStatusFilter,
        PayrollReportFormatFilter,
        "payroll_period__year",
        "created_at",
    ]
    search_fields = [
        "report_name",
        "payroll_period__year",
        "payroll_period__month",
        "generated_by__first_name",
        "generated_by__last_name",
    ]
    readonly_fields = [
        "id",
        "file_path",
        "file_size",
        "generation_status",
        "completed_at",
        "error_message",
        "created_at",
    ]
    fieldsets = (
        (
            "Report Information",
            {"fields": ("report_type", "report_format", "report_name")},
        ),
        ("Context", {"fields": ("payroll_period", "department", "role")}),
        (
            "Generation",
            {"fields": ("generation_status", "completed_at", "error_message")},
        ),
        ("File Information", {"fields": ("file_path", "file_size")}),
        ("Parameters", {"fields": ("parameters",), "classes": ("collapse",)}),
        (
            "Audit Information",
            {"fields": ("generated_by", "created_at"), "classes": ("collapse",)},
        ),
    )
    actions = ["regenerate_reports", "download_reports", "delete_report_files"]
    ordering = ["-created_at"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("payroll_period", "department", "role", "generated_by")
        )

    def report_type_badge(self, obj):
        colors = {
            "MONTHLY_SUMMARY": "#007bff",
            "DEPARTMENT_SUMMARY": "#28a745",
            "ROLE_SUMMARY": "#17a2b8",
            "INDIVIDUAL_PAYSLIP": "#ffc107",
            "BANK_TRANSFER": "#fd7e14",
            "EPF_REPORT": "#6f42c1",
            "ETF_REPORT": "#e83e8c",
            "TAX_REPORT": "#20c997",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">{}</span>',
            colors.get(obj.report_type, "#6c757d"),
            obj.get_report_type_display(),
        )

    report_type_badge.short_description = "Type"

    def format_badge(self, obj):
        colors = {"PDF": "#dc3545", "EXCEL": "#28a745", "CSV": "#007bff"}
        return format_html(
            '<span style="background-color: {}; color: white; padding: 1px 4px; border-radius: 2px; font-size: 9px;">{}</span>',
            colors.get(obj.report_format, "#6c757d"),
            obj.report_format,
        )

    format_badge.short_description = "Format"

    def period_info(self, obj):
        return format_html("<strong>{}</strong>", obj.payroll_period.period_name)

    period_info.short_description = "Period"

    def status_badge(self, obj):
        colors = {
            "PENDING": "#6c757d",
            "GENERATING": "#ffc107",
            "COMPLETED": "#28a745",
            "FAILED": "#dc3545",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">{}</span>',
            colors.get(obj.generation_status, "#6c757d"),
            obj.get_generation_status_display(),
        )

    status_badge.short_description = "Status"

    def file_info(self, obj):
        if obj.generation_status == "COMPLETED" and obj.file_path:
            size_mb = obj.file_size / (1024 * 1024) if obj.file_size else 0
            return format_html(
                '<span style="color: green;">✓ Available</span><br><small>{:.1f} MB</small>',
                size_mb,
            )
        elif obj.generation_status == "FAILED":
            return format_html('<span style="color: red;">✗ Failed</span>')
        return format_html('<span style="color: gray;">Not Available</span>')

    file_info.short_description = "File"

    def generation_time(self, obj):
        if obj.completed_at:
            duration = obj.completed_at - obj.created_at
            return format_html(
                "<strong>{}</strong><br><small>{} sec</small>",
                obj.completed_at.strftime("%H:%M:%S"),
                duration.total_seconds(),
            )
        return format_html('<span style="color: gray;">-</span>')

    generation_time.short_description = "Generated"

    def regenerate_reports(self, request, queryset):
        from .services import PayrollReportingService

        for report in queryset:
            try:
                if report.report_type == "MONTHLY_SUMMARY":
                    PayrollReportingService.generate_monthly_payroll_report(
                        str(report.payroll_period.id),
                        request.user,
                        report.report_format,
                    )
                    messages.success(
                        request, f"Regenerated report {report.report_name}"
                    )
                else:
                    messages.warning(
                        request,
                        f"Cannot regenerate {report.report_name} - unsupported type",
                    )
            except Exception as e:
                messages.error(
                    request, f"Error regenerating {report.report_name}: {str(e)}"
                )

    regenerate_reports.short_description = "Regenerate selected reports"

    def download_reports(self, request, queryset):
        import zipfile
        from django.http import HttpResponse
        import os

        response = HttpResponse(content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="payroll_reports.zip"'

        with zipfile.ZipFile(response, "w") as zip_file:
            for report in queryset:
                if (
                    report.generation_status == "COMPLETED"
                    and report.file_path
                    and os.path.exists(report.file_path)
                ):
                    zip_file.write(report.file_path, os.path.basename(report.file_path))

        return response

    download_reports.short_description = "Download selected reports as ZIP"

    def delete_report_files(self, request, queryset):
        import os

        deleted_count = 0
        for report in queryset:
            if report.file_path and os.path.exists(report.file_path):
                try:
                    os.remove(report.file_path)
                    report.file_path = ""
                    report.file_size = 0
                    report.save()
                    deleted_count += 1
                except Exception as e:
                    messages.error(
                        request,
                        f"Error deleting file for {report.report_name}: {str(e)}",
                    )

        if deleted_count > 0:
            messages.success(request, f"Deleted {deleted_count} report files")

    delete_report_files.short_description = "Delete report files (keep records)"

    def has_add_permission(self, request):
        return PayrollAccessControl.can_export_payroll(request.user)

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_view_payroll_reports(request.user)

    def has_delete_permission(self, request, obj=None):
        return PayrollAccessControl.can_export_payroll(request.user)


class PayslipInline(admin.TabularInline):
    model = Payslip
    extra = 0
    fields = ["employee", "status", "basic_salary", "gross_salary", "net_salary"]
    readonly_fields = ["employee", "basic_salary", "gross_salary", "net_salary"]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class PayrollDepartmentSummaryInline(admin.TabularInline):
    model = PayrollDepartmentSummary
    extra = 0
    fields = [
        "department",
        "employee_count",
        "total_gross_salary",
        "total_net_salary",
        "average_salary",
    ]
    readonly_fields = [
        "department",
        "employee_count",
        "total_gross_salary",
        "total_net_salary",
        "average_salary",
    ]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class PayrollBankTransferInline(admin.TabularInline):
    model = PayrollBankTransfer
    extra = 0
    fields = [
        "batch_reference",
        "status",
        "total_employees",
        "total_amount",
        "generated_at",
    ]
    readonly_fields = [
        "batch_reference",
        "total_employees",
        "total_amount",
        "generated_at",
    ]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class SalaryAdvanceInline(admin.TabularInline):
    model = SalaryAdvance
    extra = 0
    fields = [
        "advance_type",
        "amount",
        "outstanding_amount",
        "status",
        "requested_date",
    ]
    readonly_fields = ["amount", "outstanding_amount", "requested_date"]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class PayrollAuditLogInline(admin.TabularInline):
    model = PayrollAuditLog
    extra = 0
    fields = ["action_type", "user", "description", "created_at"]
    readonly_fields = ["action_type", "user", "description", "created_at"]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class PayrollConfigurationInline(admin.TabularInline):
    model = PayrollConfiguration
    extra = 0
    fields = [
        "configuration_type",
        "configuration_key",
        "configuration_value",
        "is_active",
    ]
    readonly_fields = ["configuration_type", "configuration_key"]
    can_delete = False
    show_change_link = True


class PayrollReportInline(admin.TabularInline):
    model = PayrollReport
    extra = 0
    fields = ["report_type", "report_format", "generation_status", "completed_at"]
    readonly_fields = [
        "report_type",
        "report_format",
        "generation_status",
        "completed_at",
    ]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class PayrollDashboardAdmin(admin.ModelAdmin):
    change_list_template = "admin/payroll/dashboard.html"

    def changelist_view(self, request, extra_context=None):
        from .services import PayrollDashboardService

        try:
            dashboard_data = PayrollDashboardService.get_dashboard_overview(
                request.user
            )

            extra_context = extra_context or {}
            extra_context.update(
                {
                    "dashboard_data": dashboard_data,
                    "title": "Payroll Dashboard",
                    "has_add_permission": False,
                    "has_change_permission": False,
                    "has_delete_permission": False,
                }
            )
        except Exception as e:
            messages.error(request, f"Error loading dashboard: {str(e)}")
            extra_context = {"error": str(e)}

        return super().changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_view_payroll_dashboard(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


class PayrollAnalyticsAdmin(admin.ModelAdmin):
    change_list_template = "admin/payroll/analytics.html"

    def changelist_view(self, request, extra_context=None):
        from .services import PayrollAnalyticsService

        try:
            months = int(request.GET.get("months", 12))
            analytics_data = PayrollAnalyticsService.get_payroll_trends(
                request.user, months
            )

            extra_context = extra_context or {}
            extra_context.update(
                {
                    "analytics_data": analytics_data,
                    "selected_months": months,
                    "title": "Payroll Analytics",
                    "has_add_permission": False,
                    "has_change_permission": False,
                    "has_delete_permission": False,
                }
            )
        except Exception as e:
            messages.error(request, f"Error loading analytics: {str(e)}")
            extra_context = {"error": str(e)}

        return super().changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_view_payroll_reports(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


class PayrollMaintenanceAdmin(admin.ModelAdmin):
    change_list_template = "admin/payroll/maintenance.html"

    def changelist_view(self, request, extra_context=None):
        from .services import PayrollMaintenanceService

        maintenance_actions = []

        if request.method == "POST":
            action = request.POST.get("action")

            try:
                if action == "cleanup_data":
                    days = int(request.POST.get("days", 365))
                    result = PayrollMaintenanceService.cleanup_expired_data(
                        request.user, days
                    )
                    messages.success(request, f"Cleanup completed: {result}")

                elif action == "validate_integrity":
                    result = PayrollMaintenanceService.validate_data_integrity(
                        request.user
                    )
                    if result["is_valid"]:
                        messages.success(request, "Data integrity validation passed")
                    else:
                        messages.warning(
                            request, f"Found {result['issues_found']} integrity issues"
                        )

                elif action == "recalculate_totals":
                    period_id = request.POST.get("period_id")
                    if period_id:
                        PayrollMaintenanceService.recalculate_period_totals(
                            period_id, request.user
                        )
                        messages.success(request, "Period totals recalculated")

            except Exception as e:
                messages.error(request, f"Maintenance action failed: {str(e)}")

        try:
            integrity_status = PayrollMaintenanceService.validate_data_integrity(
                request.user
            )
            recent_periods = PayrollPeriod.objects.order_by("-year", "-month")[:10]

            extra_context = extra_context or {}
            extra_context.update(
                {
                    "integrity_status": integrity_status,
                    "recent_periods": recent_periods,
                    "title": "Payroll Maintenance",
                    "has_add_permission": False,
                    "has_change_permission": False,
                    "has_delete_permission": False,
                }
            )
        except Exception as e:
            messages.error(request, f"Error loading maintenance data: {str(e)}")
            extra_context = {"error": str(e)}

        return super().changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_configure_payroll(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


class PayrollBulkOperationsAdmin(admin.ModelAdmin):
    change_list_template = "admin/payroll/bulk_operations.html"

    def changelist_view(self, request, extra_context=None):
        from .services import PayslipCalculationService, PayrollPeriodService

        if request.method == "POST":
            operation = request.POST.get("operation")

            try:
                if operation == "bulk_calculate":
                    period_id = request.POST.get("period_id")
                    employee_ids = request.POST.getlist("employee_ids")

                    if period_id:
                        result = PayslipCalculationService.bulk_calculate_payslips(
                            period_id, request.user, employee_ids
                        )
                        messages.success(
                            request,
                            f"Calculated {len(result['successful'])} payslips, {len(result['failed'])} failed",
                        )

                elif operation == "bulk_approve":
                    payslip_ids = request.POST.getlist("payslip_ids")

                    for payslip_id in payslip_ids:
                        try:
                            payslip = Payslip.objects.get(id=payslip_id)
                            if payslip.status == "CALCULATED":
                                payslip.approve(request.user)
                        except Exception as e:
                            messages.error(
                                request,
                                f"Error approving payslip {payslip_id}: {str(e)}",
                            )

                    messages.success(
                        request, f"Processed {len(payslip_ids)} payslips for approval"
                    )

                elif operation == "period_processing":
                    period_id = request.POST.get("period_id")
                    action = request.POST.get("period_action")

                    if period_id and action:
                        if action == "start":
                            PayrollPeriodService.start_processing(
                                period_id, request.user
                            )
                            messages.success(request, "Started period processing")
                        elif action == "complete":
                            PayrollPeriodService.complete_processing(
                                period_id, request.user
                            )
                            messages.success(request, "Completed period processing")
                        elif action == "approve":
                            PayrollPeriodService.approve_payroll(
                                period_id, request.user
                            )
                            messages.success(request, "Approved payroll period")

            except Exception as e:
                messages.error(request, f"Bulk operation failed: {str(e)}")

        try:
            draft_periods = PayrollPeriod.objects.filter(
                status__in=["DRAFT", "PROCESSING"]
            )
            draft_payslips = Payslip.objects.filter(
                status="DRAFT", payroll_period__status__in=["DRAFT", "PROCESSING"]
            )
            calculated_payslips = Payslip.objects.filter(
                status="CALCULATED",
                payroll_period__status__in=["PROCESSING", "COMPLETED"],
            )

            extra_context = extra_context or {}
            extra_context.update(
                {
                    "draft_periods": draft_periods,
                    "draft_payslips": draft_payslips[:100],
                    "calculated_payslips": calculated_payslips[:100],
                    "title": "Bulk Operations",
                    "has_add_permission": False,
                    "has_change_permission": False,
                    "has_delete_permission": False,
                }
            )
        except Exception as e:
            messages.error(request, f"Error loading bulk operations data: {str(e)}")
            extra_context = {"error": str(e)}

        return super().changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_process_payroll(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


class PayrollSystemStatusAdmin(admin.ModelAdmin):
    change_list_template = "admin/payroll/system_status.html"

    def changelist_view(self, request, extra_context=None):
        from .services import PayrollServiceManager

        try:
            system_health = PayrollServiceManager.get_system_health()
            service_status = PayrollServiceManager.get_service_status()

            recent_errors = PayrollAuditLog.objects.filter(
                action_type__in=[
                    "CALCULATION_ERROR",
                    "PROCESSING_ERROR",
                    "SYSTEM_ERROR",
                ]
            ).order_by("-created_at")[:10]

            active_periods = PayrollPeriod.objects.filter(
                status__in=["DRAFT", "PROCESSING"]
            ).count()
            pending_advances = SalaryAdvance.objects.filter(status="PENDING").count()
            failed_transfers = PayrollBankTransfer.objects.filter(
                status="FAILED"
            ).count()

            extra_context = extra_context or {}
            extra_context.update(
                {
                    "system_health": system_health,
                    "service_status": service_status,
                    "recent_errors": recent_errors,
                    "active_periods": active_periods,
                    "pending_advances": pending_advances,
                    "failed_transfers": failed_transfers,
                    "title": "System Status",
                    "has_add_permission": False,
                    "has_change_permission": False,
                    "has_delete_permission": False,
                }
            )
        except Exception as e:
            messages.error(request, f"Error loading system status: {str(e)}")
            extra_context = {"error": str(e)}

        return super().changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return PayrollAccessControl.can_view_payroll_dashboard(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


class PayrollAdminSite(admin.AdminSite):
    site_header = "Payroll Management System"
    site_title = "Payroll Admin"
    index_title = "Payroll Administration"
    site_url = "/payroll/"

    def index(self, request, extra_context=None):
        from .services import PayrollDashboardService

        extra_context = extra_context or {}

        try:
            if PayrollAccessControl.can_view_payroll_dashboard(request.user):
                dashboard_data = PayrollDashboardService.get_dashboard_overview(
                    request.user
                )
                extra_context["dashboard_data"] = dashboard_data

            extra_context.update(
                {
                    "quick_stats": self.get_quick_stats(request.user),
                    "recent_activities": self.get_recent_activities(request.user),
                    "system_alerts": self.get_system_alerts(request.user),
                }
            )
        except Exception as e:
            extra_context["error"] = str(e)

        return super().index(request, extra_context)

    def get_quick_stats(self, user):
        if not PayrollAccessControl.can_view_payroll_dashboard(user):
            return {}

        try:
            current_year = timezone.now().year
            current_month = timezone.now().month

            return {
                "active_employees": CustomUser.active.filter(status="ACTIVE").count(),
                "current_period_exists": PayrollPeriod.objects.filter(
                    year=current_year, month=current_month
                ).exists(),
                "pending_advances": SalaryAdvance.objects.filter(
                    status="PENDING"
                ).count(),
                "draft_payslips": Payslip.objects.filter(status="DRAFT").count(),
                "processing_periods": PayrollPeriod.objects.filter(
                    status="PROCESSING"
                ).count(),
            }
        except Exception:
            return {}

    def get_recent_activities(self, user):
        if not PayrollAccessControl.can_view_payroll_reports(user):
            return []

        try:
            return PayrollAuditLog.objects.select_related("user", "employee").order_by(
                "-created_at"
            )[:10]
        except Exception:
            return []

    def get_system_alerts(self, user):
        alerts = []

        try:
            if PayrollAccessControl.can_view_payroll_dashboard(user):
                overdue_advances = SalaryAdvance.objects.filter(
                    status="ACTIVE", is_overdue=True
                ).count()
                if overdue_advances > 0:
                    alerts.append(
                        {
                            "type": "warning",
                            "message": f"{overdue_advances} salary advances are overdue",
                            "action_url": "/admin/payroll/salaryadvance/?status=ACTIVE",
                        }
                    )

                failed_transfers = PayrollBankTransfer.objects.filter(
                    status="FAILED"
                ).count()
                if failed_transfers > 0:
                    alerts.append(
                        {
                            "type": "error",
                            "message": f"{failed_transfers} bank transfers have failed",
                            "action_url": "/admin/payroll/payrollbanktransfer/?status=FAILED",
                        }
                    )

                incomplete_periods = PayrollPeriod.objects.filter(
                    status="PROCESSING"
                ).count()
                if incomplete_periods > 0:
                    alerts.append(
                        {
                            "type": "info",
                            "message": f"{incomplete_periods} payroll periods are being processed",
                            "action_url": "/admin/payroll/payrollperiod/?status=PROCESSING",
                        }
                    )
        except Exception:
            pass

        return alerts


payroll_admin_site = PayrollAdminSite(name="payroll_admin")


def export_to_excel(modeladmin, request, queryset):
    from django.http import HttpResponse
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{modeladmin.model._meta.verbose_name_plural}.xlsx"'
    )

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = modeladmin.model._meta.verbose_name_plural

    fields = [
        field.name
        for field in modeladmin.model._meta.fields
        if not field.name.endswith("_ptr")
    ]
    headers = [modeladmin.model._meta.get_field(field).verbose_name for field in fields]

    header_font = Font(bold=True)
    header_fill = PatternFill(
        start_color="366092", end_color="366092", fill_type="solid"
    )

    for col_num, header in enumerate(headers, 1):
        cell = worksheet.cell(row=1, column=col_num, value=header)
        cell.font = header_font
        cell.fill = header_fill

    for row_num, obj in enumerate(queryset, 2):
        for col_num, field in enumerate(fields, 1):
            value = getattr(obj, field)
            if hasattr(value, "strftime"):
                value = value.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(value, Decimal):
                value = float(value)
            worksheet.cell(row=row_num, column=col_num, value=value)

    workbook.save(response)
    return response


export_to_excel.short_description = "Export selected items to Excel"


def mark_as_processed(modeladmin, request, queryset):
    updated = 0
    for obj in queryset:
        if hasattr(obj, "status") and obj.status in ["PENDING", "GENERATED", "SENT"]:
            if hasattr(obj, "mark_as_processed"):
                obj.mark_as_processed(request.user)
                updated += 1
            elif hasattr(obj, "status"):
                obj.status = "PROCESSED"
                obj.save()
                updated += 1

    messages.success(request, f"Marked {updated} items as processed")


mark_as_processed.short_description = "Mark selected items as processed"


def recalculate_totals(modeladmin, request, queryset):
    updated = 0
    for obj in queryset:
        try:
            if hasattr(obj, "calculate_totals"):
                obj.calculate_totals()
                updated += 1
            elif hasattr(obj, "calculate_summary"):
                obj.calculate_summary()
                updated += 1
            elif hasattr(obj, "calculate_period_totals"):
                obj.calculate_period_totals()
                updated += 1
        except Exception as e:
            messages.error(request, f"Error recalculating {obj}: {str(e)}")

    if updated > 0:
        messages.success(request, f"Recalculated totals for {updated} items")


recalculate_totals.short_description = "Recalculate totals for selected items"


def generate_audit_report(modeladmin, request, queryset):
    from django.http import HttpResponse
    import csv
    from datetime import datetime

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="audit_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    )

    writer = csv.writer(response)
    writer.writerow(
        ["Model", "Object ID", "Object Name", "Action", "User", "Timestamp"]
    )

    for obj in queryset:
        audit_logs = PayrollAuditLog.objects.filter(
            **{f"{modeladmin.model._meta.model_name}": obj}
        ).order_by("-created_at")[:10]

        for log in audit_logs:
            writer.writerow(
                [
                    modeladmin.model._meta.verbose_name,
                    str(obj.id),
                    str(obj),
                    log.action_type,
                    log.user.get_full_name() if log.user else "System",
                    log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                ]
            )

    return response


generate_audit_report.short_description = "Generate audit report for selected items"


class PayrollAdminMixin:
    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))

        if obj and hasattr(obj, "status"):
            if obj.status in ["APPROVED", "PAID", "COMPLETED"]:
                readonly_fields.extend(
                    [
                        field.name
                        for field in obj._meta.fields
                        if field.name not in readonly_fields
                    ]
                )

        return readonly_fields

    def get_actions(self, request):
        actions = super().get_actions(request)

        if PayrollAccessControl.can_export_payroll(request.user):
            actions["export_to_excel"] = (
                export_to_excel,
                "export_to_excel",
                export_to_excel.short_description,
            )

        if PayrollAccessControl.can_process_payroll(request.user):
            actions["mark_as_processed"] = (
                mark_as_processed,
                "mark_as_processed",
                mark_as_processed.short_description,
            )
            actions["recalculate_totals"] = (
                recalculate_totals,
                "recalculate_totals",
                recalculate_totals.short_description,
            )

        if PayrollAccessControl.can_view_payroll_reports(request.user):
            actions["generate_audit_report"] = (
                generate_audit_report,
                "generate_audit_report",
                generate_audit_report.short_description,
            )

        return actions

    def save_model(self, request, obj, form, change):
        if not change:
            if hasattr(obj, "created_by"):
                obj.created_by = request.user

        if hasattr(obj, "updated_by"):
            obj.updated_by = request.user

        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)

        if not PayrollAccessControl.can_view_all_payroll(request.user):
            if hasattr(self.model, "employee"):
                if request.user.role and request.user.role.name == "MANAGER":
                    queryset = queryset.filter(
                        employee__department=request.user.department
                    )
                elif not PayrollAccessControl.can_process_payroll(request.user):
                    queryset = queryset.filter(employee=request.user)

        return queryset


class PayrollModelAdmin(PayrollAdminMixin, admin.ModelAdmin):
    pass


class PayrollTabularInline(PayrollAdminMixin, admin.TabularInline):
    pass


class PayrollStackedInline(PayrollAdminMixin, admin.StackedInline):
    pass


def register_payroll_models():
    models_to_register = [
        (PayrollPeriod, PayrollPeriodAdmin),
        (Payslip, PayslipAdmin),
        (SalaryAdvance, SalaryAdvanceAdmin),
        (PayrollDepartmentSummary, PayrollDepartmentSummaryAdmin),
        (PayrollConfiguration, PayrollConfigurationAdmin),
        (PayrollBankTransfer, PayrollBankTransferAdmin),
        (PayrollAuditLog, PayrollAuditLogAdmin),
        (PayrollReport, PayrollReportAdmin),
    ]

    for model, admin_class in models_to_register:
        try:
            admin.site.register(model, admin_class)
            payroll_admin_site.register(model, admin_class)
        except admin.sites.AlreadyRegistered:
            pass


def register_custom_views():
    custom_views = [
        ("Dashboard", PayrollDashboardAdmin),
        ("Analytics", PayrollAnalyticsAdmin),
        ("Maintenance", PayrollMaintenanceAdmin),
        ("Bulk Operations", PayrollBulkOperationsAdmin),
        ("System Status", PayrollSystemStatusAdmin),
    ]

    for name, admin_class in custom_views:
        try:

            class DummyModel:
                class _meta:
                    verbose_name = name
                    verbose_name_plural = name
                    app_label = "payroll"

            admin_instance = admin_class(DummyModel, payroll_admin_site)
            payroll_admin_site._registry[DummyModel] = admin_instance
        except Exception:
            pass


class PayrollAdminConfig:
    @staticmethod
    def setup_admin():
        register_payroll_models()
        register_custom_views()

        admin.site.site_header = "HR Management System"
        admin.site.site_title = "HR Admin"
        admin.site.index_title = "HR Administration"

        payroll_admin_site.site_header = "Payroll Management System"
        payroll_admin_site.site_title = "Payroll Admin"
        payroll_admin_site.index_title = "Payroll Administration"

    @staticmethod
    def get_admin_urls():
        from django.urls import path, include

        return [
            path("payroll-admin/", payroll_admin_site.urls),
        ]


def customize_admin_interface():
    admin.site.enable_nav_sidebar = True

    def get_app_list(self, request):
        app_list = super(admin.AdminSite, self).get_app_list(request)

        for app in app_list:
            if app["app_label"] == "payroll":
                app["models"].sort(
                    key=lambda x: {
                        "PayrollPeriod": 1,
                        "Payslip": 2,
                        "SalaryAdvance": 3,
                        "PayrollDepartmentSummary": 4,
                        "PayrollConfiguration": 5,
                        "PayrollBankTransfer": 6,
                        "PayrollAuditLog": 7,
                        "PayrollReport": 8,
                    }.get(x["object_name"], 999)
                )

        return app_list

    admin.AdminSite.get_app_list = get_app_list


def setup_payroll_admin():
    PayrollAdminConfig.setup_admin()
    customize_admin_interface()


class PayrollAdminUtils:
    @staticmethod
    def get_model_permissions(user, model):
        return {
            "add": PayrollAccessControl.can_process_payroll(user),
            "change": PayrollAccessControl.can_process_payroll(user),
            "delete": PayrollAccessControl.can_process_payroll(user),
            "view": PayrollAccessControl.can_view_payroll(user),
        }

    @staticmethod
    def format_currency(amount):
        return f"LKR {amount:,.2f}" if amount else "LKR 0.00"

    @staticmethod
    def format_percentage(value):
        return f"{value:.1f}%" if value else "0.0%"

    @staticmethod
    def get_status_color(status):
        colors = {
            "DRAFT": "#6c757d",
            "PENDING": "#ffc107",
            "PROCESSING": "#007bff",
            "CALCULATED": "#17a2b8",
            "APPROVED": "#28a745",
            "COMPLETED": "#28a745",
            "ACTIVE": "#28a745",
            "PAID": "#6f42c1",
            "CANCELLED": "#dc3545",
            "FAILED": "#dc3545",
            "GENERATED": "#007bff",
            "SENT": "#ffc107",
        }
        return colors.get(status, "#6c757d")

PayrollPeriodAdmin.inlines = [
    PayslipInline,
    PayrollDepartmentSummaryInline,
    PayrollBankTransferInline,
    PayrollAuditLogInline,
    PayrollReportInline,
]
PayslipAdmin.inlines = [PayrollAuditLogInline]
SalaryAdvanceAdmin.inlines = [PayrollAuditLogInline]
PayrollDepartmentSummaryAdmin.inlines = [PayrollAuditLogInline]
PayrollConfigurationAdmin.inlines = [PayrollAuditLogInline]
PayrollBankTransferAdmin.inlines = [PayrollAuditLogInline]


class PayrollPeriodAdmin(PayrollPeriodAdmin):
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj and obj.status in ["APPROVED", "PAID"]:
            for field_name in form.base_fields:
                if field_name not in ["status"]:
                    form.base_fields[field_name].disabled = True
        return form

    def response_change(self, request, obj):
        if "_start_processing" in request.POST:
            try:
                PayrollPeriodService.start_processing(str(obj.id), request.user)
                messages.success(request, f"Started processing {obj.period_name}")
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect(request.get_full_path())

        if "_complete_processing" in request.POST:
            try:
                PayrollPeriodService.complete_processing(str(obj.id), request.user)
                messages.success(request, f"Completed processing {obj.period_name}")
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect(request.get_full_path())

        if "_approve_period" in request.POST:
            try:
                PayrollPeriodService.approve_payroll(str(obj.id), request.user)
                messages.success(request, f"Approved {obj.period_name}")
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect(request.get_full_path())

        return super().response_change(request, obj)


class PayslipAdmin(PayslipAdmin):
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj and obj.status in ["APPROVED", "PAID"]:
            for field_name in form.base_fields:
                if field_name not in ["status"]:
                    form.base_fields[field_name].disabled = True
        return form

    def response_change(self, request, obj):
        if "_calculate_payslip" in request.POST:
            try:
                PayslipCalculationService.calculate_single_payslip(
                    str(obj.id), request.user
                )
                messages.success(
                    request, f"Calculated payslip for {obj.employee.get_full_name()}"
                )
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect(request.get_full_path())

        if "_approve_payslip" in request.POST:
            try:
                if obj.status == "CALCULATED":
                    obj.approve(request.user)
                    messages.success(
                        request, f"Approved payslip for {obj.employee.get_full_name()}"
                    )
                else:
                    messages.error(request, "Can only approve calculated payslips")
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect(request.get_full_path())

        return super().response_change(request, obj)


class SalaryAdvanceAdmin(SalaryAdvanceAdmin):
    def response_change(self, request, obj):
        if "_approve_advance" in request.POST:
            try:
                from .services import SalaryAdvanceService

                SalaryAdvanceService.approve_advance(str(obj.id), request.user)
                messages.success(request, f"Approved advance {obj.reference_number}")
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect(request.get_full_path())

        if "_activate_advance" in request.POST:
            try:
                from .services import SalaryAdvanceService

                SalaryAdvanceService.activate_advance(str(obj.id), request.user)
                messages.success(request, f"Activated advance {obj.reference_number}")
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect(request.get_full_path())

        return super().response_change(request, obj)


class PayrollBankTransferAdmin(PayrollBankTransferAdmin):
    def response_change(self, request, obj):
        if "_generate_file" in request.POST:
            try:
                from .services import BankTransferService

                BankTransferService.generate_bank_transfer_file(
                    str(obj.payroll_period.id), request.user
                )
                messages.success(
                    request, f"Generated bank file for {obj.batch_reference}"
                )
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect(request.get_full_path())

        if "_mark_sent" in request.POST:
            try:
                from .services import BankTransferService

                BankTransferService.update_transfer_status(
                    str(obj.id), "SENT", request.user
                )
                messages.success(request, f"Marked {obj.batch_reference} as sent")
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect(request.get_full_path())

        return super().response_change(request, obj)


admin.site.register(PayrollPeriod, PayrollPeriodAdmin)
admin.site.register(Payslip, PayslipAdmin)
admin.site.register(SalaryAdvance, SalaryAdvanceAdmin)
admin.site.register(PayrollDepartmentSummary, PayrollDepartmentSummaryAdmin)
admin.site.register(PayrollConfiguration, PayrollConfigurationAdmin)
admin.site.register(PayrollBankTransfer, PayrollBankTransferAdmin)
admin.site.register(PayrollAuditLog, PayrollAuditLogAdmin)
admin.site.register(PayrollReport, PayrollReportAdmin)


payroll_admin_site.register(PayrollPeriod, PayrollPeriodAdmin)
payroll_admin_site.register(Payslip, PayslipAdmin)
payroll_admin_site.register(SalaryAdvance, SalaryAdvanceAdmin)
payroll_admin_site.register(PayrollDepartmentSummary, PayrollDepartmentSummaryAdmin)
payroll_admin_site.register(PayrollConfiguration, PayrollConfigurationAdmin)
payroll_admin_site.register(PayrollBankTransfer, PayrollBankTransferAdmin)
payroll_admin_site.register(PayrollAuditLog, PayrollAuditLogAdmin)
payroll_admin_site.register(PayrollReport, PayrollReportAdmin)


admin.site.site_header = "HR Management System - Payroll Module"
admin.site.site_title = "Payroll Admin"
admin.site.index_title = "Payroll Management Dashboard"


def admin_view_decorator(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("admin:login")

        if not PayrollAccessControl.can_view_payroll_dashboard(request.user):
            messages.error(request, "You don't have permission to access payroll admin")
            return redirect("admin:index")

        return view_func(request, *args, **kwargs)

    return wrapper


class PayrollAdminMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/payroll/"):
            if request.user.is_authenticated:
                if not PayrollAccessControl.can_view_payroll(request.user):
                    messages.error(request, "Access denied to payroll module")
                    return redirect("/admin/")

        response = self.get_response(request)
        return response


def setup_admin_permissions():
    original_has_perm = admin.ModelAdmin.has_module_permission

    def has_module_permission(self, request):
        if self.model._meta.app_label == "payroll":
            return PayrollAccessControl.can_view_payroll(request.user)
        return original_has_perm(self, request)

    admin.ModelAdmin.has_module_permission = has_module_permission


def customize_admin_templates():
    admin.site.index_template = "admin/payroll_index.html"
    admin.site.app_index_template = "admin/payroll_app_index.html"


def register_admin_actions():
    def make_active(modeladmin, request, queryset):
        if hasattr(queryset.model, "is_active"):
            updated = queryset.update(is_active=True)
            messages.success(request, f"Activated {updated} items")

    make_active.short_description = "Activate selected items"

    def make_inactive(modeladmin, request, queryset):
        if hasattr(queryset.model, "is_active"):
            updated = queryset.update(is_active=False)
            messages.success(request, f"Deactivated {updated} items")

    make_inactive.short_description = "Deactivate selected items"

    admin.site.add_action(make_active)
    admin.site.add_action(make_inactive)
    admin.site.add_action(export_to_excel)


def initialize_payroll_admin():
    setup_admin_permissions()
    customize_admin_templates()
    register_admin_actions()
    setup_payroll_admin()


try:
    initialize_payroll_admin()
except Exception as e:
    import logging

    logger = logging.getLogger(__name__)
    logger.error(f"Error initializing payroll admin: {str(e)}")


class PayrollAdminAutoComplete:
    @staticmethod
    def get_employee_autocomplete(request):
        term = request.GET.get("term", "")
        employees = CustomUser.active.filter(
            Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(employee_code__icontains=term)
        )[:10]

        return [
            {"id": emp.id, "text": f"{emp.get_full_name()} ({emp.employee_code})"}
            for emp in employees
        ]

    @staticmethod
    def get_period_autocomplete(request):
        term = request.GET.get("term", "")
        periods = PayrollPeriod.objects.filter(
            Q(year__icontains=term) | Q(month__icontains=term)
        ).order_by("-year", "-month")[:10]

        return [{"id": period.id, "text": period.period_name} for period in periods]


if __name__ == "__main__":
    print("Payroll Admin module loaded successfully")
    print(f"Registered models: {len(admin.site._registry)} in main admin")
    print(f"Registered models: {len(payroll_admin_site._registry)} in payroll admin")
