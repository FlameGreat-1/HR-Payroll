from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django.db.models import Q, Count, Sum, Avg
from django.utils.html import format_html
from django.urls import reverse, path
from django.shortcuts import redirect
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.core.exceptions import ValidationError
from accounts.models import CustomUser, Department
from .models import (
    Attendance,
    AttendanceLog,
    AttendanceDevice,
    Shift,
    EmployeeShift,
    LeaveRequest,
    LeaveType,
    LeaveBalance,
    Holiday,
    MonthlyAttendanceSummary,
    AttendanceCorrection,
    AttendanceReport,
)
from .services import (
    AttendanceService,
    DeviceService,
    LeaveService,
    ReportService,
    ExcelService,
    StatisticsService,
    BulkOperationsService,
)
from .tasks import (
    sync_device_data,
    sync_all_devices,
    process_pending_attendance_logs,
    generate_monthly_summaries,
    import_attendance_from_excel,
)
from .utils import (
    ValidationHelper,
    EmployeeDataManager,
    TimeCalculator,
    get_current_date,
    get_current_datetime,
)
from .forms import (
    AttendanceForm,
    AttendanceLogForm,
    AttendanceDeviceForm,
    ShiftForm,
    EmployeeShiftAssignmentForm,
    LeaveRequestForm,
    LeaveApprovalForm,
    HolidayForm,
    AttendanceCorrectionForm,
    AttendanceReportForm,
)
from datetime import datetime, date, timedelta
import json


class DateRangeFilter(SimpleListFilter):
    title = "Date Range"
    parameter_name = "date_range"

    def lookups(self, request, model_admin):
        return (
            ("today", "Today"),
            ("yesterday", "Yesterday"),
            ("this_week", "This Week"),
            ("last_week", "Last Week"),
            ("this_month", "This Month"),
            ("last_month", "Last Month"),
            ("this_year", "This Year"),
        )

    def queryset(self, request, queryset):
        today = get_current_date()

        if self.value() == "today":
            return queryset.filter(date=today)
        elif self.value() == "yesterday":
            return queryset.filter(date=today - timedelta(days=1))
        elif self.value() == "this_week":
            start_week = today - timedelta(days=today.weekday())
            return queryset.filter(date__gte=start_week, date__lte=today)
        elif self.value() == "last_week":
            start_week = today - timedelta(days=today.weekday() + 7)
            end_week = start_week + timedelta(days=6)
            return queryset.filter(date__gte=start_week, date__lte=end_week)
        elif self.value() == "this_month":
            return queryset.filter(date__year=today.year, date__month=today.month)
        elif self.value() == "last_month":
            last_month = today.replace(day=1) - timedelta(days=1)
            return queryset.filter(
                date__year=last_month.year, date__month=last_month.month
            )
        elif self.value() == "this_year":
            return queryset.filter(date__year=today.year)

        return queryset


class DepartmentFilter(SimpleListFilter):
    title = "Department"
    parameter_name = "department"

    def lookups(self, request, model_admin):
        departments = Department.active.all()
        return [(dept.id, dept.name) for dept in departments]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(employee__department_id=self.value())
        return queryset


