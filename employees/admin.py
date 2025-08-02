from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q
from django.contrib.admin import SimpleListFilter
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.urls import path
from accounts.models import CustomUser, Department
from .models import EmployeeProfile, Education, Contract
from .forms import (
    EmployeeProfileForm,
    EducationForm,
    ContractForm,
    BulkEmployeeImportForm,
)
import csv
from datetime import date, timedelta


class EmploymentStatusFilter(SimpleListFilter):
    title = "Employment Status"
    parameter_name = "employment_status"

    def lookups(self, request, model_admin):
        return EmployeeProfile.EMPLOYMENT_STATUS_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(employment_status=self.value())
        return queryset


class GradeLevelFilter(SimpleListFilter):
    title = "Grade Level"
    parameter_name = "grade_level"

    def lookups(self, request, model_admin):
        return EmployeeProfile.GRADE_LEVELS

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(grade_level=self.value())
        return queryset


class DepartmentFilter(SimpleListFilter):
    title = "Department"
    parameter_name = "department"

    def lookups(self, request, model_admin):
        departments = Department.active.all()
        return [(dept.id, dept.name) for dept in departments]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(user__department_id=self.value())
        return queryset


class ProbationStatusFilter(SimpleListFilter):
    title = "Probation Status"
    parameter_name = "probation_status"

    def lookups(self, request, model_admin):
        return [
            ("on_probation", "On Probation"),
            ("probation_ending", "Probation Ending Soon"),
            ("confirmed", "Confirmed"),
        ]

    def queryset(self, request, queryset):
        today = timezone.now().date()
        if self.value() == "on_probation":
            return queryset.filter(
                employment_status="PROBATION", probation_end_date__gte=today
            )
        elif self.value() == "probation_ending":
            next_month = today + timedelta(days=30)
            return queryset.filter(
                employment_status="PROBATION",
                probation_end_date__lte=next_month,
                probation_end_date__gte=today,
            )
        elif self.value() == "confirmed":
            return queryset.filter(employment_status="CONFIRMED")
        return queryset


