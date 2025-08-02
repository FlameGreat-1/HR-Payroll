from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django.utils.html import format_html, mark_safe
from django.utils import timezone
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.contrib import messages
from django.db.models import Q, Count, Sum, Avg
from django.core.exceptions import ValidationError
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import io
import logging

from .models import (
    Attendance,
    AttendanceLog,
    MonthlyAttendanceSummary,
    AttendanceDevice,
    AttendanceCorrection,
    AttendanceReport,
    Shift,
    EmployeeShift,
    Holiday,
    LeaveType,
    LeaveBalance,
    LeaveRequest,
)
from .utils import (
    TimeCalculator,
    EmployeeDataManager,
    AttendanceCalculator,
    MonthlyCalculator,
    ExcelProcessor,
    DeviceManager,
    ValidationHelper,
    CacheManager,
    AuditHelper,
    ReportGenerator,
    DeviceDataProcessor,
)
from .permissions import (
    check_attendance_permission,
    get_accessible_employees,
    get_accessible_departments,
    AttendancePermissionMixin,
    DevicePermission,
    ReportPermission,
    SystemPermission,
    EmployeeAttendancePermission,
    LeavePermission,
    BulkOperationPermission,
    TimeBasedPermission,
)
from accounts.models import CustomUser, SystemConfiguration
from employees.models import EmployeeProfile

logger = logging.getLogger(__name__)


def export_attendance_excel(modeladmin, request, queryset):
    try:
        attendance_data = []

        for attendance in queryset.select_related("employee", "employee__department"):
            employee = attendance.employee

            time_pairs = [
                (attendance.check_in_1, attendance.check_out_1),
                (attendance.check_in_2, attendance.check_out_2),
                (attendance.check_in_3, attendance.check_out_3),
                (attendance.check_in_4, attendance.check_out_4),
                (attendance.check_in_5, attendance.check_out_5),
                (attendance.check_in_6, attendance.check_out_6),
            ]

            attendance_data.append(
                {
                    "division": (
                        employee.department.name if employee.department else "N/A"
                    ),
                    "employee_id": employee.employee_code,
                    "name": employee.get_full_name(),
                    "time_pairs": time_pairs,
                    "total_time": attendance.total_time,
                    "break_time": attendance.break_time,
                    "work_time": attendance.work_time,
                    "overtime": attendance.overtime,
                }
            )

        excel_buffer = ExcelProcessor.create_attendance_excel(
            attendance_data, timezone.now().month, timezone.now().year
        )

        response = HttpResponse(
            excel_buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="attendance_export_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
        )

        AuditHelper.log_attendance_change(
            user=request.user,
            action="EXPORT",
            employee=None,
            attendance_date=timezone.now().date(),
            changes={"exported_records": queryset.count()},
            request=request,
        )

        modeladmin.message_user(
            request,
            f"Successfully exported {len(attendance_data)} attendance records to Excel.",
        )
        return response

    except Exception as e:
        logger.error(f"Excel export failed: {e}")
        modeladmin.message_user(
            request, f"Export failed: {str(e)}", level=messages.ERROR
        )


export_attendance_excel.short_description = "📊 Export to Excel"


def sync_with_devices(modeladmin, request, queryset):
    try:
        devices = AttendanceDevice.objects.filter(is_active=True)

        if not devices.exists():
            modeladmin.message_user(
                request, "No active devices configured.", level=messages.WARNING
            )
            return

        sync_results = []

        for device in devices:
            is_connected, message = DeviceManager.test_device_connection(
                device.ip_address, device.port
            )

            if is_connected:
                employees = EmployeeDataManager.get_active_employees()
                sock = DeviceManager.connect_to_realand_device(
                    device.ip_address, device.port
                )

                if sock:
                    synced_count = 0
                    for employee in employees:
                        if DeviceManager.sync_employee_to_device(sock, employee):
                            synced_count += 1

                    sock.close()
                    sync_results.append(
                        f"Device {device.device_name}: {synced_count} employees synced"
                    )
                else:
                    sync_results.append(
                        f"Device {device.device_name}: Connection failed"
                    )
            else:
                sync_results.append(f"Device {device.device_name}: {message}")

        AuditHelper.log_device_sync(
            user=request.user,
            device_id="multiple",
            sync_result={"results": sync_results},
            request=request,
        )

        modeladmin.message_user(
            request, f"Device sync completed. Results: {'; '.join(sync_results)}"
        )

    except Exception as e:
        logger.error(f"Device sync failed: {e}")
        modeladmin.message_user(
            request, f"Device sync failed: {str(e)}", level=messages.ERROR
        )


sync_with_devices.short_description = "📱 Sync with Devices"


def calculate_monthly_summaries(modeladmin, request, queryset):
    try:
        employees_processed = set()
        summaries_created = 0

        for attendance in queryset.select_related("employee"):
            employee = attendance.employee
            year = attendance.date.year
            month = attendance.date.month

            employee_key = f"{employee.id}_{year}_{month}"

            if employee_key not in employees_processed:
                summary = MonthlyAttendanceSummary.generate_for_employee_month(
                    employee, year, month, request.user
                )

                employees_processed.add(employee_key)
                summaries_created += 1

        modeladmin.message_user(
            request,
            f"Monthly summaries processed for {len(employees_processed)} employee-month combinations. "
            f"{summaries_created} summaries generated.",
        )

    except Exception as e:
        logger.error(f"Monthly summary calculation failed: {e}")
        modeladmin.message_user(
            request,
            f"Monthly summary calculation failed: {str(e)}",
            level=messages.ERROR,
        )


calculate_monthly_summaries.short_description = "📊 Calculate Monthly Summary"


def validate_attendance_data(modeladmin, request, queryset):
    validation_results = []

    for attendance in queryset.select_related("employee"):
        time_pairs = attendance.get_time_pairs()
        is_valid, errors = ValidationHelper.validate_attendance_consistency(time_pairs)

        if not is_valid:
            validation_results.append(
                f"❌ {attendance.employee.get_full_name()} ({attendance.date}): {'; '.join(errors)}"
            )
        else:
            validation_results.append(
                f"✅ {attendance.employee.get_full_name()} ({attendance.date}): Valid"
            )

    if validation_results:
        html_content = "<h3>Attendance Validation Results</h3><ul>"
        for result in validation_results:
            html_content += f"<li>{result}</li>"
        html_content += "</ul>"

        return HttpResponse(html_content)
    else:
        modeladmin.message_user(request, "No attendance records to validate.")


validate_attendance_data.short_description = "✅ Validate Data"


def process_device_logs(modeladmin, request, queryset):
    try:
        processed_count = 0

        pending_logs = AttendanceLog.objects.filter(processing_status="PENDING")
        grouped_logs = DeviceDataProcessor.group_logs_by_employee_date(
            [
                {
                    "employee_code": log.employee_code,
                    "timestamp": log.timestamp,
                    "log_type": log.log_type,
                }
                for log in pending_logs
            ]
        )

        for key, logs in grouped_logs.items():
            employee_code, log_date = key.split("_")
            employee = EmployeeDataManager.get_employee_by_code(employee_code)

            if employee:
                attendance, created = Attendance.objects.get_or_create(
                    employee=employee,
                    date=datetime.strptime(log_date, "%Y-%m-%d").date(),
                    defaults={"created_by": request.user},
                )

                attendance.update_from_device_logs(logs)
                processed_count += 1

        for log in pending_logs:
            log.mark_as_processed()

        modeladmin.message_user(
            request, f"Processed {processed_count} device log groups"
        )

    except Exception as e:
        logger.error(f"Log processing failed: {e}")
        modeladmin.message_user(
            request, f"Processing failed: {str(e)}", level=messages.ERROR
        )


process_device_logs.short_description = "🔄 Process Device Logs"


def generate_attendance_report(modeladmin, request, queryset):
    try:
        employees = list(set([record.employee for record in queryset]))
        start_date = min([record.date for record in queryset])
        end_date = max([record.date for record in queryset])

        report_data = ReportGenerator.generate_attendance_report_data(
            employees, start_date, end_date
        )

        report = AttendanceReport.objects.create(
            name=f"Attendance Report {start_date} to {end_date}",
            report_type="CUSTOM",
            start_date=start_date,
            end_date=end_date,
            report_data=report_data,
            generated_by=request.user,
        )

        report.mark_completed()

        modeladmin.message_user(
            request, f"Generated report for {len(report_data)} employees"
        )

    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        modeladmin.message_user(
            request, f"Report failed: {str(e)}", level=messages.ERROR
        )


generate_attendance_report.short_description = "📈 Generate Report"


class AttendanceStatusFilter(SimpleListFilter):
    title = "Attendance Status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [
            ("PRESENT", "✅ Present"),
            ("ABSENT", "❌ Absent"),
            ("LATE", "⏰ Late"),
            ("HALF_DAY", "🕐 Half Day"),
            ("LEAVE", "🏖️ On Leave"),
            ("HOLIDAY", "🎉 Holiday"),
            ("INCOMPLETE", "⚠️ Incomplete"),
            ("EARLY_DEPARTURE", "🏃 Early Departure"),
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class DateRangeFilter(SimpleListFilter):
    title = "Date Range"
    parameter_name = "date_range"

    def lookups(self, request, model_admin):
        return [
            ("today", "📅 Today"),
            ("yesterday", "📅 Yesterday"),
            ("this_week", "📅 This Week"),
            ("last_week", "📅 Last Week"),
            ("this_month", "📅 This Month"),
            ("last_month", "📅 Last Month"),
        ]

    def queryset(self, request, queryset):
        today = timezone.now().date()

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

        return queryset


class DepartmentFilter(SimpleListFilter):
    title = "Department"
    parameter_name = "department"

    def lookups(self, request, model_admin):
        departments = set()
        for employee in EmployeeDataManager.get_active_employees():
            if employee.department:
                departments.add((employee.department.id, employee.department.name))
        return sorted(list(departments), key=lambda x: x[1])

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(employee__department_id=self.value())
        return queryset