class AttendanceStatusFilter(SimpleListFilter):
    title = "Attendance Status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return (
            ("PRESENT", "Present"),
            ("ABSENT", "Absent"),
            ("LATE", "Late"),
            ("HALF_DAY", "Half Day"),
            ("ON_LEAVE", "On Leave"),
            ("HOLIDAY", "Holiday"),
            ("INCOMPLETE", "Incomplete"),
        )

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    form = AttendanceForm
    list_display = [
        "employee_info",
        "date",
        "status_badge",
        "first_in_time",
        "last_out_time",
        "total_work_hours",
        "overtime_hours",
        "late_minutes",
        "early_departure_minutes",
        "check_ins_count",
        "is_manual_entry",
    ]
    list_filter = [
        DateRangeFilter,
        DepartmentFilter,
        AttendanceStatusFilter,
        "is_manual_entry",
        "created_at",
    ]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "employee__email",
        "notes",
    ]
    readonly_fields = [
        "created_at",
        "updated_at",
        "total_work_time",
        "overtime",
        "late_minutes",
        "early_departure_minutes",
    ]
    fieldsets = (
        ("Employee Information", {"fields": ("employee", "date", "status")}),
        (
            "Check-in/Check-out Times",
            {
                "fields": (
                    ("check_in_1", "check_out_1"),
                    ("check_in_2", "check_out_2"),
                    ("check_in_3", "check_out_3"),
                    ("check_in_4", "check_out_4"),
                    ("check_in_5", "check_out_5"),
                    ("check_in_6", "check_out_6"),
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Calculated Fields",
            {
                "fields": (
                    "first_in_time",
                    "last_out_time",
                    "total_work_time",
                    "overtime",
                    "late_minutes",
                    "early_departure_minutes",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Additional Information",
            {
                "fields": ("notes", "is_manual_entry", "created_by"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    actions = [
        "mark_as_present",
        "mark_as_absent",
        "mark_as_late",
        "export_selected_excel",
        "bulk_approve_corrections",
        "generate_attendance_report",
        "sync_with_devices",
    ]
    date_hierarchy = "date"
    ordering = ["-date", "employee__employee_code"]
    list_per_page = 50
    list_max_show_all = 200

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("employee", "employee__department", "created_by")

    def employee_info(self, obj):
        if obj.employee:
            return format_html(
                "<strong>{}</strong><br/>" "<small>{} - {}</small>",
                obj.employee.get_full_name(),
                obj.employee.employee_code,
                (
                    obj.employee.department.name
                    if obj.employee.department
                    else "No Department"
                ),
            )
        return "No Employee"

    employee_info.short_description = "Employee"
    employee_info.admin_order_field = "employee__first_name"

    def status_badge(self, obj):
        colors = {
            "PRESENT": "green",
            "ABSENT": "red",
            "LATE": "orange",
            "HALF_DAY": "blue",
            "ON_LEAVE": "purple",
            "HOLIDAY": "gray",
            "INCOMPLETE": "darkred",
        }
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"
    status_badge.admin_order_field = "status"

    def total_work_hours(self, obj):
        if obj.total_work_time:
            hours = obj.total_work_time.total_seconds() / 3600
            return f"{hours:.2f}h"
        return "0.00h"

    total_work_hours.short_description = "Work Hours"

    def overtime_hours(self, obj):
        if obj.overtime:
            hours = obj.overtime.total_seconds() / 3600
            return format_html(
                '<span style="color: orange; font-weight: bold;">{:.2f}h</span>', hours
            )
        return "0.00h"

    overtime_hours.short_description = "Overtime"

    def check_ins_count(self, obj):
        count = 0
        for i in range(1, 7):
            if getattr(obj, f"check_in_{i}"):
                count += 1

        if count > 3:
            color = "red"
        elif count > 1:
            color = "orange"
        else:
            color = "green"

        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>', color, count
        )

    check_ins_count.short_description = "Check-ins"

    def mark_as_present(self, request, queryset):
        updated = 0
        for attendance in queryset:
            if attendance.status != "PRESENT":
                attendance.status = "PRESENT"
                attendance.save()
                updated += 1

        self.message_user(
            request,
            f"Successfully marked {updated} records as Present.",
            messages.SUCCESS,
        )

    mark_as_present.short_description = "Mark selected as Present"

    def mark_as_absent(self, request, queryset):
        updated = 0
        for attendance in queryset:
            if attendance.status != "ABSENT":
                attendance.status = "ABSENT"
                attendance.save()
                updated += 1

        self.message_user(
            request,
            f"Successfully marked {updated} records as Absent.",
            messages.SUCCESS,
        )

    mark_as_absent.short_description = "Mark selected as Absent"

    def mark_as_late(self, request, queryset):
        updated = 0
        for attendance in queryset:
            if attendance.status != "LATE":
                attendance.status = "LATE"
                attendance.save()
                updated += 1

        self.message_user(
            request, f"Successfully marked {updated} records as Late.", messages.SUCCESS
        )

    mark_as_late.short_description = "Mark selected as Late"

    def export_selected_excel(self, request, queryset):
        try:
            excel_data = ExcelService.export_attendance_to_excel(queryset, request.user)

            response = HttpResponse(
                excel_data,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="attendance_export_{get_current_date()}.xlsx"'
            )

            return response

        except Exception as e:
            self.message_user(request, f"Export failed: {str(e)}", messages.ERROR)

    export_selected_excel.short_description = "Export selected to Excel"

    def bulk_approve_corrections(self, request, queryset):
        try:
            corrections = AttendanceCorrection.objects.filter(
                attendance__in=queryset, status="PENDING"
            )

            approved_count = 0
            for correction in corrections:
                correction.approve(request.user)
                approved_count += 1

            self.message_user(
                request,
                f"Successfully approved {approved_count} corrections.",
                messages.SUCCESS,
            )

        except Exception as e:
            self.message_user(
                request, f"Bulk approval failed: {str(e)}", messages.ERROR
            )

    bulk_approve_corrections.short_description = "Approve pending corrections"

    def generate_attendance_report(self, request, queryset):
        try:
            if queryset.count() > 1000:
                task = ReportService.generate_bulk_attendance_report.delay(
                    list(queryset.values_list("id", flat=True)), request.user.id
                )

                self.message_user(
                    request,
                    f"Report generation started in background. Task ID: {task.id}",
                    messages.INFO,
                )
            else:
                report_data = ReportService.generate_attendance_report_data(
                    queryset, request.user
                )

                response = HttpResponse(
                    json.dumps(report_data, indent=2, default=str),
                    content_type="application/json",
                )
                response["Content-Disposition"] = (
                    f'attachment; filename="attendance_report_{get_current_date()}.json"'
                )

                return response

        except Exception as e:
            self.message_user(
                request, f"Report generation failed: {str(e)}", messages.ERROR
            )

    generate_attendance_report.short_description = "Generate attendance report"

    def sync_with_devices(self, request, queryset):
        try:
            task = sync_all_devices.delay()

            self.message_user(
                request,
                f"Device synchronization started. Task ID: {task.id}",
                messages.INFO,
            )

        except Exception as e:
            self.message_user(request, f"Device sync failed: {str(e)}", messages.ERROR)

    sync_with_devices.short_description = "Sync with attendance devices"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["employee"].queryset = (
            EmployeeDataManager.get_accessible_employees(request.user)
        )
        return form

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user

        try:
            obj.full_clean()
            super().save_model(request, obj, form, change)

            if obj.is_manual_entry:
                messages.success(
                    request,
                    f"Manual attendance entry saved for {obj.employee.get_full_name()}",
                )
        except ValidationError as e:
            messages.error(request, f"Validation error: {e}")
            raise

    def has_change_permission(self, request, obj=None):
        if obj and not request.user.is_superuser:
            from .permissions import EmployeeAttendancePermission

            return EmployeeAttendancePermission.can_edit_employee_attendance(
                request.user, obj.employee
            )
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and not request.user.is_superuser:
            from .permissions import EmployeeAttendancePermission

            return EmployeeAttendancePermission.can_delete_employee_attendance(
                request.user, obj.employee
            )
        return super().has_delete_permission(request, obj)


@admin.register(AttendanceLog)
class AttendanceLogAdmin(admin.ModelAdmin):
    form = AttendanceLogForm
    list_display = [
        "employee_code",
        "device_info",
        "timestamp",
        "log_type_badge",
        "processing_status_badge",
        "employee_link",
        "created_at",
    ]
    list_filter = ["processing_status", "log_type", "device", "timestamp", "created_at"]
    search_fields = [
        "employee_code",
        "employee__first_name",
        "employee__last_name",
        "device__device_name",
        "device__device_id",
        "error_message",
    ]
    readonly_fields = ["created_at", "updated_at", "processed_at"]
    fieldsets = (
        (
            "Log Information",
            {
                "fields": (
                    "employee_code",
                    "employee",
                    "device",
                    "timestamp",
                    "log_type",
                )
            },
        ),
        (
            "Processing Status",
            {"fields": ("processing_status", "error_message", "processed_at")},
        ),
        ("Raw Data", {"fields": ("raw_data",), "classes": ("collapse",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    actions = [
        "reprocess_logs",
        "mark_as_processed",
        "mark_as_error",
        "export_logs_excel",
        "bulk_assign_employees",
    ]
    date_hierarchy = "timestamp"
    ordering = ["-timestamp"]
    list_per_page = 100

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("employee", "device")

    def device_info(self, obj):
        if obj.device:
            return format_html(
                "<strong>{}</strong><br/>" "<small>{}</small>",
                obj.device.device_name,
                obj.device.device_id,
            )
        return "Unknown Device"

    device_info.short_description = "Device"

    def log_type_badge(self, obj):
        colors = {
            "CHECK_IN": "green",
            "CHECK_OUT": "red",
            "BREAK_START": "orange",
            "BREAK_END": "blue",
        }
        color = colors.get(obj.log_type, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_log_type_display(),
        )

    log_type_badge.short_description = "Type"

    def processing_status_badge(self, obj):
        colors = {"PENDING": "orange", "PROCESSED": "green", "ERROR": "red"}
        color = colors.get(obj.processing_status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_processing_status_display(),
        )

    processing_status_badge.short_description = "Status"

    def employee_link(self, obj):
        if obj.employee:
            url = reverse("admin:accounts_customuser_change", args=[obj.employee.id])
            return format_html('<a href="{}">{}</a>', url, obj.employee.get_full_name())
        return format_html(
            '<span style="color: red;">Employee not found: {}</span>', obj.employee_code
        )

    employee_link.short_description = "Employee"

    def reprocess_logs(self, request, queryset):
        try:
            task = process_pending_attendance_logs.delay()

            updated = queryset.filter(processing_status="ERROR").update(
                processing_status="PENDING", error_message=None
            )

            self.message_user(
                request,
                f"Reprocessing {updated} logs. Task ID: {task.id}",
                messages.INFO,
            )

        except Exception as e:
            self.message_user(request, f"Reprocessing failed: {str(e)}", messages.ERROR)

    reprocess_logs.short_description = "Reprocess selected logs"

    def mark_as_processed(self, request, queryset):
        updated = queryset.update(
            processing_status="PROCESSED", processed_at=get_current_datetime()
        )

        self.message_user(
            request, f"Marked {updated} logs as processed.", messages.SUCCESS
        )

    mark_as_processed.short_description = "Mark as processed"

    def mark_as_error(self, request, queryset):
        updated = queryset.update(
            processing_status="ERROR", error_message="Manually marked as error"
        )

        self.message_user(request, f"Marked {updated} logs as error.", messages.SUCCESS)

    mark_as_error.short_description = "Mark as error"

    def export_logs_excel(self, request, queryset):
        try:
            excel_data = ExcelService.export_logs_to_excel(queryset, request.user)

            response = HttpResponse(
                excel_data,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="attendance_logs_{get_current_date()}.xlsx"'
            )

            return response

        except Exception as e:
            self.message_user(request, f"Export failed: {str(e)}", messages.ERROR)

    export_logs_excel.short_description = "Export to Excel"

    def bulk_assign_employees(self, request, queryset):
        try:
            assigned_count = 0
            for log in queryset.filter(employee__isnull=True):
                employee = EmployeeDataManager.get_employee_by_code(log.employee_code)
                if employee:
                    log.employee = employee
                    log.save()
                    assigned_count += 1

            self.message_user(
                request,
                f"Assigned employees to {assigned_count} logs.",
                messages.SUCCESS,
            )

        except Exception as e:
            self.message_user(
                request, f"Employee assignment failed: {str(e)}", messages.ERROR
            )

    bulk_assign_employees.short_description = "Assign employees to logs"


@admin.register(AttendanceDevice)
class AttendanceDeviceAdmin(admin.ModelAdmin):
    form = AttendanceDeviceForm
    list_display = [
        "device_name",
        "device_id",
        "device_type",
        "connection_status",
        "ip_address",
        "port",
        "location",
        "last_sync_time",
        "is_active",
    ]
    list_filter = ["device_type", "status", "is_active", "last_sync_time"]
    search_fields = ["device_name", "device_id", "ip_address", "location"]
    readonly_fields = ["last_sync_time", "created_at", "updated_at"]
    fieldsets = (
        (
            "Device Information",
            {"fields": ("device_name", "device_id", "device_type", "location")},
        ),
        (
            "Connection Settings",
            {"fields": ("ip_address", "port", "username", "password")},
        ),
        (
            "Status & Configuration",
            {"fields": ("status", "is_active", "sync_interval_minutes")},
        ),
        ("Sync Information", {"fields": ("last_sync_time",), "classes": ("collapse",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    actions = [
        "test_connection",
        "sync_device_data",
        "sync_employees_to_device",
        "activate_devices",
        "deactivate_devices",
        "restart_devices",
    ]
    ordering = ["device_name"]

    def connection_status(self, obj):
        colors = {
            "ACTIVE": "green",
            "INACTIVE": "orange",
            "ERROR": "red",
            "MAINTENANCE": "blue",
        }
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    connection_status.short_description = "Status"

    def test_connection(self, request, queryset):
        results = []
        for device in queryset:
            try:
                is_connected, message = device.test_connection()
                status = "Success" if is_connected else "Failed"
                results.append(f"{device.device_name}: {status} - {message}")

                if is_connected and device.status != "ACTIVE":
                    device.status = "ACTIVE"
                    device.save()
                elif not is_connected and device.status == "ACTIVE":
                    device.status = "ERROR"
                    device.save()

            except Exception as e:
                results.append(f"{device.device_name}: Error - {str(e)}")

        self.message_user(
            request, f"Connection test results: {'; '.join(results)}", messages.INFO
        )

    test_connection.short_description = "Test device connections"

    def sync_device_data(self, request, queryset):
        try:
            for device in queryset:
                task = sync_device_data.delay(device.id)

            self.message_user(
                request, f"Sync started for {queryset.count()} devices.", messages.INFO
            )

        except Exception as e:
            self.message_user(request, f"Sync failed: {str(e)}", messages.ERROR)

    sync_device_data.short_description = "Sync attendance data"

    def sync_employees_to_device(self, request, queryset):
        try:
            for device in queryset:
                DeviceService.sync_employees_to_device(device)

            self.message_user(
                request,
                f"Employee sync completed for {queryset.count()} devices.",
                messages.SUCCESS,
            )

        except Exception as e:
            self.message_user(
                request, f"Employee sync failed: {str(e)}", messages.ERROR
            )

    sync_employees_to_device.short_description = "Sync employees to devices"

    def activate_devices(self, request, queryset):
        updated = queryset.update(is_active=True, status="ACTIVE")
        self.message_user(request, f"Activated {updated} devices.", messages.SUCCESS)

    activate_devices.short_description = "Activate selected devices"

    def deactivate_devices(self, request, queryset):
        updated = queryset.update(is_active=False, status="INACTIVE")
        self.message_user(request, f"Deactivated {updated} devices.", messages.SUCCESS)

    deactivate_devices.short_description = "Deactivate selected devices"

    def restart_devices(self, request, queryset):
        try:
            for device in queryset:
                DeviceService.restart_device(device)

            self.message_user(
                request,
                f"Restart command sent to {queryset.count()} devices.",
                messages.INFO,
            )

        except Exception as e:
            self.message_user(
                request, f"Device restart failed: {str(e)}", messages.ERROR
            )

    restart_devices.short_description = "Restart selected devices"


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    form = ShiftForm
    list_display = [
        "name",
        "shift_type",
        "start_time",
        "end_time",
        "break_duration",
        "total_hours",
        "is_active",
    ]
    list_filter = ["shift_type", "is_active"]
    search_fields = ["name", "description"]
    fieldsets = (
        ("Shift Information", {"fields": ("name", "shift_type", "description")}),
        ("Timing", {"fields": ("start_time", "end_time", "break_duration")}),
        (
            "Settings",
            {
                "fields": (
                    "grace_period_minutes",
                    "overtime_threshold_minutes",
                    "is_active",
                )
            },
        ),
    )
    ordering = ["name"]

    def total_hours(self, obj):
        if obj.start_time and obj.end_time:
            start_datetime = datetime.combine(date.today(), obj.start_time)
            end_datetime = datetime.combine(date.today(), obj.end_time)

            if end_datetime < start_datetime:
                end_datetime += timedelta(days=1)

            total_time = end_datetime - start_datetime

            if obj.break_duration:
                total_time -= obj.break_duration

            hours = total_time.total_seconds() / 3600
            return f"{hours:.2f}h"
        return "N/A"

    total_hours.short_description = "Total Hours"


@admin.register(EmployeeShift)
class EmployeeShiftAdmin(admin.ModelAdmin):
    form = EmployeeShiftAssignmentForm
    list_display = [
        "employee_info",
        "shift",
        "effective_from",
        "effective_to",
        "is_active",
        "assigned_by",
        "created_at",
    ]
    list_filter = ["shift", "is_active", "effective_from", "effective_to"]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "shift__name",
    ]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        ("Assignment Information", {"fields": ("employee", "shift", "assigned_by")}),
        (
            "Effective Period",
            {"fields": ("effective_from", "effective_to", "is_active")},
        ),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    actions = ["activate_assignments", "deactivate_assignments"]
    date_hierarchy = "effective_from"
    ordering = ["-effective_from"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("employee", "shift", "assigned_by")

    def employee_info(self, obj):
        return format_html(
            "<strong>{}</strong><br/>" "<small>{}</small>",
            obj.employee.get_full_name(),
            obj.employee.employee_code,
        )

    employee_info.short_description = "Employee"

    def activate_assignments(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(
            request, f"Activated {updated} shift assignments.", messages.SUCCESS
        )

    activate_assignments.short_description = "Activate assignments"

    def deactivate_assignments(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(
            request, f"Deactivated {updated} shift assignments.", messages.SUCCESS
        )

    deactivate_assignments.short_description = "Deactivate assignments"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.assigned_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    form = LeaveRequestForm
    list_display = [
        "employee_info",
        "leave_type",
        "start_date",
        "end_date",
        "total_days",
        "status_badge",
        "is_half_day",
        "applied_date",
        "approved_by",
    ]
    list_filter = ["status", "leave_type", "is_half_day", "applied_date", "start_date"]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "reason",
        "approved_by__first_name",
        "approved_by__last_name",
    ]
    readonly_fields = [
        "applied_date",
        "approved_date",
        "total_days",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        (
            "Leave Information",
            {
                "fields": (
                    "employee",
                    "leave_type",
                    "start_date",
                    "end_date",
                    "is_half_day",
                )
            },
        ),
        ("Request Details", {"fields": ("reason", "total_days")}),
        (
            "Approval Information",
            {"fields": ("status", "approved_by", "approved_date", "approval_notes")},
        ),
        (
            "Timestamps",
            {
                "fields": ("applied_date", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )
    actions = [
        "approve_requests",
        "reject_requests",
        "mark_as_pending",
        "export_leave_report",
        "bulk_notify_employees",
    ]
    date_hierarchy = "start_date"
    ordering = ["-applied_date"]
    list_per_page = 50

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("employee", "leave_type", "approved_by")

    def employee_info(self, obj):
        return format_html(
            "<strong>{}</strong><br/>" "<small>{} - {}</small>",
            obj.employee.get_full_name(),
            obj.employee.employee_code,
            (
                obj.employee.department.name
                if obj.employee.department
                else "No Department"
            ),
        )

    employee_info.short_description = "Employee"

    def status_badge(self, obj):
        colors = {
            "PENDING": "orange",
            "APPROVED": "green",
            "REJECTED": "red",
            "CANCELLED": "gray",
        }
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def approve_requests(self, request, queryset):
        try:
            approved_count = 0
            for leave_request in queryset.filter(status="PENDING"):
                result = LeaveService.approve_leave_request(leave_request, request.user)
                if result["success"]:
                    approved_count += 1

            self.message_user(
                request,
                f"Successfully approved {approved_count} leave requests.",
                messages.SUCCESS,
            )

        except Exception as e:
            self.message_user(request, f"Approval failed: {str(e)}", messages.ERROR)

    approve_requests.short_description = "Approve selected requests"

    def reject_requests(self, request, queryset):
        try:
            rejected_count = 0
            for leave_request in queryset.filter(status="PENDING"):
                result = LeaveService.reject_leave_request(
                    leave_request, request.user, "Bulk rejection"
                )
                if result["success"]:
                    rejected_count += 1

            self.message_user(
                request,
                f"Successfully rejected {rejected_count} leave requests.",
                messages.SUCCESS,
            )

        except Exception as e:
            self.message_user(request, f"Rejection failed: {str(e)}", messages.ERROR)

    reject_requests.short_description = "Reject selected requests"


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "code",
        "max_days_per_year",
        "requires_approval",
        "is_paid",
        "carry_forward_allowed",
        "is_active",
    ]
    list_filter = ["requires_approval", "is_paid", "carry_forward_allowed", "is_active"]
    search_fields = ["name", "code", "description"]
    fieldsets = (
        ("Leave Type Information", {"fields": ("name", "code", "description")}),
        (
            "Configuration",
            {
                "fields": (
                    "max_days_per_year",
                    "requires_approval",
                    "is_paid",
                    "carry_forward_allowed",
                    "max_carry_forward_days",
                )
            },
        ),
        ("Settings", {"fields": ("is_active",)}),
    )
    ordering = ["name"]


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = [
        "employee_info",
        "leave_type",
        "year",
        "allocated_days",
        "used_days",
        "remaining_days",
        "carried_forward_days",
    ]
    list_filter = ["leave_type", "year"]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
    ]
    readonly_fields = ["used_days", "remaining_days", "created_at", "updated_at"]
    fieldsets = (
        ("Balance Information", {"fields": ("employee", "leave_type", "year")}),
        (
            "Days Allocation",
            {
                "fields": (
                    "allocated_days",
                    "carried_forward_days",
                    "used_days",
                    "remaining_days",
                )
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    actions = ["recalculate_balances", "reset_yearly_balances"]
    ordering = ["employee__employee_code", "leave_type", "year"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("employee", "leave_type")

    def employee_info(self, obj):
        return format_html(
            "<strong>{}</strong><br/>" "<small>{}</small>",
            obj.employee.get_full_name(),
            obj.employee.employee_code,
        )

    employee_info.short_description = "Employee"

    def recalculate_balances(self, request, queryset):
        try:
            updated_count = 0
            for balance in queryset:
                LeaveService.recalculate_leave_balance(balance)
                updated_count += 1

            self.message_user(
                request,
                f"Recalculated {updated_count} leave balances.",
                messages.SUCCESS,
            )

        except Exception as e:
            self.message_user(
                request, f"Recalculation failed: {str(e)}", messages.ERROR
            )

    recalculate_balances.short_description = "Recalculate selected balances"


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    form = HolidayForm
    list_display = [
        "name",
        "date",
        "holiday_type",
        "is_recurring",
        "applicable_departments",
        "is_active",
    ]
    list_filter = ["holiday_type", "is_recurring", "is_active", "date"]
    search_fields = ["name", "description"]
    fieldsets = (
        (
            "Holiday Information",
            {"fields": ("name", "date", "holiday_type", "description")},
        ),
        ("Configuration", {"fields": ("is_recurring", "departments", "is_active")}),
    )
    filter_horizontal = ["departments"]
    date_hierarchy = "date"
    ordering = ["-date"]

    def applicable_departments(self, obj):
        if obj.departments.exists():
            return ", ".join([dept.name for dept in obj.departments.all()[:3]])
        return "All Departments"

    applicable_departments.short_description = "Departments"


@admin.register(MonthlyAttendanceSummary)
class MonthlyAttendanceSummaryAdmin(admin.ModelAdmin):
    list_display = [
        "employee_info",
        "year",
        "month_name",
        "total_working_days",
        "total_present_days",
        "attendance_percentage",
        "total_work_hours",
        "total_overtime_hours",
        "total_late_days",
    ]
    list_filter = ["year", "month", "employee__department"]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
    ]
    readonly_fields = [
        "total_working_days",
        "total_present_days",
        "total_absent_days",
        "total_late_days",
        "total_early_departure_days",
        "total_overtime_hours",
        "total_work_hours",
        "total_break_hours",
        "average_daily_hours",
        "attendance_percentage",
        "punctuality_percentage",
        "total_leave_days",
        "total_holiday_days",
        "longest_continuous_work_streak",
        "total_check_ins",
        "average_first_check_in",
        "average_last_check_out",
        "most_productive_day",
        "least_productive_day",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        ("Summary Information", {"fields": ("employee", "year", "month")}),
        (
            "Attendance Statistics",
            {
                "fields": (
                    "total_working_days",
                    "total_present_days",
                    "total_absent_days",
                    "attendance_percentage",
                    "punctuality_percentage",
                )
            },
        ),
        (
            "Time Statistics",
            {
                "fields": (
                    "total_work_hours",
                    "total_overtime_hours",
                    "total_break_hours",
                    "average_daily_hours",
                    "average_first_check_in",
                    "average_last_check_out",
                )
            },
        ),
        (
            "Behavioral Statistics",
            {
                "fields": (
                    "total_late_days",
                    "total_early_departure_days",
                    "total_check_ins",
                    "longest_continuous_work_streak",
                    "most_productive_day",
                    "least_productive_day",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Leave & Holiday Statistics",
            {
                "fields": ("total_leave_days", "total_holiday_days"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    actions = [
        "regenerate_summaries",
        "export_summaries_excel",
        "generate_performance_report",
    ]
    ordering = ["-year", "-month", "employee__employee_code"]
    list_per_page = 50

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("employee", "employee__department")

    def employee_info(self, obj):
        return format_html(
            "<strong>{}</strong><br/>" "<small>{}</small>",
            obj.employee.get_full_name(),
            obj.employee.employee_code,
        )

    employee_info.short_description = "Employee"

    def month_name(self, obj):
        import calendar

        return calendar.month_name[obj.month]

    month_name.short_description = "Month"

    def regenerate_summaries(self, request, queryset):
        try:
            task = generate_monthly_summaries.delay()

            self.message_user(
                request,
                f"Summary regeneration started. Task ID: {task.id}",
                messages.INFO,
            )

        except Exception as e:
            self.message_user(request, f"Regeneration failed: {str(e)}", messages.ERROR)

    regenerate_summaries.short_description = "Regenerate selected summaries"

    def export_summaries_excel(self, request, queryset):
        try:
            excel_data = ExcelService.export_monthly_summaries_to_excel(
                queryset, request.user
            )

            response = HttpResponse(
                excel_data,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="monthly_summaries_{get_current_date()}.xlsx"'
            )

            return response

        except Exception as e:
            self.message_user(request, f"Export failed: {str(e)}", messages.ERROR)

    export_summaries_excel.short_description = "Export to Excel"


@admin.register(AttendanceCorrection)
class AttendanceCorrectionAdmin(admin.ModelAdmin):
    form = AttendanceCorrectionForm
    list_display = [
        "attendance_info",
        "correction_type",
        "status_badge",
        "requested_by",
        "requested_date",
        "approved_by",
        "approved_date",
    ]
    list_filter = ["correction_type", "status", "requested_date", "approved_date"]
    search_fields = [
        "attendance__employee__employee_code",
        "attendance__employee__first_name",
        "attendance__employee__last_name",
        "reason",
        "requested_by__first_name",
    ]
    readonly_fields = ["requested_date", "approved_date", "created_at", "updated_at"]
    fieldsets = (
        (
            "Correction Information",
            {"fields": ("attendance", "correction_type", "reason")},
        ),
        ("Original Values", {"fields": ("original_data",), "classes": ("collapse",)}),
        ("Corrected Values", {"fields": ("corrected_data",)}),
        (
            "Request Information",
            {"fields": ("requested_by", "requested_date", "status")},
        ),
        (
            "Approval Information",
            {"fields": ("approved_by", "approved_date", "approval_notes")},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    actions = ["approve_corrections", "reject_corrections", "bulk_process_corrections"]
    date_hierarchy = "requested_date"
    ordering = ["-requested_date"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related(
            "attendance__employee", "requested_by", "approved_by"
        )

    def attendance_info(self, obj):
        return format_html(
            "<strong>{}</strong><br/>" "<small>{} - {}</small>",
            obj.attendance.employee.get_full_name(),
            obj.attendance.employee.employee_code,
            obj.attendance.date,
        )

    attendance_info.short_description = "Attendance Record"

    def status_badge(self, obj):
        colors = {"PENDING": "orange", "APPROVED": "green", "REJECTED": "red"}
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def approve_corrections(self, request, queryset):
        try:
            approved_count = 0
            for correction in queryset.filter(status="PENDING"):
                correction.approve(request.user)
                approved_count += 1

            self.message_user(
                request,
                f"Successfully approved {approved_count} corrections.",
                messages.SUCCESS,
            )

        except Exception as e:
            self.message_user(request, f"Approval failed: {str(e)}", messages.ERROR)

    approve_corrections.short_description = "Approve selected corrections"

    def reject_corrections(self, request, queryset):
        try:
            rejected_count = 0
            for correction in queryset.filter(status="PENDING"):
                correction.reject(request.user, "Bulk rejection")
                rejected_count += 1

            self.message_user(
                request,
                f"Successfully rejected {rejected_count} corrections.",
                messages.SUCCESS,
            )

        except Exception as e:
            self.message_user(request, f"Rejection failed: {str(e)}", messages.ERROR)

    reject_corrections.short_description = "Reject selected corrections"


@admin.register(AttendanceReport)
class AttendanceReportAdmin(admin.ModelAdmin):
    form = AttendanceReportForm
    list_display = [
        "report_name",
        "report_type",
        "generated_by",
        "generated_date",
        "start_date",
        "end_date",
        "status_badge",
        "file_size",
    ]
    list_filter = ["report_type", "status", "generated_date"]
    search_fields = [
        "report_name",
        "generated_by__first_name",
        "generated_by__last_name",
    ]
    readonly_fields = [
        "generated_date",
        "file_path",
        "file_size",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        (
            "Report Information",
            {"fields": ("report_name", "report_type", "description")},
        ),
        ("Parameters", {"fields": ("start_date", "end_date", "filters")}),
        ("Generation Info", {"fields": ("generated_by", "generated_date", "status")}),
        (
            "File Information",
            {"fields": ("file_path", "file_size"), "classes": ("collapse",)},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    actions = ["regenerate_reports", "download_reports", "delete_report_files"]
    date_hierarchy = "generated_date"
    ordering = ["-generated_date"]

    def status_badge(self, obj):
        colors = {"PENDING": "orange", "COMPLETED": "green", "FAILED": "red"}
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def regenerate_reports(self, request, queryset):
        try:
            for report in queryset:
                ReportService.regenerate_report(report, request.user)

            self.message_user(
                request,
                f"Regeneration started for {queryset.count()} reports.",
                messages.INFO,
            )

        except Exception as e:
            self.message_user(request, f"Regeneration failed: {str(e)}", messages.ERROR)

    regenerate_reports.short_description = "Regenerate selected reports"


# Custom Admin Site Configuration
class AttendanceAdminSite(admin.AdminSite):
    site_header = "Attendance Management System"
    site_title = "Attendance Admin"
    index_title = "Welcome to Attendance Management"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "dashboard/",
                self.admin_view(self.dashboard_view),
                name="attendance_dashboard",
            ),
            path(
                "device-status/",
                self.admin_view(self.device_status_view),
                name="device_status",
            ),
            path(
                "sync-all-devices/",
                self.admin_view(self.sync_all_devices_view),
                name="sync_all_devices",
            ),
            path(
                "import-excel/",
                self.admin_view(self.import_excel_view),
                name="import_excel",
            ),
            path(
                "export-data/",
                self.admin_view(self.export_data_view),
                name="export_data",
            ),
            path(
                "system-health/",
                self.admin_view(self.system_health_view),
                name="system_health",
            ),
        ]
        return custom_urls + urls

    def dashboard_view(self, request):
        try:
            today = get_current_date()

            context = {
                "title": "Attendance Dashboard",
                "today_stats": StatisticsService.get_daily_statistics(today),
                "device_stats": StatisticsService.get_device_statistics(),
                "recent_logs": AttendanceLog.objects.filter(
                    created_at__date=today
                ).order_by("-created_at")[:10],
                "pending_corrections": AttendanceCorrection.objects.filter(
                    status="PENDING"
                ).count(),
                "pending_leaves": LeaveRequest.objects.filter(status="PENDING").count(),
            }

            return TemplateResponse(request, "admin/attendance_dashboard.html", context)

        except Exception as e:
            messages.error(request, f"Dashboard error: {str(e)}")
            return redirect("admin:index")

    def device_status_view(self, request):
        try:
            devices = AttendanceDevice.objects.all()
            device_status = []

            for device in devices:
                is_connected, message = device.test_connection()
                device_status.append(
                    {
                        "device": device,
                        "is_connected": is_connected,
                        "message": message,
                        "recent_logs": AttendanceLog.objects.filter(
                            device=device,
                            created_at__gte=get_current_datetime()
                            - timedelta(hours=24),
                        ).count(),
                    }
                )

            context = {
                "title": "Device Status",
                "device_status": device_status,
            }

            return TemplateResponse(request, "admin/device_status.html", context)

        except Exception as e:
            messages.error(request, f"Device status error: {str(e)}")
            return redirect("admin:index")

    def sync_all_devices_view(self, request):
        try:
            if request.method == "POST":
                task = sync_all_devices.delay()
                messages.success(
                    request, f"Device synchronization started. Task ID: {task.id}"
                )
                return redirect("admin:device_status")

            context = {
                "title": "Sync All Devices",
                "devices": AttendanceDevice.active.all(),
            }

            return TemplateResponse(request, "admin/sync_devices.html", context)

        except Exception as e:
            messages.error(request, f"Sync error: {str(e)}")
            return redirect("admin:index")

    def import_excel_view(self, request):
        try:
            if request.method == "POST" and request.FILES.get("excel_file"):
                excel_file = request.FILES["excel_file"]

                task = import_attendance_from_excel.delay(
                    excel_file.read(),
                    request.user.id,
                    {
                        "overwrite": request.POST.get("overwrite") == "on",
                        "skip_errors": request.POST.get("skip_errors") == "on",
                    },
                )

                messages.success(request, f"Excel import started. Task ID: {task.id}")
                return redirect("admin:index")

            context = {
                "title": "Import Excel Data",
            }

            return TemplateResponse(request, "admin/import_excel.html", context)

        except Exception as e:
            messages.error(request, f"Import error: {str(e)}")
            return redirect("admin:index")

    def export_data_view(self, request):
        try:
            if request.method == "POST":
                export_type = request.POST.get("export_type")
                start_date = request.POST.get("start_date")
                end_date = request.POST.get("end_date")

                if export_type == "attendance":
                    queryset = Attendance.objects.filter(
                        date__range=[start_date, end_date]
                    )
                    excel_data = ExcelService.export_attendance_to_excel(
                        queryset, request.user
                    )
                    filename = f"attendance_export_{start_date}_{end_date}.xlsx"

                elif export_type == "monthly_summaries":
                    year, month = start_date.split("-")
                    queryset = MonthlyAttendanceSummary.objects.filter(
                        year=int(year), month=int(month)
                    )
                    excel_data = ExcelService.export_monthly_summaries_to_excel(
                        queryset, request.user
                    )
                    filename = f"monthly_summaries_{year}_{month}.xlsx"

                response = HttpResponse(
                    excel_data,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                response["Content-Disposition"] = f'attachment; filename="{filename}"'

                return response

            context = {
                "title": "Export Data",
            }

            return TemplateResponse(request, "admin/export_data.html", context)

        except Exception as e:
            messages.error(request, f"Export error: {str(e)}")
            return redirect("admin:index")

    def system_health_view(self, request):
        try:
            from .tasks import system_health_monitor

            health_data = system_health_monitor()

            context = {
                "title": "System Health",
                "health_data": health_data,
            }

            return TemplateResponse(request, "admin/system_health.html", context)

        except Exception as e:
            messages.error(request, f"Health check error: {str(e)}")
            return redirect("admin:index")


# Create custom admin site instance
attendance_admin_site = AttendanceAdminSite(name="attendance_admin")

# Register all models with custom admin site
attendance_admin_site.register(Attendance, AttendanceAdmin)
attendance_admin_site.register(AttendanceLog, AttendanceLogAdmin)
attendance_admin_site.register(AttendanceDevice, AttendanceDeviceAdmin)
attendance_admin_site.register(Shift, ShiftAdmin)
attendance_admin_site.register(EmployeeShift, EmployeeShiftAdmin)
attendance_admin_site.register(LeaveRequest, LeaveRequestAdmin)
attendance_admin_site.register(LeaveType, LeaveTypeAdmin)
attendance_admin_site.register(LeaveBalance, LeaveBalanceAdmin)
attendance_admin_site.register(Holiday, HolidayAdmin)
attendance_admin_site.register(MonthlyAttendanceSummary, MonthlyAttendanceSummaryAdmin)
attendance_admin_site.register(AttendanceCorrection, AttendanceCorrectionAdmin)
attendance_admin_site.register(AttendanceReport, AttendanceReportAdmin)

# Additional admin customizations
admin.site.site_header = "Enterprise Attendance Management"
admin.site.site_title = "Attendance Admin Portal"
admin.site.index_title = "Welcome to Attendance Management System"


# Custom admin actions for bulk operations
def bulk_sync_devices(modeladmin, request, queryset):
    try:
        task = sync_all_devices.delay()
        modeladmin.message_user(
            request, f"Bulk device sync started. Task ID: {task.id}", messages.INFO
        )
    except Exception as e:
        modeladmin.message_user(request, f"Bulk sync failed: {str(e)}", messages.ERROR)


bulk_sync_devices.short_description = "Sync all attendance devices"


def generate_monthly_reports(modeladmin, request, queryset):
    try:
        task = generate_monthly_summaries.delay()
        modeladmin.message_user(
            request,
            f"Monthly report generation started. Task ID: {task.id}",
            messages.INFO,
        )
    except Exception as e:
        modeladmin.message_user(
            request, f"Report generation failed: {str(e)}", messages.ERROR
        )


generate_monthly_reports.short_description = "Generate monthly attendance reports"

# Add global admin actions
admin.site.add_action(bulk_sync_devices)
admin.site.add_action(generate_monthly_reports)