class EducationInline(admin.TabularInline):
    model = Education
    form = EducationForm
    extra = 0
    fields = [
        "education_level",
        "qualification",
        "institution",
        "field_of_study",
        "start_year",
        "completion_year",
        "grade_gpa",
        "certificate_file",
        "is_verified",
    ]
    readonly_fields = ["is_verified", "verified_by", "verified_at"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("verified_by")


class ContractInline(admin.TabularInline):
    model = Contract
    form = ContractForm
    extra = 0
    fields = [
        "contract_number",
        "contract_type",
        "status",
        "start_date",
        "end_date",
        "basic_salary",
        "is_active",
    ]
    readonly_fields = ["contract_number"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("department", "reporting_manager")
        )


class EmployeeProfileAdmin(admin.ModelAdmin):
    form = EmployeeProfileForm
    list_display = [
        "get_employee_code",
        "get_full_name",
        "get_email",
        "get_department",
        "get_role",
        "employment_status",
        "grade_level",
        "basic_salary",
        "get_probation_status",
        "is_active",
    ]
    list_filter = [
        EmploymentStatusFilter,
        GradeLevelFilter,
        DepartmentFilter,
        ProbationStatusFilter,
        "is_active",
        "created_at",
    ]
    search_fields = [
        "user__employee_code",
        "user__first_name",
        "user__last_name",
        "user__email",
        "user__phone_number",
    ]
    readonly_fields = [
        "get_employee_code",
        "get_full_name",
        "get_email",
        "get_phone_number",
        "get_date_of_birth",
        "get_gender",
        "get_department",
        "get_role",
        "get_job_title",
        "get_manager",
        "get_hire_date",
        "get_reporting_time",
        "get_shift_hours",
        "years_of_service",
        "is_on_probation",
        "created_at",
        "updated_at",
        "created_by",
    ]
    fieldsets = [
        (
            "Employee Information",
            {"fields": ["user", "get_employee_code", "get_full_name", "get_email"]},
        ),
        (
            "Basic Details",
            {
                "fields": [
                    "get_phone_number",
                    "get_date_of_birth",
                    "get_gender",
                    "get_department",
                    "get_role",
                    "get_job_title",
                    "get_manager",
                    "get_hire_date",
                ]
            },
        ),
        (
            "Employment Details",
            {
                "fields": [
                    "employment_status",
                    "grade_level",
                    "basic_salary",
                    "probation_end_date",
                    "confirmation_date",
                    "work_location",
                    "get_reporting_time",
                    "get_shift_hours",
                ]
            },
        ),
        (
            "Financial Information",
            {
                "fields": [
                    "bank_name",
                    "bank_account_number",
                    "bank_branch",
                    "tax_identification_number",
                ]
            },
        ),
        (
            "Personal Information",
            {"fields": ["marital_status", "spouse_name", "number_of_children"]},
        ),
        (
            "System Information",
            {
                "fields": [
                    "years_of_service",
                    "is_on_probation",
                    "is_active",
                    "created_at",
                    "updated_at",
                    "created_by",
                ],
                "classes": ["collapse"],
            },
        ),
    ]
    actions = [
        "mark_as_confirmed",
        "mark_as_inactive",
        "export_to_csv",
        "bulk_salary_update",
    ]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "user", "user__department", "user__role", "user__manager", "created_by"
            )
        )

    def get_employee_code(self, obj):
        return obj.user.employee_code

    get_employee_code.short_description = "Employee Code"
    get_employee_code.admin_order_field = "user__employee_code"

    def get_full_name(self, obj):
        return obj.user.get_full_name()

    get_full_name.short_description = "Full Name"
    get_full_name.admin_order_field = "user__first_name"

    def get_email(self, obj):
        return obj.user.email

    get_email.short_description = "Email"
    get_email.admin_order_field = "user__email"

    def get_phone_number(self, obj):
        return obj.user.phone_number or "-"

    get_phone_number.short_description = "Phone Number"

    def get_date_of_birth(self, obj):
        return obj.user.date_of_birth or "-"

    get_date_of_birth.short_description = "Date of Birth"

    def get_gender(self, obj):
        return obj.user.get_gender_display() if obj.user.gender else "-"

    get_gender.short_description = "Gender"

    def get_department(self, obj):
        if obj.user.department:
            return obj.user.department.name
        return "-"

    get_department.short_description = "Department"
    get_department.admin_order_field = "user__department__name"

    def get_role(self, obj):
        if obj.user.role:
            return obj.user.role.display_name
        return "-"

    get_role.short_description = "Role"
    get_role.admin_order_field = "user__role__display_name"

    def get_job_title(self, obj):
        return obj.user.job_title or "-"

    get_job_title.short_description = "Job Title"

    def get_manager(self, obj):
        if obj.user.manager:
            return obj.user.manager.get_full_name()
        return "-"

    get_manager.short_description = "Manager"

    def get_hire_date(self, obj):
        return obj.user.hire_date or "-"

    get_hire_date.short_description = "Hire Date"

    def get_reporting_time(self, obj):
        return obj.reporting_time

    get_reporting_time.short_description = "Reporting Time"

    def get_shift_hours(self, obj):
        return f"{obj.shift_hours} hours"

    get_shift_hours.short_description = "Shift Hours"

    def get_probation_status(self, obj):
        if obj.employment_status == "PROBATION":
            if obj.is_on_probation:
                days_left = (obj.probation_end_date - timezone.now().date()).days
                if days_left <= 7:
                    return format_html(
                        '<span style="color: red;">Ending in {} days</span>', days_left
                    )
                return format_html(
                    '<span style="color: orange;">Active ({} days left)</span>',
                    days_left,
                )
            return format_html('<span style="color: red;">Expired</span>')
        return format_html('<span style="color: green;">Confirmed</span>')

    get_probation_status.short_description = "Probation Status"

    def years_of_service(self, obj):
        return f"{obj.years_of_service:.1f} years"

    years_of_service.short_description = "Years of Service"

    def is_on_probation(self, obj):
        return obj.is_on_probation

    is_on_probation.boolean = True
    is_on_probation.short_description = "On Probation"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def mark_as_confirmed(self, request, queryset):
        updated = queryset.filter(employment_status="PROBATION").update(
            employment_status="CONFIRMED", confirmation_date=timezone.now().date()
        )
        self.message_user(
            request, f"{updated} employee(s) marked as confirmed.", messages.SUCCESS
        )

    mark_as_confirmed.short_description = "Mark selected employees as confirmed"

    def mark_as_inactive(self, request, queryset):
        updated = 0
        for obj in queryset:
            obj.soft_delete()
            updated += 1
        self.message_user(
            request, f"{updated} employee(s) marked as inactive.", messages.SUCCESS
        )

    mark_as_inactive.short_description = "Mark selected employees as inactive"

    def export_to_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="employees.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "Employee Code",
                "Full Name",
                "Email",
                "Phone Number",
                "Department",
                "Role",
                "Job Title",
                "Employment Status",
                "Grade Level",
                "Basic Salary",
                "Hire Date",
                "Probation End Date",
                "Manager",
            ]
        )

        for obj in queryset:
            writer.writerow(
                [
                    obj.user.employee_code,
                    obj.user.get_full_name(),
                    obj.user.email,
                    obj.user.phone_number or "",
                    obj.user.department.name if obj.user.department else "",
                    obj.user.role.display_name if obj.user.role else "",
                    obj.user.job_title or "",
                    obj.employment_status,
                    obj.grade_level,
                    obj.basic_salary,
                    obj.user.hire_date,
                    obj.probation_end_date,
                    obj.user.manager.get_full_name() if obj.user.manager else "",
                ]
            )

        return response

    export_to_csv.short_description = "Export selected employees to CSV"

    def bulk_salary_update(self, request, queryset):
        if "apply" in request.POST:
            percentage = float(request.POST.get("percentage", 0))
            if percentage:
                updated = 0
                for obj in queryset:
                    new_salary = obj.basic_salary * (1 + percentage / 100)
                    obj.basic_salary = new_salary
                    obj.save()
                    updated += 1

                self.message_user(
                    request,
                    f"Salary updated for {updated} employee(s) by {percentage}%.",
                    messages.SUCCESS,
                )
                return redirect(request.get_full_path())

        return render(
            request,
            "admin/employees/bulk_salary_update.html",
            {"employees": queryset, "action": "bulk_salary_update"},
        )

    bulk_salary_update.short_description = "Bulk update salary"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "bulk-import/",
                self.admin_site.admin_view(self.bulk_import_view),
                name="employees_employeeprofile_bulk_import",
            ),
            path(
                "export/",
                self.admin_site.admin_view(self.employeeprofile_export_view),
                name="employees_employeeprofile_export",
            ),
        ]
        return custom_urls + urls

    def bulk_import_view(self, request):
        if request.method == "POST":
            form = BulkEmployeeImportForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    csv_file = form.cleaned_data["csv_file"]
                    messages.success(request, "Employees imported successfully.")
                    return redirect("admin:employees_employeeprofile_changelist")
                except Exception as e:
                    messages.error(request, f"Import failed: {str(e)}")
        else:
            form = BulkEmployeeImportForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Bulk Import Employees",
            "form": form,
            "opts": self.model._meta,
        }
        return render(
            request, "admin/employees/employeeprofile_bulk_import.html", context
        )

    def employeeprofile_export_view(self, request):
        return self.export_to_csv(request, EmployeeProfile.objects.all())