class WorkTimeFilter(SimpleListFilter):
    title = "Work Time Performance"
    parameter_name = "work_performance"

    def lookups(self, request, model_admin):
        return [
            ("overtime", "⏰ Overtime (>8h)"),
            ("fulltime", "✅ Full Time (7-8h)"),
            ("undertime", "⚠️ Under Time (<7h)"),
            ("minimal", "❌ Minimal (<4h)"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "overtime":
            return queryset.filter(work_time__gt=timedelta(hours=8))
        elif self.value() == "fulltime":
            return queryset.filter(
                work_time__gte=timedelta(hours=7), work_time__lte=timedelta(hours=8)
            )
        elif self.value() == "undertime":
            return queryset.filter(
                work_time__gte=timedelta(hours=4), work_time__lt=timedelta(hours=7)
            )
        elif self.value() == "minimal":
            return queryset.filter(work_time__lt=timedelta(hours=4))
        return queryset


class ShiftFilter(SimpleListFilter):
    title = "Shift"
    parameter_name = "shift"

    def lookups(self, request, model_admin):
        return [(shift.id, shift.name) for shift in Shift.active.all()]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(shift_id=self.value())
        return queryset


class DeviceFilter(SimpleListFilter):
    title = "Device"
    parameter_name = "device"

    def lookups(self, request, model_admin):
        return [
            (device.id, device.device_name) for device in AttendanceDevice.active.all()
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(device_id=self.value())
        return queryset


class ManualEntryFilter(SimpleListFilter):
    title = "Entry Type"
    parameter_name = "entry_type"

    def lookups(self, request, model_admin):
        return [
            ("manual", "✋ Manual Entry"),
            ("device", "📱 Device Entry"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "manual":
            return queryset.filter(is_manual_entry=True)
        elif self.value() == "device":
            return queryset.filter(is_manual_entry=False)
        return queryset


class AttendanceAdmin(admin.ModelAdmin):
    list_display = [
        "division_info",
        "employee_code_display",
        "full_name",
        "email_display",
        "department_display",
        "date_display",
        "status_badge",
        "time_summary",
        "performance_score",
        "punctuality_score",
        "shift_info",
        "in_1",
        "out_1",
        "in_2",
        "out_2",
        "in_3",
        "out_3",
        "actions_column",
    ]

    list_filter = [
        AttendanceStatusFilter,
        DateRangeFilter,
        DepartmentFilter,
        WorkTimeFilter,
        ShiftFilter,
        DeviceFilter,
        ManualEntryFilter,
        "is_weekend",
        "is_holiday",
        "created_at",
        "updated_at",
    ]

    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "employee__email",
        "employee__department__name",
        "notes",
    ]

    list_per_page = 25
    list_max_show_all = 100
    ordering = ["-date", "employee__employee_code"]

    actions = [
        export_attendance_excel,
        sync_with_devices,
        calculate_monthly_summaries,
        validate_attendance_data,
        process_device_logs,
        generate_attendance_report,
    ]

    fieldsets = (
        (
            "👤 Employee Information",
            {"fields": ("employee", "date", "shift"), "classes": ("wide",)},
        ),
        (
            "📊 Status & Performance",
            {
                "fields": ("status", "is_manual_entry", "device", "location"),
                "classes": ("wide",),
            },
        ),
        (
            "⏰ Time Records - Session 1",
            {"fields": (("check_in_1", "check_out_1"),), "classes": ("collapse",)},
        ),
        (
            "⏰ Time Records - Session 2",
            {"fields": (("check_in_2", "check_out_2"),), "classes": ("collapse",)},
        ),
        (
            "⏰ Time Records - Session 3",
            {"fields": (("check_in_3", "check_out_3"),), "classes": ("collapse",)},
        ),
        (
            "⏰ Time Records - Session 4",
            {"fields": (("check_in_4", "check_out_4"),), "classes": ("collapse",)},
        ),
        (
            "⏰ Time Records - Session 5",
            {"fields": (("check_in_5", "check_out_5"),), "classes": ("collapse",)},
        ),
        (
            "⏰ Time Records - Session 6",
            {"fields": (("check_in_6", "check_out_6"),), "classes": ("collapse",)},
        ),
        (
            "📊 Calculated Metrics",
            {
                "fields": (
                    ("total_time", "break_time"),
                    ("work_time", "overtime"),
                    ("undertime", "first_in_time"),
                    ("last_out_time", "late_minutes"),
                    ("early_departure_minutes",),
                ),
                "classes": ("wide",),
                "description": "These fields are automatically calculated based on time records.",
            },
        ),
        (
            "🏷️ Additional Information",
            {"fields": ("is_weekend", "is_holiday", "notes"), "classes": ("collapse",)},
        ),
        (
            "📝 Metadata",
            {
                "fields": ("created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = [
        "total_time",
        "break_time",
        "work_time",
        "overtime",
        "undertime",
        "first_in_time",
        "last_out_time",
        "late_minutes",
        "early_departure_minutes",
        "is_weekend",
        "is_holiday",
        "created_at",
        "updated_at",
    ]

    def division_info(self, obj):
        if not obj.employee or not obj.employee.department:
            return format_html('<span style="color: #6C757D;">N/A</span>')

        return format_html(
            '<div style="font-weight: bold; color: #2C3E50;">{}</div>',
            obj.employee.department.name,
        )

    division_info.short_description = "🏢 Division"
    division_info.admin_order_field = "employee__department__name"

    def employee_code_display(self, obj):
        if not obj.employee:
            return format_html('<span style="color: red;">❌ No Code</span>')

        return format_html(
            '<div style="font-family: monospace; font-weight: bold; color: #2C3E50;">{}</div>',
            obj.employee.employee_code,
        )

    employee_code_display.short_description = "🆔 Code"
    employee_code_display.admin_order_field = "employee__employee_code"

    def full_name(self, obj):
        if not obj.employee:
            return format_html('<span style="color: red;">❌ No Employee</span>')

        return format_html(
            '<div style="font-weight: bold; color: #2C3E50;">{}</div>',
            obj.employee.get_full_name(),
        )

    full_name.short_description = "👤 Name"
    full_name.admin_order_field = "employee__first_name"

    def email_display(self, obj):
        if not obj.employee:
            return format_html('<span style="color: #6C757D;">N/A</span>')

        return format_html(
            '<div style="font-size: 11px; color: #7F8C8D;">{}</div>', obj.employee.email
        )

    email_display.short_description = "📧 Email"
    email_display.admin_order_field = "employee__email"

    def department_display(self, obj):
        if not obj.employee or not obj.employee.department:
            return format_html('<span style="color: #6C757D;">N/A</span>')

        return format_html(
            '<div style="color: #7F8C8D;">{}</div>', obj.employee.department.name
        )

    department_display.short_description = "🏢 Department"
    department_display.admin_order_field = "employee__department__name"

    def date_display(self, obj):
        if not obj.date:
            return "❌ No Date"

        today = timezone.now().date()
        days_diff = (today - obj.date).days

        if days_diff == 0:
            date_class = "color: #27AE60; font-weight: bold;"
            date_label = "📅 Today"
        elif days_diff == 1:
            date_class = "color: #3498DB;"
            date_label = "📅 Yesterday"
        elif days_diff <= 7:
            date_class = "color: #F39C12;"
            date_label = f"📅 {days_diff} days ago"
        else:
            date_class = "color: #7F8C8D;"
            date_label = "📅 Older"

        return format_html(
            '<div style="{}"><strong>{}</strong><br><small>{}</small></div>',
            date_class,
            obj.date.strftime("%Y-%m-%d"),
            date_label,
        )

    date_display.short_description = "📅 Date"
    date_display.admin_order_field = "date"

    def status_badge(self, obj):
        status_config = {
            "PRESENT": {"color": "#27AE60", "icon": "✅", "bg": "#D5EDDA"},
            "ABSENT": {"color": "#E74C3C", "icon": "❌", "bg": "#F8D7DA"},
            "LATE": {"color": "#F39C12", "icon": "⏰", "bg": "#FFF3CD"},
            "HALF_DAY": {"color": "#3498DB", "icon": "🕐", "bg": "#D1ECF1"},
            "LEAVE": {"color": "#6F42C1", "icon": "🏖️", "bg": "#E2E3F3"},
            "HOLIDAY": {"color": "#20C997", "icon": "🎉", "bg": "#D1F2EB"},
            "INCOMPLETE": {"color": "#6C757D", "icon": "⚠️", "bg": "#E2E3E5"},
            "EARLY_DEPARTURE": {"color": "#FD7E14", "icon": "🏃", "bg": "#FFE8D1"},
        }

        config = status_config.get(
            obj.status, {"color": "#6C757D", "icon": "❓", "bg": "#E2E3E5"}
        )

        return format_html(
            '<span style="background-color: {}; color: {}; padding: 4px 8px; '
            "border-radius: 12px; font-size: 12px; font-weight: bold; "
            'display: inline-block; min-width: 80px; text-align: center;">'
            "{} {}</span>",
            config["bg"],
            config["color"],
            config["icon"],
            obj.status.replace("_", " ").title(),
        )

    status_badge.short_description = "📊 Status"
    status_badge.admin_order_field = "status"

    def time_summary(self, obj):
        work_time_str = (
            ReportGenerator.format_duration_for_display(obj.work_time)
            if obj.work_time
            else "00:00:00"
        )
        total_time_str = (
            ReportGenerator.format_duration_for_display(obj.total_time)
            if obj.total_time
            else "00:00:00"
        )
        break_time_str = (
            ReportGenerator.format_duration_for_display(obj.break_time)
            if obj.break_time
            else "00:00:00"
        )

        overtime_str = (
            ReportGenerator.format_duration_for_display(obj.overtime)
            if obj.overtime
            else "00:00:00"
        )

        return format_html(
            '<div style="font-family: monospace; font-size: 11px; line-height: 1.3;">'
            '<div style="color: #2C3E50;"><strong>⏱️ Work: {}</strong></div>'
            '<div style="color: #7F8C8D;">📊 Total: {}</div>'
            '<div style="color: #7F8C8D;">☕ Break: {}</div>'
            '<div style="color: #E67E22;"><strong>⏰ OT: {}</strong></div>'
            "</div>",
            work_time_str,
            total_time_str,
            break_time_str,
            overtime_str,
        )

    time_summary.short_description = "⏱️ Time Summary"

    def performance_score(self, obj):
        if not obj.work_time:
            return format_html('<span style="color: #6C757D;">❓ No Data</span>')

        work_hours = TimeCalculator.duration_to_decimal_hours(obj.work_time)
        performance_percentage = obj.performance_score

        if performance_percentage >= 90:
            performance_color = "#27AE60"
            performance_icon = "🚀"
            performance_text = "Excellent"
        elif performance_percentage >= 75:
            performance_color = "#3498DB"
            performance_icon = "✅"
            performance_text = "Good"
        elif performance_percentage >= 60:
            performance_color = "#F39C12"
            performance_icon = "⚠️"
            performance_text = "Average"
        else:
            performance_color = "#E74C3C"
            performance_icon = "❌"
            performance_text = "Poor"

        return format_html(
            '<div style="font-size: 11px; line-height: 1.3;">'
            '<div style="color: {}; font-weight: bold;">{} {}</div>'
            '<div style="color: #7F8C8D;">📈 {}%</div>'
            "</div>",
            performance_color,
            performance_icon,
            performance_text,
            performance_percentage or 0,
        )

    performance_score.short_description = "📈 Performance"

    def punctuality_score(self, obj):
        punctuality_percentage = obj.punctuality_score
        late_mins = obj.late_minutes or 0
        early_mins = obj.early_departure_minutes or 0

        if punctuality_percentage >= 95:
            punctuality_color = "#27AE60"
            punctuality_icon = "🎯"
        elif punctuality_percentage >= 80:
            punctuality_color = "#3498DB"
            punctuality_icon = "✅"
        elif punctuality_percentage >= 60:
            punctuality_color = "#F39C12"
            punctuality_icon = "⚠️"
        else:
            punctuality_color = "#E74C3C"
            punctuality_icon = "❌"

        info_parts = []
        if late_mins > 0:
            info_parts.append(f"Late: {late_mins}m")
        if early_mins > 0:
            info_parts.append(f"Early: {early_mins}m")

        return format_html(
            '<div style="font-size: 11px; line-height: 1.3;">'
            '<div style="color: {}; font-weight: bold;">{} {}%</div>'
            '<div style="color: #7F8C8D;">{}</div>'
            "</div>",
            punctuality_color,
            punctuality_icon,
            punctuality_percentage or 100,
            " | ".join(info_parts) if info_parts else "On Time",
        )

    punctuality_score.short_description = "🎯 Punctuality"

    def shift_info(self, obj):
        if not obj.shift:
            return format_html('<span style="color: #6C757D;">❓ No Shift</span>')

        shift = obj.shift
        return format_html(
            '<div style="font-size: 11px;">'
            "<strong>{}</strong><br>"
            '<small style="color: #7F8C8D;">{} - {}</small>'
            "</div>",
            shift.name,
            shift.start_time.strftime("%H:%M"),
            shift.end_time.strftime("%H:%M"),
        )

    shift_info.short_description = "🕐 Shift"

    def in_1(self, obj):
        if obj.check_in_1:
            return format_html(
                '<div style="font-family: monospace; color: #27AE60; font-weight: bold;">{}</div>',
                obj.check_in_1.strftime("%H:%M")
            )
        return format_html('<span style="color: #6C757D;">--:--</span>')

    in_1.short_description = "🟢 IN 1"

    def out_1(self, obj):
        if obj.check_out_1:
            return format_html(
                '<div style="font-family: monospace; color: #E74C3C; font-weight: bold;">{}</div>',
                obj.check_out_1.strftime("%H:%M")
            )
        return format_html('<span style="color: #6C757D;">--:--</span>')

    out_1.short_description = "🔴 OUT 1"

    def in_2(self, obj):
        if obj.check_in_2:
            return format_html(
                '<div style="font-family: monospace; color: #27AE60; font-weight: bold;">{}</div>',
                obj.check_in_2.strftime("%H:%M")
            )
        return format_html('<span style="color: #6C757D;">--:--</span>')

    in_2.short_description = "🟢 IN 2"

    def out_2(self, obj):
        if obj.check_out_2:
            return format_html(
                '<div style="font-family: monospace; color: #E74C3C; font-weight: bold;">{}</div>',
                obj.check_out_2.strftime("%H:%M")
            )
        return format_html('<span style="color: #6C757D;">--:--</span>')

    out_2.short_description = "🔴 OUT 2"

    def in_3(self, obj):
        if obj.check_in_3:
            return format_html(
                '<div style="font-family: monospace; color: #27AE60; font-weight: bold;">{}</div>',
                obj.check_in_3.strftime("%H:%M")
            )
        return format_html('<span style="color: #6C757D;">--:--</span>')

    in_3.short_description = "🟢 IN 3"

    def out_3(self, obj):
        if obj.check_out_3:
            return format_html(
                '<div style="font-family: monospace; color: #E74C3C; font-weight: bold;">{}</div>',
                obj.check_out_3.strftime("%H:%M")
            )
        return format_html('<span style="color: #6C757D;">--:--</span>')

    out_3.short_description = "🔴 OUT 3"

    def actions_column(self, obj):
        return format_html(
            '<div style="white-space: nowrap;">'
            '<a href="{}" style="color: #3498DB; text-decoration: none; margin-right: 8px;" title="Edit">✏️</a>'
            '<a href="{}" style="color: #27AE60; text-decoration: none; margin-right: 8px;" title="View">👁️</a>'
            '<a href="#" onclick="return confirm(\'Delete this record?\');" style="color: #E74C3C; text-decoration: none;" title="Delete">🗑️</a>'
            "</div>",
            reverse("admin:attendance_attendance_change", args=[obj.pk]),
            reverse("admin:attendance_attendance_change", args=[obj.pk]),
        )

    actions_column.short_description = "⚙️ Actions"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user

        if obj.employee:
            time_pairs = obj.get_time_pairs()
            is_valid, errors = ValidationHelper.validate_attendance_consistency(
                time_pairs
            )
            if not is_valid:
                raise ValidationError("; ".join(errors))

            is_employee_valid, message = (
                EmployeeDataManager.validate_employee_for_attendance(obj.employee)
            )
            if not is_employee_valid:
                raise ValidationError(message)

            obj.calculate_attendance_metrics()

        super().save_model(request, obj, form, change)

        AuditHelper.log_attendance_change(
            user=request.user,
            action="UPDATE" if change else "CREATE",
            employee=obj.employee,
            attendance_date=obj.date,
            changes={"status": obj.status, "work_time": str(obj.work_time)},
            request=request,
        )

        CacheManager.invalidate_employee_cache(obj.employee.id)

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return check_attendance_permission(request.user, 'view_attendance')

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj:
            return EmployeeAttendancePermission.can_edit_employee_attendance(request.user, obj.employee)
        return check_attendance_permission(request.user, 'edit_employee_attendance')

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return request.user.role and request.user.role.name == 'SUPER_ADMIN'

    def get_queryset(self, request):
        if request.user.is_superuser:
            return super().get_queryset(request)

        qs = super().get_queryset(request)
        accessible_employees = get_accessible_employees(request.user)
        return qs.filter(employee__in=accessible_employees)

    def get_actions(self, request):
        if request.user.is_superuser:
            return super().get_actions(request)

        actions = super().get_actions(request)

        if not ReportPermission.can_export_attendance_data(request.user):
            if 'export_attendance_excel' in actions:
                del actions['export_attendance_excel']

        if not DevicePermission.can_sync_device_data(request.user):
            if 'sync_with_devices' in actions:
                del actions['sync_with_devices']

        if not BulkOperationPermission.can_bulk_update_attendance(request.user):
            if 'calculate_monthly_summaries' in actions:
                del actions['calculate_monthly_summaries']

        return actions


class AttendanceLogAdmin(admin.ModelAdmin):
    list_display = [
        "employee_display",
        "timestamp_display",
        "log_type_badge",
        "device_info",
        "status_indicator",
    ]
    list_filter = ["log_type", "device", "processing_status", "timestamp"]
    search_fields = [
        "employee_code",
        "employee__first_name",
        "employee__last_name",
        "device__device_name",
    ]
    list_per_page = 50
    ordering = ["-timestamp"]
    readonly_fields = ["id", "timestamp", "raw_data", "processed_at"]

    fieldsets = (
        (
            "📱 Device Information",
            {
                "fields": ("device", "device_location", "timestamp"),
                "classes": ("wide",),
            },
        ),
        (
            "👤 Employee Information",
            {"fields": ("employee", "employee_code"), "classes": ("wide",)},
        ),
        (
            "📝 Log Details",
            {
                "fields": ("log_type", "processing_status", "processed_at"),
                "classes": ("wide",),
            },
        ),
        (
            "🔧 Technical Data",
            {"fields": ("raw_data", "error_message"), "classes": ("collapse",)},
        ),
    )

    def employee_display(self, obj):
        if obj.employee:
            return format_html(
                "<div><strong>{}</strong><br><small>{}</small></div>",
                obj.employee.get_full_name(),
                obj.employee_code,
            )
        return format_html('<span style="color: #E74C3C;">{}</span>', obj.employee_code)

    employee_display.short_description = "👤 Employee"

    def timestamp_display(self, obj):
        return format_html(
            '<div style="font-family: monospace;">'
            "<strong>{}</strong><br>"
            '<small style="color: #7F8C8D;">{}</small>'
            "</div>",
            obj.timestamp.strftime("%Y-%m-%d"),
            obj.timestamp.strftime("%H:%M:%S"),
        )

    timestamp_display.short_description = "🕐 Timestamp"
    timestamp_display.admin_order_field = "timestamp"

    def log_type_badge(self, obj):
        type_config = {
            "CHECK_IN": {"color": "#27AE60", "icon": "🟢"},
            "CHECK_OUT": {"color": "#E74C3C", "icon": "🔴"},
            "BREAK_START": {"color": "#F39C12", "icon": "🟡"},
            "BREAK_END": {"color": "#3498DB", "icon": "🔵"},
            "OVERTIME_IN": {"color": "#9B59B6", "icon": "🟣"},
            "OVERTIME_OUT": {"color": "#E67E22", "icon": "🟠"},
            "MANUAL_ENTRY": {"color": "#34495E", "icon": "✋"},
        }

        config = type_config.get(obj.log_type, {"color": "#6C757D", "icon": "⚪"})

        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            config["color"],
            config["icon"],
            obj.log_type.replace("_", " ").title(),
        )

    log_type_badge.short_description = "📝 Log Type"

    def device_info(self, obj):
        return format_html(
            '<div style="font-size: 11px;">'
            "<strong>📱 {}</strong><br>"
            '<small style="color: #7F8C8D;">ID: {}</small>'
            "</div>",
            obj.device.device_name if obj.device else "Unknown",
            obj.device.device_id if obj.device else "N/A",
        )

    device_info.short_description = "📱 Device"

    def status_indicator(self, obj):
        status_config = {
            "PENDING": {"color": "#F39C12", "icon": "⏳"},
            "PROCESSED": {"color": "#27AE60", "icon": "✅"},
            "ERROR": {"color": "#E74C3C", "icon": "❌"},
            "DUPLICATE": {"color": "#6C757D", "icon": "🔄"},
            "IGNORED": {"color": "#95A5A6", "icon": "🚫"},
        }

        config = status_config.get(
            obj.processing_status, {"color": "#6C757D", "icon": "❓"}
        )

        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            config["color"],
            config["icon"],
            obj.processing_status.title(),
        )

    status_indicator.short_description = "⚙️ Status"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return DevicePermission.can_view_device_logs(request.user)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return DevicePermission.can_manage_devices(request.user)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_queryset(self, request):
        if request.user.is_superuser:
            return super().get_queryset(request)

        qs = super().get_queryset(request)
        accessible_employees = get_accessible_employees(request.user)
        return qs.filter(
            employee_code__in=[emp.employee_code for emp in accessible_employees]
        )

class MonthlyAttendanceSummaryAdmin(admin.ModelAdmin):
    list_display = [
        "employee_summary",
        "period_display",
        "attendance_metrics",
        "performance_score",
        "efficiency_display",
    ]
    list_filter = ["year", "month", "employee__department"]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
    ]
    list_per_page = 20
    ordering = ["-year", "-month", "employee__employee_code"]
    readonly_fields = ["id", "generated_at", "updated_at", "efficiency_score"]

    fieldsets = (
        (
            "👤 Employee & Period",
            {
                "fields": ("employee", "year", "month", "generated_by"),
                "classes": ("wide",),
            },
        ),
        (
            "⏰ Time Metrics",
            {
                "fields": (
                    ("total_work_time", "total_break_time"),
                    ("total_overtime", "total_undertime"),
                    ("earliest_in_time", "latest_out_time"),
                ),
                "classes": ("wide",),
            },
        ),
        (
            "📊 Attendance Counts",
            {
                "fields": (
                    ("working_days", "attended_days"),
                    ("half_days", "late_days"),
                    ("early_days", "absent_days"),
                    ("leave_days", "holiday_days"),
                ),
                "classes": ("wide",),
            },
        ),
        (
            "📈 Performance Metrics",
            {
                "fields": (
                    ("attendance_percentage", "punctuality_score"),
                    ("average_work_hours", "efficiency_score"),
                ),
                "classes": ("wide",),
            },
        ),
        (
            "📝 Metadata",
            {"fields": ("generated_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def employee_summary(self, obj):
        return format_html(
            "<div>"
            "<strong>{}</strong><br>"
            '<small style="color: #7F8C8D;">{}</small><br>'
            '<small style="color: #7F8C8D;">🏢 {}</small>'
            "</div>",
            obj.employee.get_full_name(),
            obj.employee.employee_code,
            obj.employee.department.name if obj.employee.department else "N/A",
        )

    employee_summary.short_description = "👤 Employee"

    def period_display(self, obj):
        month_str = str(obj.month).zfill(2) if obj.month else "00"
        
        return format_html(
            '<div style="text-align: center;">'
            '<strong style="color: #2C3E50;">{}/{}</strong><br>'
            '<small style="color: #7F8C8D;">📅 {} Days</small>'
            "</div>",
            obj.year,
            month_str,
            obj.working_days,
        )

    period_display.short_description = "📅 Period"

    def attendance_metrics(self, obj):
        return format_html(
            '<div style="font-size: 11px; line-height: 1.3;">'
            '<div style="color: #27AE60;"><strong>✅ Present: {}</strong></div>'
            '<div style="color: #E74C3C;">❌ Absent: {}</div>'
            '<div style="color: #F39C12;">⏰ Late: {}</div>'
            '<div style="color: #3498DB;">🕐 Half: {}</div>'
            "</div>",
            obj.attended_days,
            obj.absent_days,
            obj.late_days,
            obj.half_days,
        )

    attendance_metrics.short_description = "📊 Attendance"

    def performance_score(self, obj):
        attendance_pct = float(str(obj.attendance_percentage)) if obj.attendance_percentage else 0
        punctuality_pct = float(str(obj.punctuality_score)) if obj.punctuality_score else 0
        avg_hours = float(str(obj.average_work_hours)) if obj.average_work_hours else 0
        
        attendance_color = (
            "#27AE60"
            if attendance_pct >= 90
            else "#F39C12" if attendance_pct >= 75 else "#E74C3C"
        )
        punctuality_color = (
            "#27AE60"
            if punctuality_pct >= 90
            else "#F39C12" if punctuality_pct >= 75 else "#E74C3C"
        )

        return format_html(
            '<div style="font-size: 11px; line-height: 1.3;">'
            '<div style="color: {};">📈 Attendance: {}%</div>'
            '<div style="color: {};">🎯 Punctuality: {}%</div>'
            '<div style="color: #7F8C8D;">⏱️ Avg Hours: {}</div>'
            "</div>",
            attendance_color,
            round(attendance_pct, 1),
            punctuality_color,
            round(punctuality_pct, 1),
            round(avg_hours, 1),
        )

    performance_score.short_description = "📈 Performance"

    def efficiency_display(self, obj):
        efficiency = float(str(obj.efficiency_score)) if obj.efficiency_score else 0

        if efficiency >= 90:
            color = "#27AE60"
            icon = "🚀"
            label = "Excellent"
        elif efficiency >= 75:
            color = "#3498DB"
            icon = "✅"
            label = "Good"
        elif efficiency >= 60:
            color = "#F39C12"
            icon = "⚠️"
            label = "Average"
        else:
            color = "#E74C3C"
            icon = "❌"
            label = "Poor"

        return format_html(
            '<div style="text-align: center;">'
            '<div style="color: {}; font-weight: bold; font-size: 14px;">{}</div>'
            '<div style="color: {}; font-size: 12px;">{} {}%</div>'
            "</div>",
            color,
            icon,
            color,
            label,
            round(efficiency, 1),
        )

    efficiency_display.short_description = "⭐ Efficiency"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return check_attendance_permission(request.user, "view_attendance")

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return request.user.role and request.user.role.name in [
            "SUPER_ADMIN",
            "MANAGER",
        ]

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_queryset(self, request):
        if request.user.is_superuser:
            return super().get_queryset(request)

        qs = super().get_queryset(request)
        accessible_employees = get_accessible_employees(request.user)
        return qs.filter(employee__in=accessible_employees)
class AttendanceDeviceAdmin(admin.ModelAdmin):
    list_display = [
        "device_info",
        "connection_status",
        "location_info",
        "sync_status",
        "device_actions",
    ]
    list_filter = ["device_type", "status", "department", "is_active"]
    search_fields = ["device_id", "device_name", "ip_address", "location"]
    list_per_page = 20
    ordering = ["device_name"]

    fieldsets = (
        (
            "📱 Device Information",
            {
                "fields": ("device_id", "device_name", "device_type", "status"),
                "classes": ("wide",),
            },
        ),
        (
            "🌐 Network Configuration",
            {
                "fields": ("ip_address", "port", "location", "department"),
                "classes": ("wide",),
            },
        ),
        (
            "⚙️ Device Settings",
            {
                "fields": (
                    ("sync_interval_minutes", "max_users"),
                    ("max_transactions", "firmware_version"),
                ),
                "classes": ("wide",),
            },
        ),
        (
            "📊 Sync Information",
            {"fields": ("last_sync_time", "is_active"), "classes": ("wide",)},
        ),
        (
            "📝 Metadata",
            {
                "fields": ("created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = ["created_at", "updated_at", "last_sync_time"]

    def device_info(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #2C3E50;">{}</strong><br>'
            '<small style="color: #7F8C8D;">📱 {}</small><br>'
            '<small style="color: #7F8C8D;">🆔 {}</small>'
            "</div>",
            obj.device_name,
            obj.get_device_type_display(),
            obj.device_id,
        )

    device_info.short_description = "📱 Device"

    def connection_status(self, obj):
        status_config = {
            "ACTIVE": {"color": "#27AE60", "icon": "🟢", "bg": "#D5EDDA"},
            "INACTIVE": {"color": "#6C757D", "icon": "⚫", "bg": "#E2E3E5"},
            "MAINTENANCE": {"color": "#F39C12", "icon": "🟡", "bg": "#FFF3CD"},
            "ERROR": {"color": "#E74C3C", "icon": "🔴", "bg": "#F8D7DA"},
        }

        config = status_config.get(
            obj.status, {"color": "#6C757D", "icon": "❓", "bg": "#E2E3E5"}
        )

        return format_html(
            '<span style="background-color: {}; color: {}; padding: 4px 8px; '
            "border-radius: 12px; font-size: 12px; font-weight: bold; "
            'display: inline-block; min-width: 80px; text-align: center;">'
            "{} {}</span>",
            config["bg"],
            config["color"],
            config["icon"],
            obj.status.title(),
        )

    connection_status.short_description = "🔗 Status"

    def location_info(self, obj):
        return format_html(
            '<div style="font-size: 11px;">'
            "<div><strong>📍 {}</strong></div>"
            '<div style="color: #7F8C8D;">🌐 {}:{}</div>'
            '<div style="color: #7F8C8D;">🏢 {}</div>'
            "</div>",
            obj.location,
            obj.ip_address,
            obj.port,
            obj.department.name if obj.department else "N/A",
        )

    location_info.short_description = "📍 Location"

    def sync_status(self, obj):
        if obj.last_sync_time:
            time_diff = timezone.now() - obj.last_sync_time
            if time_diff.total_seconds() < 3600:
                sync_color = "#27AE60"
                sync_icon = "✅"
                sync_text = "Recent"
            elif time_diff.total_seconds() < 86400:
                sync_color = "#F39C12"
                sync_icon = "⚠️"
                sync_text = "Delayed"
            else:
                sync_color = "#E74C3C"
                sync_icon = "❌"
                sync_text = "Stale"

            return format_html(
                '<div style="font-size: 11px;">'
                '<div style="color: {};">{} {}</div>'
                '<div style="color: #7F8C8D;">{}</div>'
                "</div>",
                sync_color,
                sync_icon,
                sync_text,
                obj.last_sync_time.strftime("%Y-%m-%d %H:%M"),
            )
        else:
            return format_html('<span style="color: #6C757D;">❓ Never Synced</span>')

    sync_status.short_description = "🔄 Last Sync"

    def device_actions(self, obj):
        return format_html(
            '<div style="white-space: nowrap;">'
            '<a href="#" onclick="testConnection(\'{}\');" style="color: #3498DB; text-decoration: none; margin-right: 8px;" title="Test Connection">🔗</a>'
            '<a href="#" onclick="syncDevice(\'{}\');" style="color: #27AE60; text-decoration: none; margin-right: 8px;" title="Sync Now">🔄</a>'
            '<a href="{}" style="color: #F39C12; text-decoration: none;" title="Edit">✏️</a>'
            "</div>",
            obj.id,
            obj.id,
            reverse("admin:attendance_attendancedevice_change", args=[obj.pk]),
        )

    device_actions.short_description = "⚙️ Actions"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return DevicePermission.can_manage_devices(
            request.user
        ) or DevicePermission.can_view_device_logs(request.user)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return DevicePermission.can_manage_devices(request.user)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return DevicePermission.can_manage_devices(request.user)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return DevicePermission.can_manage_devices(request.user)

class AttendanceCorrectionAdmin(admin.ModelAdmin):
    list_display = ['correction_info', 'correction_type_badge', 'attendance_details', 'status_badge', 'approval_info']
    list_filter = ['correction_type', 'status', 'requested_at']
    search_fields = ['attendance__employee__employee_code', 'attendance__employee__first_name', 'reason']
    list_per_page = 25
    ordering = ['-requested_at']

    fieldsets = (
        (
            "📝 Correction Information",
            {"fields": ("attendance", "correction_type", "reason"), "classes": ("wide",)},
        ),
        (
            "🔄 Data Changes",
            {"fields": ("original_data", "corrected_data"), "classes": ("wide",)},
        ),
        (
            "✅ Approval Process",
            {"fields": ("status", "approved_by", "approved_at", "rejection_reason"), "classes": ("wide",)},
        ),
        (
            "📝 Metadata",
            {"fields": ("requested_by", "requested_at"), "classes": ("collapse",)},
        ),
    )

    readonly_fields = ["original_data", "requested_at", "approved_at"]

    def correction_info(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #2C3E50;">{}</strong><br>'
            '<small style="color: #7F8C8D;">📅 {}</small><br>'
            '<small style="color: #7F8C8D;">👤 {}</small>'
            "</div>",
            obj.attendance.employee.get_full_name(),
            obj.attendance.date.strftime("%Y-%m-%d"),
            obj.requested_by.get_full_name(),
        )
    correction_info.short_description = "📝 Correction"

    def correction_type_badge(self, obj):
        type_config = {
            "TIME_ADJUSTMENT": {"color": "#3498DB", "icon": "⏰"},
            "STATUS_CHANGE": {"color": "#9B59B6", "icon": "📊"},
            "MANUAL_ENTRY": {"color": "#E67E22", "icon": "✋"},
            "DEVICE_ERROR": {"color": "#E74C3C", "icon": "🔧"},
            "LEAVE_ADJUSTMENT": {"color": "#1ABC9C", "icon": "🏖️"},
        }

        config = type_config.get(obj.correction_type, {"color": "#6C757D", "icon": "❓"})

        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            config["color"],
            config["icon"],
            obj.get_correction_type_display(),
        )
    correction_type_badge.short_description = "🔄 Type"

    def attendance_details(self, obj):
        attendance = obj.attendance
        return format_html(
            '<div style="font-size: 11px;">'
            "<div><strong>📊 {}</strong></div>"
            '<div style="color: #7F8C8D;">⏱️ {}</div>'
            '<div style="color: #7F8C8D;">🕐 {} - {}</div>'
            "</div>",
            attendance.status,
            ReportGenerator.format_duration_for_display(attendance.work_time) if attendance.work_time else "00:00:00",
            attendance.first_in_time.strftime("%H:%M") if attendance.first_in_time else "--:--",
            attendance.last_out_time.strftime("%H:%M") if attendance.last_out_time else "--:--",
        )
    attendance_details.short_description = "📊 Attendance"

    def status_badge(self, obj):
        status_config = {
            "PENDING": {"color": "#F39C12", "icon": "⏳", "bg": "#FFF3CD"},
            "APPROVED": {"color": "#27AE60", "icon": "✅", "bg": "#D5EDDA"},
            "REJECTED": {"color": "#E74C3C", "icon": "❌", "bg": "#F8D7DA"},
        }

        config = status_config.get(obj.status, {"color": "#6C757D", "icon": "❓", "bg": "#E2E3E5"})

        return format_html(
            '<span style="background-color: {}; color: {}; padding: 4px 8px; '
            "border-radius: 12px; font-size: 12px; font-weight: bold; "
            'display: inline-block; min-width: 80px; text-align: center;">'
            "{} {}</span>",
            config["bg"],
            config["color"],
            config["icon"],
            obj.status.title(),
        )
    status_badge.short_description = "📊 Status"

    def approval_info(self, obj):
        if obj.approved_by:
            return format_html(
                '<div style="font-size: 11px;">'
                "<div><strong>{}</strong></div>"
                '<div style="color: #7F8C8D;">{}</div>'
                "</div>",
                obj.approved_by.get_full_name(),
                (
                    obj.approved_at.strftime("%Y-%m-%d %H:%M")
                    if obj.approved_at
                    else "N/A"
                ),
            )
        return format_html('<span style="color: #6C757D;">⏳ Pending</span>')
    approval_info.short_description = "✅ Approved By"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return check_attendance_permission(request.user, "view_attendance")

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj:
            return EmployeeAttendancePermission.can_approve_attendance_correction(
                request.user, obj.attendance.employee
            )
        return True

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return request.user.role and request.user.role.name == "SUPER_ADMIN"

    def get_queryset(self, request):
        if request.user.is_superuser:
            return super().get_queryset(request)
        
        qs = super().get_queryset(request)
        accessible_employees = get_accessible_employees(request.user)
        return qs.filter(attendance__employee__in=accessible_employees)


class AttendanceReportAdmin(admin.ModelAdmin):
    list_display = [
        "report_info",
        "report_type_badge",
        "date_range_display",
        "status_indicator",
        "generated_info",
    ]
    list_filter = ["report_type", "status", "generated_at"]
    search_fields = ["name", "generated_by__first_name", "generated_by__last_name"]
    list_per_page = 20
    ordering = ["-generated_at"]
    readonly_fields = ["id", "generated_at", "completed_at", "report_data"]

    fieldsets = (
        (
            "📊 Report Information",
            {"fields": ("name", "report_type", "status"), "classes": ("wide",)},
        ),
        ("📅 Date Range", {"fields": ("start_date", "end_date"), "classes": ("wide",)}),
        (
            "🎯 Filters & Scope",
            {"fields": ("employees", "departments", "filters"), "classes": ("wide",)},
        ),
        (
            "📁 Output",
            {"fields": ("file_path", "report_data"), "classes": ("collapse",)},
        ),
        (
            "📝 Metadata",
            {
                "fields": ("generated_by", "generated_at", "completed_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def report_info(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #2C3E50;">{}</strong><br>'
            '<small style="color: #7F8C8D;">👤 {}</small>'
            "</div>",
            obj.name,
            obj.generated_by.get_full_name(),
        )

    report_info.short_description = "📊 Report"

    def report_type_badge(self, obj):
        type_config = {
            "DAILY": {"color": "#3498DB", "icon": "📅"},
            "WEEKLY": {"color": "#9B59B6", "icon": "📆"},
            "MONTHLY": {"color": "#E67E22", "icon": "🗓️"},
            "CUSTOM": {"color": "#1ABC9C", "icon": "🔧"},
            "EMPLOYEE": {"color": "#F39C12", "icon": "👤"},
            "DEPARTMENT": {"color": "#E74C3C", "icon": "🏢"},
        }

        config = type_config.get(obj.report_type, {"color": "#6C757D", "icon": "❓"})

        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            config["color"],
            config["icon"],
            obj.get_report_type_display(),
        )

    report_type_badge.short_description = "📋 Type"

    def date_range_display(self, obj):
        return format_html(
            '<div style="font-family: monospace; font-size: 11px;">'
            "<strong>{}</strong><br>"
            '<small style="color: #7F8C8D;">to</small><br>'
            "<strong>{}</strong>"
            "</div>",
            obj.start_date.strftime("%Y-%m-%d"),
            obj.end_date.strftime("%Y-%m-%d"),
        )

    date_range_display.short_description = "📅 Period"

    def status_indicator(self, obj):
        status_config = {
            "GENERATING": {"color": "#F39C12", "icon": "⏳"},
            "COMPLETED": {"color": "#27AE60", "icon": "✅"},
            "FAILED": {"color": "#E74C3C", "icon": "❌"},
        }

        config = status_config.get(obj.status, {"color": "#6C757D", "icon": "❓"})

        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            config["color"],
            config["icon"],
            obj.status.title(),
        )

    status_indicator.short_description = "⚙️ Status"

    def generated_info(self, obj):
        return format_html(
            '<div style="font-size: 11px;">'
            "<div>{}</div>"
            '<div style="color: #7F8C8D;">{}</div>'
            "</div>",
            obj.generated_at.strftime("%Y-%m-%d"),
            obj.generated_at.strftime("%H:%M:%S"),
        )

    generated_info.short_description = "🕐 Generated"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return ReportPermission.can_generate_reports(request.user)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return ReportPermission.can_generate_reports(request.user)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return ReportPermission.can_generate_reports(request.user)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return request.user.role and request.user.role.name == 'SUPER_ADMIN'


class HolidayAdmin(admin.ModelAdmin):
    list_display = [
        "holiday_info",
        "holiday_type_badge",
        "date_display",
        "applicability_info",
        "holiday_status",
    ]
    list_filter = ["holiday_type", "is_optional", "is_paid", "is_active", "date"]
    search_fields = ["name", "description"]
    list_per_page = 25
    ordering = ["-date"]

    fieldsets = (
        (
            "🎉 Holiday Information",
            {
                "fields": ("name", "date", "holiday_type", "description"),
                "classes": ("wide",),
            },
        ),
        (
            "⚙️ Holiday Settings",
            {"fields": ("is_optional", "is_paid", "is_active"), "classes": ("wide",)},
        ),
        (
            "🎯 Applicability",
            {
                "fields": ("applicable_departments", "applicable_locations"),
                "classes": ("wide",),
            },
        ),
        (
            "📝 Metadata",
            {
                "fields": ("created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = ["created_at", "updated_at"]

    def holiday_info(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #2C3E50;">{}</strong><br>'
            '<small style="color: #7F8C8D;">{}</small>'
            "</div>",
            obj.name,
            (
                obj.description[:50] + "..."
                if obj.description and len(obj.description) > 50
                else obj.description or ""
            ),
        )

    holiday_info.short_description = "🎉 Holiday"

    def holiday_type_badge(self, obj):
        type_config = {
            "NATIONAL": {"color": "#E74C3C", "icon": "🇺🇸"},
            "RELIGIOUS": {"color": "#9B59B6", "icon": "🕊️"},
            "COMPANY": {"color": "#3498DB", "icon": "🏢"},
            "OPTIONAL": {"color": "#F39C12", "icon": "🔄"},
            "LOCAL": {"color": "#1ABC9C", "icon": "📍"},
        }

        config = type_config.get(obj.holiday_type, {"color": "#6C757D", "icon": "❓"})

        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            config["color"],
            config["icon"],
            obj.get_holiday_type_display(),
        )

    holiday_type_badge.short_description = "🏷️ Type"

    def date_display(self, obj):
        today = timezone.now().date()

        if obj.date == today:
            date_class = "color: #27AE60; font-weight: bold;"
            date_label = "📅 Today"
        elif obj.date > today:
            days_until = obj.days_until
            date_class = "color: #3498DB;"
            date_label = f"📅 In {days_until} days"
        else:
            date_class = "color: #7F8C8D;"
            date_label = "📅 Past"

        return format_html(
            '<div style="text-align: center;">'
            '<div style="{}"><strong>{}</strong></div>'
            '<div style="font-size: 10px; color: #7F8C8D;">{}</div>'
            "</div>",
            date_class,
            obj.date.strftime("%Y-%m-%d"),
            date_label,
        )

    date_display.short_description = "📅 Date"

    def applicability_info(self, obj):
        dept_count = obj.applicable_departments.count()
        location_count = (
            len(obj.applicable_locations) if obj.applicable_locations else 0
        )

        return format_html(
            '<div style="font-size: 11px;">'
            "<div>🏢 {} Departments</div>"
            "<div>📍 {} Locations</div>"
            "</div>",
            dept_count if dept_count > 0 else "All",
            location_count if location_count > 0 else "All",
        )

    applicability_info.short_description = "🎯 Scope"

    def holiday_status(self, obj):
        status_indicators = []

        if obj.is_active:
            status_indicators.append('<span style="color: #27AE60;">✅ Active</span>')
        else:
            status_indicators.append('<span style="color: #E74C3C;">❌ Inactive</span>')

        if obj.is_optional:
            status_indicators.append('<span style="color: #F39C12;">🔄 Optional</span>')

        if obj.is_paid:
            status_indicators.append('<span style="color: #27AE60;">💰 Paid</span>')
        else:
            status_indicators.append('<span style="color: #E74C3C;">💸 Unpaid</span>')

        return format_html(
            '<div style="font-size: 10px;">{}</div>', "<br>".join(status_indicators)
        )

    holiday_status.short_description = "🏷️ Status"

    def has_view_permission(self, request, obj=None):
        return True

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_holidays(request.user)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_holidays(request.user)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_holidays(request.user)


class EmployeeShiftAdmin(admin.ModelAdmin):
    list_display = [
        "employee_shift_info",
        "shift_details",
        "period_display",
        "assignment_status",
        "assigned_by_info",
    ]
    list_filter = ["shift", "is_temporary", "is_active", "effective_from"]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "shift__name",
    ]
    list_per_page = 25
    ordering = ["-effective_from", "employee__employee_code"]

    fieldsets = (
        (
            "👤 Employee & Shift",
            {"fields": ("employee", "shift"), "classes": ("wide",)},
        ),
        (
            "📅 Assignment Period",
            {
                "fields": ("effective_from", "effective_to", "is_temporary"),
                "classes": ("wide",),
            },
        ),
        (
            "📝 Assignment Details",
            {"fields": ("assigned_by", "notes", "is_active"), "classes": ("wide",)},
        ),
        (
            "📝 Metadata",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    readonly_fields = ["created_at", "updated_at"]

    def employee_shift_info(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #2C3E50;">{}</strong><br>'
            '<small style="color: #7F8C8D;">📧 {}</small><br>'
            '<small style="color: #7F8C8D;">🆔 {}</small>'
            "</div>",
            obj.employee.get_full_name(),
            obj.employee.email,
            obj.employee.employee_code,
        )

    employee_shift_info.short_description = "👤 Employee"

    def shift_details(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #6F42C1;">{}</strong><br>'
            '<small style="color: #7F8C8D;">🕐 {} - {}</small><br>'
            '<small style="color: #7F8C8D;">📋 {}</small>'
            "</div>",
            obj.shift.name,
            obj.shift.start_time.strftime("%H:%M"),
            obj.shift.end_time.strftime("%H:%M"),
            obj.shift.get_shift_type_display(),
        )

    shift_details.short_description = "🕐 Shift"

    def period_display(self, obj):
        if obj.effective_to:
            return format_html(
                '<div style="font-family: monospace; font-size: 11px;">'
                "<strong>{}</strong><br>"
                '<small style="color: #7F8C8D;">to</small><br>'
                "<strong>{}</strong><br>"
                '<small style="color: #7F8C8D;">({} days)</small>'
                "</div>",
                obj.effective_from.strftime("%Y-%m-%d"),
                obj.effective_to.strftime("%Y-%m-%d"),
                obj.duration_days,
            )
        else:
            return format_html(
                '<div style="font-family: monospace; font-size: 11px;">'
                "<strong>{}</strong><br>"
                '<small style="color: #7F8C8D;">to</small><br>'
                "<strong>Ongoing</strong>"
                "</div>",
                obj.effective_from.strftime("%Y-%m-%d"),
            )

    period_display.short_description = "📅 Period"

    def assignment_status(self, obj):
        status_indicators = []

        if obj.is_current:
            status_indicators.append('<span style="color: #27AE60;">✅ Current</span>')
        else:
            status_indicators.append(
                '<span style="color: #6C757D;">⏸️ Not Current</span>'
            )

        if obj.is_temporary:
            status_indicators.append(
                '<span style="color: #F39C12;">⏰ Temporary</span>'
            )
        else:
            status_indicators.append(
                '<span style="color: #3498DB;">📋 Permanent</span>'
            )

        if obj.is_active:
            status_indicators.append('<span style="color: #27AE60;">🟢 Active</span>')
        else:
            status_indicators.append('<span style="color: #E74C3C;">🔴 Inactive</span>')

        return format_html(
            '<div style="font-size: 10px;">{}</div>', "<br>".join(status_indicators)
        )

    assignment_status.short_description = "📊 Status"

    def assigned_by_info(self, obj):
        if obj.assigned_by:
            return format_html(
                '<div style="font-size: 11px;">'
                "<div><strong>{}</strong></div>"
                '<div style="color: #7F8C8D;">{}</div>'
                "</div>",
                obj.assigned_by.get_full_name(),
                obj.created_at.strftime("%Y-%m-%d") if obj.created_at else "N/A",
            )
        return format_html('<span style="color: #6C757D;">❓ System</span>')

    assigned_by_info.short_description = "👤 Assigned By"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_shifts(request.user)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_shifts(request.user)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_shifts(request.user)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_shifts(request.user)

    def get_queryset(self, request):
        if request.user.is_superuser:
            return super().get_queryset(request)

        qs = super().get_queryset(request)
        accessible_employees = get_accessible_employees(request.user)
        return qs.filter(employee__in=accessible_employees)

class ShiftAdmin(admin.ModelAdmin):
    list_display = [
        "shift_info",
        "timing_display",
        "shift_type_display",
        "status_display",
        "employee_count",
    ]
    list_filter = [
        "shift_type",
        "is_active",
        "is_night_shift",
        "weekend_applicable",
        "holiday_applicable",
    ]
    search_fields = ["name", "code"]
    list_per_page = 25
    ordering = ["name"]

    fieldsets = (
        (
            "🕐 Shift Information",
            {"fields": ("name", "code", "shift_type"), "classes": ("wide",)},
        ),
        (
            "⏰ Timing",
            {
                "fields": (
                    "start_time",
                    "end_time",
                    "working_hours",
                    "break_duration_minutes",
                ),
                "classes": ("wide",),
            },
        ),
        (
            "⚙️ Settings",
            {
                "fields": (
                    "grace_period_minutes",
                    "overtime_threshold_minutes",
                    "is_night_shift",
                    "weekend_applicable",
                    "holiday_applicable",
                    "is_active",
                ),
                "classes": ("wide",),
            },
        ),
        (
            "📝 Metadata",
            {
                "fields": ("created_at", "updated_at", "created_by"),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = ["created_at", "updated_at"]

    def shift_info(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #2C3E50;">{}</strong><br>'
            '<small style="color: #7F8C8D;">🆔 {}</small><br>'
            '<small style="color: #7F8C8D;">⏱️ {} hours</small>'
            "</div>",
            obj.name,
            obj.code,
            obj.working_hours,
        )

    shift_info.short_description = "🕐 Shift"

    def timing_display(self, obj):
        return format_html(
            '<div style="font-family: monospace;">'
            "<strong>{} - {}</strong><br>"
            '<small style="color: #7F8C8D;">Break: {} min</small><br>'
            '<small style="color: #7F8C8D;">Grace: {} min</small>'
            "</div>",
            obj.start_time.strftime("%H:%M"),
            obj.end_time.strftime("%H:%M"),
            obj.break_duration_minutes,
            obj.grace_period_minutes,
        )

    timing_display.short_description = "⏰ Timing"

    def shift_type_display(self, obj):
        return format_html(
            '<span style="background: #E3F2FD; color: #1976D2; padding: 2px 6px; border-radius: 3px; font-size: 11px; font-weight: bold;">'
            "{}"
            "</span>",
            obj.get_shift_type_display(),
        )

    shift_type_display.short_description = "📋 Type"

    def status_display(self, obj):
        from django.utils.safestring import mark_safe
        
        status_parts = []

        if obj.is_active:
            status_parts.append('<span style="color: #27AE60;">🟢 Active</span>')
        else:
            status_parts.append('<span style="color: #E74C3C;">🔴 Inactive</span>')

        if obj.is_night_shift:
            status_parts.append('<span style="color: #9B59B6;"> Night</span>')

        if obj.weekend_applicable:
            status_parts.append('<span style="color: #3498DB;">📅 Weekend</span>')

        if obj.holiday_applicable:
            status_parts.append('<span style="color: #F39C12;">🎉 Holiday</span>')

        return mark_safe('<div style="font-size: 10px;">' + '<br>'.join(status_parts) + '</div>')

    status_display.short_description = "📊 Status"

    def employee_count(self, obj):
        try:
            from django.apps import apps
            EmployeeShift = apps.get_model('attendance', 'EmployeeShift')
            count = EmployeeShift.objects.filter(shift=obj, is_active=True).count()
        except:
            try:
                count = obj.shift_employees.filter(is_active=True).count()
            except:
                try:
                    count = obj.employees.filter(is_active=True).count()
                except:
                    count = 0
            
        return format_html(
            '<span style="background: #F8F9FA; color: #2C3E50; padding: 2px 6px; border-radius: 3px; font-weight: bold; border: 1px solid #DEE2E6;">'
            "{} employees"
            "</span>",
            count,
        )

    employee_count.short_description = "👥 Assigned"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("created_by")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_shifts(request.user)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_shifts(request.user)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_shifts(request.user)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_shifts(request.user)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = [
        "leave_type_info",
        "category_badge",
        "allowance_info",
        "leave_settings",
        "leave_status",
    ]
    list_filter = [
        "category",
        "requires_approval",
        "requires_medical_certificate",
        "is_paid",
        "gender_specific",
        "is_active",
    ]
    search_fields = ["name", "code", "description"]
    list_per_page = 20
    ordering = ["name"]

    fieldsets = (
        (
            "🏖️ Leave Type Information",
            {
                "fields": ("name", "code", "category", "description"),
                "classes": ("wide",),
            },
        ),
        (
            "📊 Allowance & Limits",
            {
                "fields": (
                    ("days_allowed_per_year", "max_consecutive_days"),
                    ("min_notice_days", "carry_forward_allowed"),
                    ("carry_forward_max_days",),
                ),
                "classes": ("wide",),
            },
        ),
        (
            "⚙️ Leave Settings",
            {
                "fields": (
                    ("requires_approval", "requires_medical_certificate"),
                    ("is_paid", "applicable_after_probation_only"),
                    ("gender_specific", "is_active"),
                ),
                "classes": ("wide",),
            },
        ),
        (
            "📝 Metadata",
            {
                "fields": ("created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = ["created_at", "updated_at"]

    def leave_type_info(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #2C3E50;">{}</strong><br>'
            '<small style="color: #7F8C8D;">📋 {}</small><br>'
            '<small style="color: #7F8C8D;">{}</small>'
            "</div>",
            obj.name,
            obj.code,
            (
                obj.description[:50] + "..."
                if obj.description and len(obj.description) > 50
                else obj.description or ""
            ),
        )

    leave_type_info.short_description = "🏖️ Leave Type"

    def category_badge(self, obj):
        category_config = {
            "ANNUAL": {"color": "#3498DB", "icon": "📅"},
            "SICK": {"color": "#E74C3C", "icon": "🏥"},
            "MATERNITY": {"color": "#E91E63", "icon": "👶"},
            "PATERNITY": {"color": "#2196F3", "icon": "👨‍👶"},
            "EMERGENCY": {"color": "#FF5722", "icon": "🚨"},
            "STUDY": {"color": "#9C27B0", "icon": "📚"},
            "UNPAID": {"color": "#607D8B", "icon": "💸"},
            "COMPENSATORY": {"color": "#4CAF50", "icon": "🔄"},
        }

        config = category_config.get(obj.category, {"color": "#6C757D", "icon": "❓"})

        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            config["color"],
            config["icon"],
            obj.get_category_display(),
        )

    category_badge.short_description = "🏷️ Category"

    def allowance_info(self, obj):
        return format_html(
            '<div style="font-size: 11px;">'
            "<div><strong>📊 {} days/year</strong></div>"
            '<div style="color: #7F8C8D;">Max Consecutive: {}</div>'
            '<div style="color: #7F8C8D;">Notice: {} days</div>'
            "</div>",
            obj.days_allowed_per_year,
            obj.max_consecutive_days or "No limit",
            obj.min_notice_days,
        )

    allowance_info.short_description = "📊 Allowance"

    def leave_settings(self, obj):
        settings = []

        if obj.requires_approval:
            settings.append('<span style="color: #F39C12;">✅ Approval Required</span>')

        if obj.requires_medical_certificate:
            settings.append('<span style="color: #E74C3C;">🏥 Medical Cert</span>')

        if obj.is_paid:
            settings.append('<span style="color: #27AE60;">💰 Paid</span>')
        else:
            settings.append('<span style="color: #E74C3C;">💸 Unpaid</span>')

        if obj.carry_forward_allowed:
            settings.append(
                f'<span style="color: #3498DB;">🔄 CF: {obj.carry_forward_max_days}</span>'
            )

        return format_html(
            '<div style="font-size: 10px;">{}</div>', "<br>".join(settings)
        )

    leave_settings.short_description = "⚙️ Settings"

    def leave_status(self, obj):
        status_indicators = []

        if obj.is_active:
            status_indicators.append('<span style="color: #27AE60;">✅ Active</span>')
        else:
            status_indicators.append('<span style="color: #E74C3C;">❌ Inactive</span>')

        if obj.gender_specific != "A":
            gender_label = "Male" if obj.gender_specific == "M" else "Female"
            status_indicators.append(
                f'<span style="color: #9B59B6;">👤 {gender_label}</span>'
            )

        if obj.applicable_after_probation_only:
            status_indicators.append(
                '<span style="color: #F39C12;">⏳ Post-Probation</span>'
            )

        return format_html(
            '<div style="font-size: 10px;">{}</div>', "<br>".join(status_indicators)
        )

    leave_status.short_description = "🏷️ Status"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_leave_types(request.user)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_leave_types(request.user)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_leave_types(request.user)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return SystemPermission.can_manage_leave_types(request.user)


class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = [
        "employee_leave_info",
        "balance_summary",
        "utilization_display",
        "balance_breakdown",
        "last_updated_info",
    ]
    list_filter = ["leave_type", "year", "employee__department"]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "leave_type__name",
    ]
    list_per_page = 25
    ordering = ["-year", "employee__employee_code", "leave_type__name"]

    fieldsets = (
        (
            "👤 Employee & Leave Type",
            {"fields": ("employee", "leave_type", "year"), "classes": ("wide",)},
        ),
        (
            "📊 Balance Details",
            {
                "fields": (
                    ("allocated_days", "used_days"),
                    ("carried_forward_days", "adjustment_days"),
                ),
                "classes": ("wide",),
            },
        ),
        (
            "📝 Metadata",
            {"fields": ("updated_by", "last_updated"), "classes": ("collapse",)},
        ),
    )

    readonly_fields = ["last_updated"]

    def employee_leave_info(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #2C3E50;">{}</strong><br>'
            '<small style="color: #7F8C8D;">🏖️ {}</small><br>'
            '<small style="color: #7F8C8D;">📅 {}</small>'
            "</div>",
            obj.employee.get_full_name(),
            obj.leave_type.name,
            obj.year,
        )

    employee_leave_info.short_description = "👤 Employee & Leave"

    def balance_summary(self, obj):
        available = obj.available_days
        total_entitled = (
            obj.allocated_days + obj.carried_forward_days + obj.adjustment_days
        )

        if available > 0:
            balance_color = "#27AE60"
            balance_icon = "✅"
        elif available == 0:
            balance_color = "#F39C12"
            balance_icon = "⚠️"
        else:
            balance_color = "#E74C3C"
            balance_icon = "❌"

        return format_html(
            '<div style="text-align: center;">'
            '<div style="color: {}; font-weight: bold; font-size: 14px;">{} {}</div>'
            '<div style="color: #7F8C8D; font-size: 11px;">Available</div>'
            '<div style="color: #7F8C8D; font-size: 10px;">of {} entitled</div>'
            "</div>",
            balance_color,
            balance_icon,
            available,
            total_entitled,
        )

    balance_summary.short_description = "📊 Available"

    def utilization_display(self, obj):
        percentage = obj.utilization_percentage

        if percentage <= 50:
            util_color = "#27AE60"
            util_icon = "🟢"
        elif percentage <= 80:
            util_color = "#F39C12"
            util_icon = "🟡"
        else:
            util_color = "#E74C3C"
            util_icon = "🔴"

        return format_html(
            '<div style="text-align: center;">'
            '<div style="color: {}; font-weight: bold;">{} {}%</div>'
            '<div style="color: #7F8C8D; font-size: 11px;">Utilized</div>'
            "</div>",
            util_color,
            util_icon,
            percentage,
        )

    utilization_display.short_description = "📈 Utilization"

    def balance_breakdown(self, obj):
        return format_html(
            '<div style="font-size: 11px; line-height: 1.3;">'
            "<div>📊 Allocated: {}</div>"
            '<div style="color: #E74C3C;">📉 Used: {}</div>'
            '<div style="color: #3498DB;">🔄 Carried: {}</div>'
            '<div style="color: #F39C12;">⚖️ Adjusted: {}</div>'
            "</div>",
            obj.allocated_days,
            obj.used_days,
            obj.carried_forward_days,
            obj.adjustment_days,
        )

    balance_breakdown.short_description = "📋 Breakdown"

    def last_updated_info(self, obj):
        return format_html(
            '<div style="font-size: 11px;">'
            "<div>{}</div>"
            '<div style="color: #7F8C8D;">{}</div>'
            "</div>",
            obj.last_updated.strftime("%Y-%m-%d") if obj.last_updated else "Never",
            obj.updated_by.get_full_name() if obj.updated_by else "System",
        )

    last_updated_info.short_description = "🕐 Updated"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return LeavePermission.can_view_leave_balances(request.user)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return LeavePermission.can_manage_leave_balances(request.user)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return LeavePermission.can_manage_leave_balances(request.user)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return LeavePermission.can_manage_leave_balances(request.user)

    def get_queryset(self, request):
        if request.user.is_superuser:
            return super().get_queryset(request)

        qs = super().get_queryset(request)
        accessible_employees = get_accessible_employees(request.user)
        return qs.filter(employee__in=accessible_employees)


class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = [
        "employee_info",
        "leave_details",
        "date_range",
        "status_badge",
        "approval_info",
    ]
    list_filter = ["status", "leave_type", "is_half_day", "applied_at"]
    search_fields = [
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "reason",
    ]
    list_per_page = 25
    ordering = ["-applied_at"]

    fieldsets = (
        (
            "👤 Employee & Leave Type",
            {"fields": ("employee", "leave_type"), "classes": ("wide",)},
        ),
        (
            "📅 Leave Period",
            {
                "fields": (
                    ("start_date", "end_date"),
                    ("total_days", "is_half_day"),
                    ("half_day_period",),
                ),
                "classes": ("wide",),
            },
        ),
        (
            "📝 Request Details",
            {
                "fields": (
                    "reason",
                    "medical_certificate",
                    "emergency_contact_during_leave",
                    "handover_notes",
                ),
                "classes": ("wide",),
            },
        ),
        (
            "✅ Approval Process",
            {
                "fields": ("status", "approved_by", "approved_at", "rejection_reason"),
                "classes": ("wide",),
            },
        ),
        (
            "📝 Metadata",
            {"fields": ("applied_at",), "classes": ("collapse",)},
        ),
    )

    readonly_fields = ["total_days", "applied_at", "approved_at"]

    def employee_info(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #2C3E50;">{}</strong><br>'
            '<small style="color: #7F8C8D;">📧 {}</small><br>'
            '<small style="color: #7F8C8D;">🆔 {}</small>'
            "</div>",
            obj.employee.get_full_name(),
            obj.employee.email,
            obj.employee.employee_code,
        )

    employee_info.short_description = "👤 Employee"

    def leave_details(self, obj):
        return format_html(
            "<div>"
            '<strong style="color: #6F42C1;">{}</strong><br>'
            '<small style="color: #7F8C8D;">📊 {} days</small><br>'
            '<small style="color: #7F8C8D;">{}</small>'
            "</div>",
            obj.leave_type.name,
            obj.total_days,
            "🕐 Half Day" if obj.is_half_day else "📅 Full Day(s)",
        )

    leave_details.short_description = "🏖️ Leave Type"

    def date_range(self, obj):
        if obj.start_date == obj.end_date:
            return format_html(
                '<div style="text-align: center;">'
                "<strong>{}</strong><br>"
                '<small style="color: #7F8C8D;">{}</small>'
                "</div>",
                obj.start_date.strftime("%Y-%m-%d"),
                obj.half_day_period if obj.is_half_day else "Single Day",
            )
        else:
            return format_html(
                '<div style="text-align: center;">'
                "<strong>{}</strong><br>"
                '<small style="color: #7F8C8D;">to</small><br>'
                "<strong>{}</strong>"
                "</div>",
                obj.start_date.strftime("%Y-%m-%d"),
                obj.end_date.strftime("%Y-%m-%d"),
            )

    date_range.short_description = "📅 Period"

    def status_badge(self, obj):
        status_config = {
            "PENDING": {"color": "#F39C12", "icon": "⏳", "bg": "#FFF3CD"},
            "APPROVED": {"color": "#27AE60", "icon": "✅", "bg": "#D5EDDA"},
            "REJECTED": {"color": "#E74C3C", "icon": "❌", "bg": "#F8D7DA"},
            "CANCELLED": {"color": "#6C757D", "icon": "🚫", "bg": "#E2E3E5"},
            "WITHDRAWN": {"color": "#95A5A6", "icon": "↩️", "bg": "#F8F9FA"},
        }

        config = status_config.get(
            obj.status, {"color": "#6C757D", "icon": "❓", "bg": "#E2E3E5"}
        )

        return format_html(
            '<span style="background-color: {}; color: {}; padding: 4px 8px; '
            "border-radius: 12px; font-size: 12px; font-weight: bold; "
            'display: inline-block; min-width: 80px; text-align: center;">'
            "{} {}</span>",
            config["bg"],
            config["color"],
            config["icon"],
            obj.status.title(),
        )

    status_badge.short_description = "📊 Status"

    def approval_info(self, obj):
        if obj.approved_by:
            return format_html(
                '<div style="font-size: 11px;">'
                "<div><strong>{}</strong></div>"
                '<div style="color: #7F8C8D;">{}</div>'
                "</div>",
                obj.approved_by.get_full_name(),
                (
                    obj.approved_at.strftime("%Y-%m-%d %H:%M")
                    if obj.approved_at
                    else "N/A"
                ),
            )
        return format_html('<span style="color: #6C757D;">⏳ Pending</span>')

    approval_info.short_description = "✅ Approved By"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return check_attendance_permission(request.user, "view_attendance")

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj:
            return LeavePermission.can_approve_leave(request.user, obj)
        return True

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return request.user.role and request.user.role.name == "SUPER_ADMIN"

    def get_queryset(self, request):
        if request.user.is_superuser:
            return super().get_queryset(request)

        qs = super().get_queryset(request)
        accessible_employees = get_accessible_employees(request.user)
        return qs.filter(employee__in=accessible_employees)


admin.site.register(Attendance, AttendanceAdmin)
admin.site.register(AttendanceLog, AttendanceLogAdmin)
admin.site.register(MonthlyAttendanceSummary, MonthlyAttendanceSummaryAdmin)
admin.site.register(AttendanceDevice, AttendanceDeviceAdmin)
admin.site.register(AttendanceCorrection, AttendanceCorrectionAdmin)
admin.site.register(AttendanceReport, AttendanceReportAdmin)
admin.site.register(Holiday, HolidayAdmin)
admin.site.register(EmployeeShift, EmployeeShiftAdmin)
admin.site.register(Shift, ShiftAdmin)
admin.site.register(LeaveType, LeaveTypeAdmin)
admin.site.register(LeaveBalance, LeaveBalanceAdmin)
admin.site.register(LeaveRequest, LeaveRequestAdmin)

admin.site.site_header = "Attendance Management"
admin.site.site_title = "Attendance Admin"
admin.site.index_title = "Attendance Dashboard"