class EducationAdmin(admin.ModelAdmin):
    form = EducationForm
    list_display = [
        "get_employee_code",
        "get_employee_name",
        "education_level",
        "qualification",
        "institution",
        "completion_year",
        "is_verified",
        "is_active",
    ]
    list_filter = [
        "education_level",
        "is_verified",
        "is_active",
        "completion_year",
        "created_at",
    ]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "qualification",
        "institution",
    ]
    readonly_fields = [
        "is_verified",
        "verified_by",
        "verified_at",
        "created_at",
        "updated_at",
        "created_by",
    ]
    fieldsets = [
        (
            "Education Information",
            {
                "fields": [
                    "employee",
                    "education_level",
                    "qualification",
                    "institution",
                    "field_of_study",
                ]
            },
        ),
        ("Timeline", {"fields": ["start_year", "completion_year", "grade_gpa"]}),
        ("Documentation", {"fields": ["certificate_file"]}),
        (
            "Verification",
            {
                "fields": ["is_verified", "verified_by", "verified_at"],
                "classes": ["collapse"],
            },
        ),
        (
            "System Information",
            {
                "fields": ["is_active", "created_at", "updated_at", "created_by"],
                "classes": ["collapse"],
            },
        ),
    ]
    actions = ["verify_education", "mark_as_inactive", "export_to_csv"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("employee", "verified_by", "created_by")
        )

    def get_employee_code(self, obj):
        return obj.employee.employee_code

    get_employee_code.short_description = "Employee Code"
    get_employee_code.admin_order_field = "employee__employee_code"

    def get_employee_name(self, obj):
        return obj.employee.get_full_name()

    get_employee_name.short_description = "Employee"
    get_employee_name.admin_order_field = "employee__first_name"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def verify_education(self, request, queryset):
        updated = 0
        for obj in queryset.filter(is_verified=False):
            obj.verify_education(request.user)
            updated += 1

        self.message_user(
            request, f"{updated} education record(s) verified.", messages.SUCCESS
        )

    verify_education.short_description = "Verify selected education records"

    def mark_as_inactive(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(
            request,
            f"{updated} education record(s) marked as inactive.",
            messages.SUCCESS,
        )

    mark_as_inactive.short_description = "Mark selected records as inactive"

    def export_to_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="education_records.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "Employee Code",
                "Employee Name",
                "Education Level",
                "Qualification",
                "Institution",
                "Field of Study",
                "Start Year",
                "Completion Year",
                "Grade/GPA",
                "Verified",
            ]
        )

        for obj in queryset:
            writer.writerow(
                [
                    obj.employee.employee_code,
                    obj.employee.get_full_name(),
                    obj.get_education_level_display(),
                    obj.qualification,
                    obj.institution,
                    obj.field_of_study,
                    obj.start_year,
                    obj.completion_year,
                    obj.grade_gpa,
                    "Yes" if obj.is_verified else "No",
                ]
            )

        return response

    export_to_csv.short_description = "Export selected records to CSV"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "bulk-verify/",
                self.admin_site.admin_view(self.bulk_verify_view),
                name="employees_education_bulk_verify",
            ),
            path(
                "export/",
                self.admin_site.admin_view(self.education_export_view),
                name="employees_education_export",
            ),
            path(
                "<int:education_id>/verify/",
                self.admin_site.admin_view(self.verify_individual_education),
                name="employees_education_verify",
            ),
        ]
        return custom_urls + urls

    def bulk_verify_view(self, request):
        unverified_records = Education.objects.filter(is_verified=False).select_related(
            "employee"
        )

        if request.method == "POST":
            selected_ids = request.POST.getlist("selected_records")
            if selected_ids:
                updated = 0
                for record_id in selected_ids:
                    try:
                        record = Education.objects.get(pk=record_id)
                        record.verify_education(request.user)
                        updated += 1
                    except Education.DoesNotExist:
                        continue

                messages.success(request, f"{updated} education record(s) verified.")
                return redirect("admin:employees_education_changelist")
            else:
                messages.warning(request, "No records selected for verification.")

        context = {
            **self.admin_site.each_context(request),
            "title": "Bulk Verify Education Records",
            "unverified_records": unverified_records,
            "opts": self.model._meta,
        }
        return render(request, "admin/employees/education_bulk_verify.html", context)

    def education_export_view(self, request):
        return self.export_to_csv(request, Education.objects.all())

    def verify_individual_education(self, request, education_id):
        education = get_object_or_404(Education, pk=education_id)

        if request.method == "POST":
            if not education.is_verified:
                education.verify_education(request.user)
                messages.success(
                    request,
                    f"Education record for {education.employee.get_full_name()} has been verified.",
                )
            else:
                messages.info(request, "This education record is already verified.")
            return redirect("admin:employees_education_changelist")

        context = {
            **self.admin_site.each_context(request),
            "title": f"Verify Education Record",
            "education": education,
            "opts": self.model._meta,
        }
        return render(
            request, "admin/employees/education_verify_individual.html", context
        )

class ContractAdmin(admin.ModelAdmin):
    form = ContractForm
    list_display = [
        "contract_number",
        "get_employee_code",
        "get_employee_name",
        "contract_type",
        "status",
        "start_date",
        "end_date",
        "get_days_remaining",
        "basic_salary",
        "is_active",
    ]
    list_filter = [
        "contract_type",
        "status",
        "is_active",
        "start_date",
        "end_date",
        "created_at",
    ]
    search_fields = [
        "contract_number",
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "job_title",
    ]
    readonly_fields = [
        "contract_number",
        "is_expired",
        "days_remaining",
        "contract_duration_days",
        "created_at",
        "updated_at",
        "created_by",
        "terminated_by",
        "termination_date",
    ]
    fieldsets = [
        (
            "Contract Information",
            {"fields": ["employee", "contract_number", "contract_type", "status"]},
        ),
        ("Contract Dates", {"fields": ["start_date", "end_date", "signed_date"]}),
        ("Job Details", {"fields": ["job_title", "department", "reporting_manager"]}),
        ("Compensation", {"fields": ["basic_salary", "working_hours"]}),
        (
            "Terms",
            {
                "fields": [
                    "terms_and_conditions",
                    "benefits",
                    "probation_period_months",
                    "notice_period_days",
                ]
            },
        ),
        ("Documentation", {"fields": ["contract_file"]}),
        (
            "Contract Status",
            {
                "fields": ["is_expired", "days_remaining", "contract_duration_days"],
                "classes": ["collapse"],
            },
        ),
        (
            "Termination Information",
            {
                "fields": ["terminated_by", "termination_date", "termination_reason"],
                "classes": ["collapse"],
            },
        ),
        (
            "System Information",
            {
                "fields": ["is_active", "created_at", "updated_at", "created_by"],
                "classes": ["collapse"],
            },
        ),
    ]
    actions = ["activate_contracts", "terminate_contracts", "export_to_csv"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("employee", "department", "reporting_manager", "created_by")
        )

    def get_employee_code(self, obj):
        return obj.employee.employee_code

    get_employee_code.short_description = "Employee Code"
    get_employee_code.admin_order_field = "employee__employee_code"

    def get_employee_name(self, obj):
        return obj.employee.get_full_name()

    get_employee_name.short_description = "Employee"
    get_employee_name.admin_order_field = "employee__first_name"

    def get_days_remaining(self, obj):
        if obj.days_remaining is None:
            return "Permanent"
        elif obj.days_remaining <= 0:
            return format_html('<span style="color: red;">Expired</span>')
        elif obj.days_remaining <= 30:
            return format_html(
                '<span style="color: orange;">{} days</span>', obj.days_remaining
            )
        return f"{obj.days_remaining} days"

    get_days_remaining.short_description = "Days Remaining"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def activate_contracts(self, request, queryset):
        updated = 0
        for obj in queryset.filter(status="DRAFT"):
            obj.activate_contract()
            updated += 1

        self.message_user(
            request, f"{updated} contract(s) activated.", messages.SUCCESS
        )

    activate_contracts.short_description = "Activate selected contracts"

    def terminate_contracts(self, request, queryset):
        updated = 0
        for obj in queryset.filter(status="ACTIVE"):
            obj.terminate_contract(request.user, "Bulk termination via admin")
            updated += 1

        self.message_user(
            request, f"{updated} contract(s) terminated.", messages.SUCCESS
        )

    terminate_contracts.short_description = "Terminate selected contracts"

    def export_to_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="contracts.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "Contract Number",
                "Employee Code",
                "Employee Name",
                "Contract Type",
                "Status",
                "Start Date",
                "End Date",
                "Basic Salary",
                "Job Title",
                "Department",
            ]
        )

        for obj in queryset:
            writer.writerow(
                [
                    obj.contract_number,
                    obj.employee.employee_code,
                    obj.employee.get_full_name(),
                    obj.get_contract_type_display(),
                    obj.get_status_display(),
                    obj.start_date,
                    obj.end_date,
                    obj.basic_salary,
                    obj.job_title,
                    obj.department.name if obj.department else "",
                ]
            )

        return response

    export_to_csv.short_description = "Export selected contracts to CSV"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "renewal-form/",
                self.admin_site.admin_view(self.contract_renewal_form),
                name="employees_contract_renewal_form",
            ),
            path(
                "expiry-report/",
                self.admin_site.admin_view(self.contract_expiry_report),
                name="employees_contract_expiry_report",
            ),
            path(
                "export/",
                self.admin_site.admin_view(self.contract_export_view),
                name="employees_contract_export",
            ),
            path(
                "<uuid:contract_id>/renew/",
                self.admin_site.admin_view(self.renew_contract),
                name="employees_contract_renew",
            ),
            path(
                "<uuid:contract_id>/terminate/",
                self.admin_site.admin_view(self.terminate_contract_view),
                name="employees_contract_terminate",
            ),
        ]
        return custom_urls + urls

    def contract_renewal_form(self, request):
        contracts_expiring = Contract.objects.filter(
            end_date__lte=timezone.now().date() + timedelta(days=90), status="ACTIVE"
        ).order_by("end_date")

        context = {
            **self.admin_site.each_context(request),
            "title": "Contract Renewal Form",
            "contracts_expiring": contracts_expiring,
            "opts": self.model._meta,
        }
        return render(request, "admin/employees/contract_renewal_form.html", context)

    def contract_expiry_report(self, request):
        contracts = Contract.objects.filter(end_date__isnull=False).order_by("end_date")
        today = timezone.now().date()

        expired = contracts.filter(end_date__lt=today)
        expiring_30 = contracts.filter(
            end_date__gte=today, end_date__lte=today + timedelta(days=30)
        )
        expiring_90 = contracts.filter(
            end_date__gte=today + timedelta(days=31),
            end_date__lte=today + timedelta(days=90),
        )

        context = {
            **self.admin_site.each_context(request),
            "title": "Contract Expiry Report",
            "expired_contracts": expired,
            "expiring_30_days": expiring_30,
            "expiring_90_days": expiring_90,
            "total_contracts": contracts.count(),
            "opts": self.model._meta,
        }
        return render(request, "admin/employees/contract_expiry_report.html", context)

    def contract_export_view(self, request):
        return self.export_to_csv(request, Contract.objects.all())

    def renew_contract(self, request, contract_id):
        contract = get_object_or_404(Contract, pk=contract_id)

        if request.method == "POST":
            messages.success(
                request, f"Contract {contract.contract_number} renewal initiated."
            )
            return redirect("admin:employees_contract_changelist")

        context = {
            **self.admin_site.each_context(request),
            "title": f"Renew Contract {contract.contract_number}",
            "contract": contract,
            "opts": self.model._meta,
        }
        return render(
            request, "admin/employees/contract_renew_individual.html", context
        )

    def terminate_contract_view(self, request, contract_id):
        contract = get_object_or_404(Contract, pk=contract_id)

        if request.method == "POST":
            termination_reason = request.POST.get(
                "termination_reason", "Terminated via admin"
            )
            contract.terminate_contract(request.user, termination_reason)
            messages.success(
                request, f"Contract {contract.contract_number} has been terminated."
            )
            return redirect("admin:employees_contract_changelist")

        context = {
            **self.admin_site.each_context(request),
            "title": f"Terminate Contract {contract.contract_number}",
            "contract": contract,
            "opts": self.model._meta,
        }
        return render(
            request, "admin/employees/contract_terminate_individual.html", context
        )


