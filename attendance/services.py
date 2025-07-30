from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum, Count, Avg
from accounts.models import CustomUser, Department, SystemConfiguration
from employees.models import EmployeeProfile, Contract
from .models import (
    Attendance,
    AttendanceLog,
    AttendanceDevice,
    Shift,
    EmployeeShift,
    LeaveRequest,
    LeaveBalance,
    LeaveType,
    Holiday,
    MonthlyAttendanceSummary,
    AttendanceCorrection,
    AttendanceReport,
)
from .utils import (
    TimeCalculator,
    EmployeeDataManager,
    AttendanceCalculator,
    DeviceDataProcessor,
    DeviceManager,
    ValidationHelper,
    AuditHelper,
    CacheManager,
    ExcelProcessor,
    get_current_date,
    get_current_datetime,
    safe_decimal_conversion,
)
from .permissions import (
    EmployeeAttendancePermission,
    LeavePermission,
    DevicePermission,
    ReportPermission,
    check_attendance_permission,
)
from datetime import datetime, date, time, timedelta
from decimal import Decimal
import socket
import json


class AttendanceService:
    @staticmethod
    def create_or_get_attendance_record(employee, attendance_date, created_by=None):
        attendance, created = Attendance.objects.get_or_create(
            employee=employee,
            date=attendance_date,
            defaults={
                "status": "ABSENT",
                "is_weekend": attendance_date.weekday() >= 5,
                "is_holiday": Holiday.active.filter(date=attendance_date).exists(),
                "is_manual_entry": True,
                "created_by": created_by,
            },
        )

        if created:
            shift = AttendanceService.get_employee_shift_for_date(
                employee, attendance_date
            )
            if shift:
                attendance.shift = shift
                attendance.save(update_fields=["shift"])

        return attendance, created

    @staticmethod
    def get_employee_shift_for_date(employee, target_date):
        employee_shift = (
            EmployeeShift.objects.filter(
                employee=employee, effective_from__lte=target_date, is_active=True
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=target_date))
            .first()
        )

        return employee_shift.shift if employee_shift else None

    @staticmethod
    def update_attendance_from_manual_entry(attendance_id, time_data, user):
        try:
            attendance = Attendance.objects.get(id=attendance_id)

            if not EmployeeAttendancePermission.can_edit_employee_attendance(
                user, attendance.employee
            ):
                raise ValidationError(
                    "You don't have permission to edit this attendance record"
                )

            time_pairs = [
                (time_data.get("check_in_1"), time_data.get("check_out_1")),
                (time_data.get("check_in_2"), time_data.get("check_out_2")),
                (time_data.get("check_in_3"), time_data.get("check_out_3")),
                (time_data.get("check_in_4"), time_data.get("check_out_4")),
                (time_data.get("check_in_5"), time_data.get("check_out_5")),
                (time_data.get("check_in_6"), time_data.get("check_out_6")),
            ]

            is_valid, errors = ValidationHelper.validate_attendance_consistency(
                time_pairs
            )
            if not is_valid:
                raise ValidationError("; ".join(errors))

            attendance.set_time_pairs(time_pairs)
            attendance.is_manual_entry = True
            attendance.notes = time_data.get("notes", attendance.notes)
            attendance._attendance_changed_by = user
            attendance.save()

            return attendance

        except Attendance.DoesNotExist:
            raise ValidationError("Attendance record not found")

    @staticmethod
    def bulk_create_attendance_records(
        employees, start_date, end_date, created_by=None
    ):
        created_records = []
        current_date = start_date

        while current_date <= end_date:
            if current_date.weekday() < 5:
                for employee in employees:
                    attendance, created = (
                        AttendanceService.create_or_get_attendance_record(
                            employee, current_date, created_by
                        )
                    )
                    if created:
                        created_records.append(attendance)
            current_date += timedelta(days=1)

        return created_records

    @staticmethod
    def get_employee_attendance_summary(employee, start_date, end_date):
        attendance_records = Attendance.objects.filter(
            employee=employee, date__range=[start_date, end_date]
        ).order_by("date")

        total_days = (end_date - start_date).days + 1
        working_days = sum(
            1
            for d in range(total_days)
            if (start_date + timedelta(days=d)).weekday() < 5
        )

        present_days = attendance_records.filter(status__in=["PRESENT", "LATE"]).count()
        absent_days = attendance_records.filter(status="ABSENT").count()
        half_days = attendance_records.filter(status="HALF_DAY").count()
        late_days = attendance_records.filter(status="LATE").count()
        leave_days = attendance_records.filter(status="LEAVE").count()
        holiday_days = attendance_records.filter(status="HOLIDAY").count()

        total_work_time = sum(
            (record.work_time for record in attendance_records if record.work_time),
            timedelta(0),
        )

        total_overtime = sum(
            (record.overtime for record in attendance_records if record.overtime),
            timedelta(0),
        )

        attendance_percentage = (
            (present_days / working_days * 100) if working_days > 0 else 0
        )

        return {
            "employee": employee,
            "period": f"{start_date} to {end_date}",
            "total_days": total_days,
            "working_days": working_days,
            "present_days": present_days,
            "absent_days": absent_days,
            "half_days": half_days,
            "late_days": late_days,
            "leave_days": leave_days,
            "holiday_days": holiday_days,
            "total_work_time": total_work_time,
            "total_overtime": total_overtime,
            "attendance_percentage": round(attendance_percentage, 2),
            "records": attendance_records,
        }

    @staticmethod
    def get_department_attendance_summary(department, target_date):
        department_employees = department.employees.filter(is_active=True)

        attendance_records = Attendance.objects.filter(
            employee__in=department_employees, date=target_date
        ).select_related("employee")

        summary = {
            "department": department,
            "date": target_date,
            "total_employees": department_employees.count(),
            "present": attendance_records.filter(
                status__in=["PRESENT", "LATE"]
            ).count(),
            "absent": attendance_records.filter(status="ABSENT").count(),
            "late": attendance_records.filter(status="LATE").count(),
            "half_day": attendance_records.filter(status="HALF_DAY").count(),
            "on_leave": attendance_records.filter(status="LEAVE").count(),
            "holiday": attendance_records.filter(status="HOLIDAY").count(),
            "records": attendance_records,
        }

        if summary["total_employees"] > 0:
            summary["attendance_percentage"] = (
                summary["present"] / summary["total_employees"] * 100
            )
        else:
            summary["attendance_percentage"] = 0

        return summary

    @staticmethod
    def process_attendance_correction_request(
        attendance_id, correction_data, requested_by
    ):
        try:
            attendance = Attendance.objects.get(id=attendance_id)

            if not EmployeeAttendancePermission.can_edit_employee_attendance(
                requested_by, attendance.employee
            ):
                raise ValidationError(
                    "You don't have permission to request correction for this attendance"
                )

            correction = AttendanceCorrection.objects.create(
                attendance=attendance,
                correction_type=correction_data["correction_type"],
                reason=correction_data["reason"],
                corrected_data=correction_data["corrected_data"],
                requested_by=requested_by,
            )

            return correction

        except Attendance.DoesNotExist:
            raise ValidationError("Attendance record not found")

    @staticmethod
    def approve_attendance_correction(correction_id, approved_by, approval_notes=None):
        try:
            correction = AttendanceCorrection.objects.get(id=correction_id)

            if not EmployeeAttendancePermission.can_approve_attendance_correction(
                approved_by, correction.attendance.employee
            ):
                raise ValidationError(
                    "You don't have permission to approve this correction"
                )

            if correction.status != "PENDING":
                raise ValidationError("Correction is not in pending status")

            correction.approve(approved_by)

            return correction

        except AttendanceCorrection.DoesNotExist:
            raise ValidationError("Attendance correction not found")


class DeviceService:
    @staticmethod
    def sync_device_data(device):
        if not device.is_active or device.status != "ACTIVE":
            return {"success": False, "error": "Device is not active"}

        try:
            sock = DeviceManager.connect_to_realand_device(
                device.ip_address, device.port
            )
            if not sock:
                device.status = "ERROR"
                device.save(update_fields=["status"])
                return {"success": False, "error": "Cannot connect to device"}

            today = get_current_date()
            yesterday = today - timedelta(days=1)

            logs = DeviceManager.get_attendance_logs_from_device(sock, yesterday, today)
            sock.close()

            processed_logs = []
            for log_data in logs:
                log = AttendanceLog.objects.create(
                    employee_code=log_data["employee_id"],
                    device=device,
                    timestamp=log_data["timestamp"],
                    log_type=log_data["log_type"],
                    device_location=device.location,
                    raw_data=log_data,
                    processing_status="PENDING",
                )
                processed_logs.append(log)

            device.last_sync_time = get_current_datetime()
            device.save(update_fields=["last_sync_time"])

            return {
                "success": True,
                "logs_synced": len(processed_logs),
                "sync_time": device.last_sync_time,
            }

        except Exception as e:
            device.status = "ERROR"
            device.save(update_fields=["status"])
            return {"success": False, "error": str(e)}

    @staticmethod
    def sync_employees_to_device(device):
        if not device.is_active:
            return {"success": False, "error": "Device is not active"}

        try:
            sock = DeviceManager.connect_to_realand_device(
                device.ip_address, device.port
            )
            if not sock:
                return {"success": False, "error": "Cannot connect to device"}

            active_employees = EmployeeDataManager.get_active_employees()
            synced_count = 0

            for employee in active_employees:
                success = DeviceManager.sync_employee_to_device(sock, employee)
                if success:
                    synced_count += 1

            sock.close()

            return {
                "success": True,
                "employees_synced": synced_count,
                "total_employees": active_employees.count(),
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def process_employee_daily_logs(logs):
        if not logs:
            return

        employee_code = logs[0].employee_code
        log_date = logs[0].timestamp.date()

        employee = EmployeeDataManager.get_employee_by_code(employee_code)
        if not employee:
            for log in logs:
                log.mark_as_error(f"Employee not found: {employee_code}")
            return

        attendance, created = AttendanceService.create_or_get_attendance_record(
            employee, log_date
        )

        log_data = []
        for log in logs:
            log_data.append(
                {
                    "timestamp": log.timestamp,
                    "log_type": log.log_type,
                    "device": log.device,
                    "employee_code": log.employee_code,
                }
            )
            log.employee = employee
            log.save(update_fields=["employee"])

        attendance.update_from_device_logs(log_data)

        for log in logs:
            log.mark_as_processed()

    @staticmethod
    def get_device_status_summary():
        devices = AttendanceDevice.objects.all()

        summary = {
            "total_devices": devices.count(),
            "active_devices": devices.filter(status="ACTIVE").count(),
            "inactive_devices": devices.filter(status="INACTIVE").count(),
            "error_devices": devices.filter(status="ERROR").count(),
            "maintenance_devices": devices.filter(status="MAINTENANCE").count(),
            "devices": [],
        }

        for device in devices:
            device_info = {
                "id": device.id,
                "name": device.device_name,
                "type": device.device_type,
                "status": device.status,
                "location": device.location,
                "last_sync": device.last_sync_time,
                "connection_test": device.test_connection(),
            }
            summary["devices"].append(device_info)

        return summary


class LeaveService:
    @staticmethod
    def apply_leave_request(employee, leave_data, applied_by=None):
        if not LeavePermission.can_apply_leave(applied_by or employee, employee):
            raise ValidationError(
                "You don't have permission to apply leave for this employee"
            )

        try:
            leave_type = LeaveType.objects.get(
                id=leave_data["leave_type_id"], is_active=True
            )
        except LeaveType.DoesNotExist:
            raise ValidationError("Invalid leave type")

        start_date = leave_data["start_date"]
        end_date = leave_data["end_date"]
        is_half_day = leave_data.get("is_half_day", False)

        leave_request = LeaveRequest(
            employee=employee,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            reason=leave_data["reason"],
            is_half_day=is_half_day,
            half_day_period=leave_data.get("half_day_period"),
            emergency_contact_during_leave=leave_data.get("emergency_contact"),
            handover_notes=leave_data.get("handover_notes"),
        )

        leave_request.calculate_total_days()

        leave_balance = LeaveService.get_employee_leave_balance(
            employee, leave_type, start_date.year
        )

        if not leave_balance.can_apply_leave(leave_request.total_days):
            raise ValidationError(
                f"Insufficient leave balance. Available: {leave_balance.available_days} days"
            )

        leave_request.full_clean()
        leave_request.save()

        return leave_request

    @staticmethod
    def approve_leave_request(leave_request_id, approved_by, approval_notes=None):
        try:
            leave_request = LeaveRequest.objects.get(id=leave_request_id)

            if not LeavePermission.can_approve_leave(approved_by, leave_request):
                raise ValidationError(
                    "You don't have permission to approve this leave request"
                )

            if leave_request.status != "PENDING":
                raise ValidationError("Leave request is not in pending status")

            leave_request.approve(approved_by)

            return leave_request

        except LeaveRequest.DoesNotExist:
            raise ValidationError("Leave request not found")

    @staticmethod
    def reject_leave_request(leave_request_id, rejected_by, rejection_reason):
        try:
            leave_request = LeaveRequest.objects.get(id=leave_request_id)

            if not LeavePermission.can_approve_leave(rejected_by, leave_request):
                raise ValidationError(
                    "You don't have permission to reject this leave request"
                )

            if leave_request.status != "PENDING":
                raise ValidationError("Leave request is not in pending status")

            leave_request.reject(rejected_by, rejection_reason)

            return leave_request

        except LeaveRequest.DoesNotExist:
            raise ValidationError("Leave request not found")

    @staticmethod
    def cancel_leave_request(leave_request_id, cancelled_by):
        try:
            leave_request = LeaveRequest.objects.get(id=leave_request_id)

            if leave_request.employee != cancelled_by:
                if not LeavePermission.can_approve_leave(cancelled_by, leave_request):
                    raise ValidationError(
                        "You don't have permission to cancel this leave request"
                    )

            if not leave_request.can_be_cancelled:
                raise ValidationError("Leave request cannot be cancelled")

            if leave_request.status == "APPROVED":
                leave_balance = LeaveService.get_employee_leave_balance(
                    leave_request.employee,
                    leave_request.leave_type,
                    leave_request.start_date.year,
                )
                leave_balance.add_leave(leave_request.total_days)

                LeaveService.remove_leave_attendance_records(leave_request)

            leave_request.status = "CANCELLED"
            leave_request.save(update_fields=["status"])

            return leave_request

        except LeaveRequest.DoesNotExist:
            raise ValidationError("Leave request not found")

    @staticmethod
    def get_employee_leave_balance(employee, leave_type, year):
        balance, created = LeaveBalance.objects.get_or_create(
            employee=employee,
            leave_type=leave_type,
            year=year,
            defaults={
                "allocated_days": Decimal(str(leave_type.days_allowed_per_year)),
                "used_days": Decimal("0.00"),
                "carried_forward_days": Decimal("0.00"),
                "adjustment_days": Decimal("0.00"),
            },
        )
        return balance

    @staticmethod
    def get_employee_all_leave_balances(employee, year):
        active_leave_types = LeaveType.active.all()
        balances = []

        for leave_type in active_leave_types:
            if leave_type.applicable_after_probation_only:
                profile = EmployeeDataManager.get_employee_profile(employee)
                if profile and profile.employment_status == "PROBATION":
                    continue

            if leave_type.gender_specific != "A":
                if employee.gender != leave_type.gender_specific:
                    continue

            balance = LeaveService.get_employee_leave_balance(
                employee, leave_type, year
            )
            balances.append(balance)

        return balances

    @staticmethod
    def remove_leave_attendance_records(leave_request):
        current_date = leave_request.start_date

        while current_date <= leave_request.end_date:
            try:
                attendance = Attendance.objects.get(
                    employee=leave_request.employee, date=current_date
                )

                if attendance.status == "LEAVE":
                    attendance.status = "ABSENT"
                    attendance.notes = "Leave cancelled"
                    attendance.save(update_fields=["status", "notes"])

            except Attendance.DoesNotExist:
                pass

            current_date += timedelta(days=1)

    @staticmethod
    def adjust_leave_balance(
        employee, leave_type, year, adjustment_days, adjusted_by, reason
    ):
        balance = LeaveService.get_employee_leave_balance(employee, leave_type, year)

        balance.adjustment_days += Decimal(str(adjustment_days))
        balance.updated_by = adjusted_by
        balance.save(update_fields=["adjustment_days", "updated_by", "last_updated"])

        AuditHelper.log_attendance_change(
            user=adjusted_by,
            action="LEAVE_BALANCE_ADJUSTED",
            employee=employee,
            attendance_date=get_current_date(),
            changes={
                "leave_type": leave_type.name,
                "year": year,
                "adjustment_days": float(adjustment_days),
                "reason": reason,
            },
        )

        return balance


class ReportService:
    @staticmethod
    def generate_daily_attendance_report(target_date, departments=None, user=None):
        if not ReportPermission.can_generate_reports(user):
            raise ValidationError("You don't have permission to generate reports")

        employees = CustomUser.active.all()

        if departments:
            employees = employees.filter(department__in=departments)

        attendance_records = Attendance.objects.filter(
            employee__in=employees, date=target_date
        ).select_related("employee", "employee__department")

        report_data = []
        for record in attendance_records:
            report_data.append(
                {
                    "employee_code": record.employee.employee_code,
                    "employee_name": record.employee.get_full_name(),
                    "department": (
                        record.employee.department.name
                        if record.employee.department
                        else "N/A"
                    ),
                    "date": record.date,
                    "status": record.status,
                    "first_in_time": record.first_in_time,
                    "last_out_time": record.last_out_time,
                    "total_work_time": record.formatted_work_time,
                    "overtime": record.formatted_overtime,
                    "late_minutes": record.late_minutes,
                    "early_departure_minutes": record.early_departure_minutes,
                }
            )

        summary = {
            "report_date": target_date,
            "total_employees": len(report_data),
            "present": len(
                [r for r in report_data if r["status"] in ["PRESENT", "LATE"]]
            ),
            "absent": len([r for r in report_data if r["status"] == "ABSENT"]),
            "late": len([r for r in report_data if r["status"] == "LATE"]),
            "on_leave": len([r for r in report_data if r["status"] == "LEAVE"]),
            "records": report_data,
        }

        return summary

    @staticmethod
    def generate_monthly_attendance_report(employee, year, month, user=None):
        if not EmployeeAttendancePermission.can_view_employee_attendance(
            user, employee
        ):
            raise ValidationError(
                "You don't have permission to view this employee's attendance"
            )

        summary = MonthlyAttendanceSummary.objects.filter(
            employee=employee, year=year, month=month
        ).first()

        if not summary:
            summary = MonthlyAttendanceSummary.generate_for_employee_month(
                employee, year, month
            )

        attendance_records = Attendance.objects.filter(
            employee=employee, date__year=year, date__month=month
        ).order_by("date")

        return {
            "employee": employee,
            "year": year,
            "month": month,
            "summary": summary,
            "daily_records": attendance_records,
        }

    @staticmethod
    def generate_department_monthly_report(department, year, month, user=None):
        if not ReportPermission.can_view_department_reports(user, department):
            raise ValidationError(
                "You don't have permission to view this department's reports"
            )

        department_employees = department.employees.filter(is_active=True)

        summaries = MonthlyAttendanceSummary.objects.filter(
            employee__in=department_employees, year=year, month=month
        ).select_related("employee")

        missing_summaries = department_employees.exclude(
            monthly_summaries__year=year, monthly_summaries__month=month
        )

        for employee in missing_summaries:
            summary = MonthlyAttendanceSummary.generate_for_employee_month(
                employee, year, month
            )
            summaries = summaries.union(
                MonthlyAttendanceSummary.objects.filter(id=summary.id)
            )

        department_stats = {
            "total_employees": department_employees.count(),
            "avg_attendance_percentage": summaries.aggregate(
                avg_attendance=Avg("attendance_percentage")
            )["avg_attendance"]
            or 0,
            "avg_punctuality_score": summaries.aggregate(
                avg_punctuality=Avg("punctuality_score")
            )["avg_punctuality"]
            or 0,
            "total_overtime_hours": sum(
                (s.total_overtime.total_seconds() / 3600 for s in summaries), 0
            ),
        }

        return {
            "department": department,
            "year": year,
            "month": month,
            "employee_summaries": summaries,
            "department_stats": department_stats,
        }

    @staticmethod
    def generate_overtime_report(start_date, end_date, departments=None, user=None):
        if not ReportPermission.can_generate_reports(user):
            raise ValidationError("You don't have permission to generate reports")

        employees = CustomUser.active.all()
        if departments:
            employees = employees.filter(department__in=departments)

        overtime_records = Attendance.objects.filter(
            employee__in=employees,
            date__range=[start_date, end_date],
            overtime__gt=timedelta(0),
        ).select_related("employee", "employee__department")

        employee_overtime = {}
        for record in overtime_records:
            emp_id = record.employee.id
            if emp_id not in employee_overtime:
                employee_overtime[emp_id] = {
                    "employee": record.employee,
                    "total_overtime": timedelta(0),
                    "overtime_days": 0,
                    "records": [],
                }

            employee_overtime[emp_id]["total_overtime"] += record.overtime
            employee_overtime[emp_id]["overtime_days"] += 1
            employee_overtime[emp_id]["records"].append(record)

        sorted_overtime = sorted(
            employee_overtime.values(), key=lambda x: x["total_overtime"], reverse=True
        )

        return {
            "period": f"{start_date} to {end_date}",
            "total_employees_with_overtime": len(sorted_overtime),
            "total_overtime_hours": sum(
                (
                    emp["total_overtime"].total_seconds() / 3600
                    for emp in sorted_overtime
                ),
                0,
            ),
            "employee_overtime_data": sorted_overtime,
        }

    @staticmethod
    def generate_leave_report(start_date, end_date, departments=None, user=None):
        if not ReportPermission.can_generate_reports(user):
            raise ValidationError("You don't have permission to generate reports")

        employees = CustomUser.active.all()
        if departments:
            employees = employees.filter(department__in=departments)

        leave_requests = LeaveRequest.objects.filter(
            employee__in=employees,
            start_date__lte=end_date,
            end_date__gte=start_date,
            status="APPROVED",
        ).select_related("employee", "leave_type", "employee__department")

        leave_summary = {}
        for leave in leave_requests:
            leave_type_name = leave.leave_type.name
            if leave_type_name not in leave_summary:
                leave_summary[leave_type_name] = {
                    "total_requests": 0,
                    "total_days": Decimal("0.00"),
                    "employees": set(),
                }

            leave_summary[leave_type_name]["total_requests"] += 1
            leave_summary[leave_type_name]["total_days"] += leave.total_days
            leave_summary[leave_type_name]["employees"].add(leave.employee.id)

        for leave_type in leave_summary:
            leave_summary[leave_type]["unique_employees"] = len(
                leave_summary[leave_type]["employees"]
            )
            del leave_summary[leave_type]["employees"]

        return {
            "period": f"{start_date} to {end_date}",
            "total_leave_requests": leave_requests.count(),
            "leave_type_summary": leave_summary,
            "leave_requests": leave_requests,
        }


class ShiftService:
    @staticmethod
    def assign_shift_to_employee(
        employee, shift, effective_from, effective_to=None, assigned_by=None
    ):
        if effective_from < get_current_date():
            raise ValidationError("Shift assignment cannot be backdated")

        overlapping_assignments = EmployeeShift.objects.filter(
            employee=employee,
            is_active=True,
            effective_from__lte=effective_to or date(2099, 12, 31),
            effective_to__gte=effective_from,
        )

        if overlapping_assignments.exists():
            raise ValidationError(
                "Employee already has a shift assigned for this period"
            )

        employee_shift = EmployeeShift.objects.create(
            employee=employee,
            shift=shift,
            effective_from=effective_from,
            effective_to=effective_to,
            assigned_by=assigned_by,
            notes=f"Assigned by {assigned_by.get_full_name() if assigned_by else 'System'}",
        )

        return employee_shift

    @staticmethod
    def bulk_assign_shift(
        employees, shift, effective_from, effective_to=None, assigned_by=None
    ):
        assignments = []
        errors = []

        for employee in employees:
            try:
                assignment = ShiftService.assign_shift_to_employee(
                    employee, shift, effective_from, effective_to, assigned_by
                )
                assignments.append(assignment)
            except ValidationError as e:
                errors.append(f"{employee.get_full_name()}: {str(e)}")

        return {
            "successful_assignments": assignments,
            "errors": errors,
            "total_processed": len(employees),
            "successful_count": len(assignments),
        }

    @staticmethod
    def get_employee_current_shift(employee):
        today = get_current_date()

        current_assignment = (
            EmployeeShift.objects.filter(
                employee=employee, effective_from__lte=today, is_active=True
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today))
            .first()
        )

        return current_assignment.shift if current_assignment else None


class ExcelService:
    @staticmethod
    def import_attendance_from_excel(file_content, user):
        if not check_attendance_permission(user, "edit_employee_attendance"):
            raise ValidationError("You don't have permission to import attendance data")

        is_valid, message, df = ValidationHelper.validate_excel_file(file_content)
        if not is_valid:
            raise ValidationError(message)

        imported_records = []
        errors = []

        for index, row in df.iterrows():
            try:
                employee_code = ValidationHelper.sanitize_employee_code(
                    str(row.get("ID", ""))
                )
                if not employee_code:
                    errors.append(f"Row {index + 2}: Missing employee ID")
                    continue

                employee = EmployeeDataManager.get_employee_by_code(employee_code)
                if not employee:
                    errors.append(
                        f"Row {index + 2}: Employee {employee_code} not found"
                    )
                    continue

                attendance_date = safe_date_conversion(row.get("Date"))
                if not attendance_date:
                    errors.append(f"Row {index + 2}: Invalid date format")
                    continue

                time_pairs = []
                for i in range(1, 7):
                    in_time = safe_time_conversion(row.get(f"In{i}"))
                    out_time = safe_time_conversion(row.get(f"Out{i}"))
                    time_pairs.append((in_time, out_time))

                attendance, created = AttendanceService.create_or_get_attendance_record(
                    employee, attendance_date, user
                )

                attendance.set_time_pairs(time_pairs)
                attendance.is_manual_entry = True
                attendance.notes = f"Imported from Excel by {user.get_full_name()}"
                attendance._attendance_changed_by = user
                attendance.save()

                imported_records.append(attendance)

            except Exception as e:
                errors.append(f"Row {index + 2}: {str(e)}")

        return {
            "imported_count": len(imported_records),
            "error_count": len(errors),
            "errors": errors,
            "imported_records": imported_records,
        }

    @staticmethod
    def export_attendance_to_excel(employees, start_date, end_date, user):
        if not ReportPermission.can_export_attendance_data(user):
            raise ValidationError("You don't have permission to export attendance data")

        attendance_data = []

        current_date = start_date
        while current_date <= end_date:
            for employee in employees:
                try:
                    attendance = Attendance.objects.get(
                        employee=employee, date=current_date
                    )

                    time_pairs = attendance.get_time_pairs()

                    data = {
                        "division": (
                            employee.department.name if employee.department else "N/A"
                        ),
                        "employee_id": employee.employee_code,
                        "name": employee.get_full_name(),
                        "date": current_date,
                        "time_pairs": time_pairs,
                        "total_time": attendance.total_time,
                        "break_time": attendance.break_time,
                        "work_time": attendance.work_time,
                        "overtime": attendance.overtime,
                    }

                    attendance_data.append(data)

                except Attendance.DoesNotExist:
                    data = {
                        "division": (
                            employee.department.name if employee.department else "N/A"
                        ),
                        "employee_id": employee.employee_code,
                        "name": employee.get_full_name(),
                        "date": current_date,
                        "time_pairs": [(None, None)] * 6,
                        "total_time": timedelta(0),
                        "break_time": timedelta(0),
                        "work_time": timedelta(0),
                        "overtime": timedelta(0),
                    }

                    attendance_data.append(data)

            current_date += timedelta(days=1)

        excel_buffer = ExcelProcessor.create_attendance_excel(
            attendance_data, start_date.month, start_date.year
        )

        return excel_buffer

    @staticmethod
    def generate_monthly_excel_report(employee, year, month, user):
        if not EmployeeAttendancePermission.can_view_employee_attendance(
            user, employee
        ):
            raise ValidationError(
                "You don't have permission to view this employee's attendance"
            )

        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)

        return ExcelService.export_attendance_to_excel(
            [employee], start_date, end_date, user
        )


class StatisticsService:
    @staticmethod
    def get_dashboard_statistics(user, date_filter=None):
        target_date = date_filter or get_current_date()

        accessible_employees = []
        if user.is_superuser or (user.role and user.role.can_view_all_data):
            accessible_employees = CustomUser.active.all()
        elif user.role and user.role.name == "DEPARTMENT_MANAGER" and user.department:
            accessible_employees = user.department.employees.filter(is_active=True)
        else:
            accessible_employees = [user]

        today_attendance = Attendance.objects.filter(
            employee__in=accessible_employees, date=target_date
        )

        stats = {
            "date": target_date,
            "total_employees": len(accessible_employees),
            "present_today": today_attendance.filter(
                status__in=["PRESENT", "LATE"]
            ).count(),
            "absent_today": today_attendance.filter(status="ABSENT").count(),
            "late_today": today_attendance.filter(status="LATE").count(),
            "on_leave_today": today_attendance.filter(status="LEAVE").count(),
            "half_day_today": today_attendance.filter(status="HALF_DAY").count(),
        }

        if stats["total_employees"] > 0:
            stats["attendance_percentage"] = (
                stats["present_today"] / stats["total_employees"] * 100
            )
        else:
            stats["attendance_percentage"] = 0

        current_month = target_date.replace(day=1)
        monthly_summaries = MonthlyAttendanceSummary.objects.filter(
            employee__in=accessible_employees,
            year=target_date.year,
            month=target_date.month,
        )

        if monthly_summaries.exists():
            stats["monthly_avg_attendance"] = (
                monthly_summaries.aggregate(
                    avg_attendance=Avg("attendance_percentage")
                )["avg_attendance"]
                or 0
            )

            stats["monthly_avg_punctuality"] = (
                monthly_summaries.aggregate(avg_punctuality=Avg("punctuality_score"))[
                    "avg_punctuality"
                ]
                or 0
            )
        else:
            stats["monthly_avg_attendance"] = 0
            stats["monthly_avg_punctuality"] = 0

        return stats

    @staticmethod
    def get_employee_performance_metrics(employee, start_date, end_date, user):
        if not EmployeeAttendancePermission.can_view_employee_attendance(
            user, employee
        ):
            raise ValidationError(
                "You don't have permission to view this employee's metrics"
            )

        attendance_records = Attendance.objects.filter(
            employee=employee, date__range=[start_date, end_date]
        )

        total_days = attendance_records.count()
        if total_days == 0:
            return {
                "employee": employee,
                "period": f"{start_date} to {end_date}",
                "no_data": True,
            }

        present_days = attendance_records.filter(status__in=["PRESENT", "LATE"]).count()
        late_days = attendance_records.filter(status="LATE").count()

        total_work_time = sum(
            (record.work_time for record in attendance_records if record.work_time),
            timedelta(0),
        )

        total_overtime = sum(
            (record.overtime for record in attendance_records if record.overtime),
            timedelta(0),
        )

        avg_daily_hours = total_work_time.total_seconds() / 3600 / max(present_days, 1)

        metrics = {
            "employee": employee,
            "period": f"{start_date} to {end_date}",
            "total_days": total_days,
            "present_days": present_days,
            "attendance_rate": (
                (present_days / total_days * 100) if total_days > 0 else 0
            ),
            "punctuality_rate": (
                ((present_days - late_days) / present_days * 100)
                if present_days > 0
                else 0
            ),
            "avg_daily_hours": round(avg_daily_hours, 2),
            "total_overtime_hours": round(total_overtime.total_seconds() / 3600, 2),
            "consistency_score": StatisticsService.calculate_consistency_score(
                attendance_records
            ),
            "performance_trend": StatisticsService.calculate_performance_trend(
                attendance_records
            ),
        }

        return metrics

    @staticmethod
    def calculate_consistency_score(attendance_records):
        if not attendance_records:
            return 0

        work_times = [
            record.work_time.total_seconds() / 3600
            for record in attendance_records
            if record.work_time and record.status in ["PRESENT", "LATE"]
        ]

        if len(work_times) < 2:
            return 100

        avg_hours = sum(work_times) / len(work_times)
        variance = sum((hours - avg_hours) ** 2 for hours in work_times) / len(
            work_times
        )
        std_deviation = variance**0.5

        consistency_score = max(0, 100 - (std_deviation * 10))
        return round(consistency_score, 2)

    @staticmethod
    def calculate_performance_trend(attendance_records):
        if len(attendance_records) < 7:
            return "insufficient_data"

        records_list = list(attendance_records.order_by("date"))
        first_week = records_list[:7]
        last_week = records_list[-7:]

        first_week_attendance = (
            sum(1 for record in first_week if record.status in ["PRESENT", "LATE"])
            / 7
            * 100
        )

        last_week_attendance = (
            sum(1 for record in last_week if record.status in ["PRESENT", "LATE"])
            / 7
            * 100
        )

        difference = last_week_attendance - first_week_attendance

        if difference > 5:
            return "improving"
        elif difference < -5:
            return "declining"
        else:
            return "stable"

    @staticmethod
    def get_department_comparison(departments, target_date, user):
        if not ReportPermission.can_generate_reports(user):
            raise ValidationError(
                "You don't have permission to view department comparisons"
            )

        comparison_data = []

        for department in departments:
            department_employees = department.employees.filter(is_active=True)

            if department_employees.exists():
                attendance_records = Attendance.objects.filter(
                    employee__in=department_employees, date=target_date
                )

                total_employees = department_employees.count()
                present_count = attendance_records.filter(
                    status__in=["PRESENT", "LATE"]
                ).count()
                late_count = attendance_records.filter(status="LATE").count()

                attendance_rate = (
                    (present_count / total_employees * 100)
                    if total_employees > 0
                    else 0
                )
                punctuality_rate = (
                    ((present_count - late_count) / present_count * 100)
                    if present_count > 0
                    else 0
                )

                comparison_data.append(
                    {
                        "department": department,
                        "total_employees": total_employees,
                        "present_count": present_count,
                        "attendance_rate": round(attendance_rate, 2),
                        "punctuality_rate": round(punctuality_rate, 2),
                    }
                )

        comparison_data.sort(key=lambda x: x["attendance_rate"], reverse=True)

        return {
            "date": target_date,
            "departments": comparison_data,
            "best_performing": comparison_data[0] if comparison_data else None,
            "avg_attendance_rate": (
                sum(d["attendance_rate"] for d in comparison_data)
                / len(comparison_data)
                if comparison_data
                else 0
            ),
        }


class NotificationService:
    @staticmethod
    def send_late_arrival_notification(attendance_record):
        if attendance_record.status == "LATE" and attendance_record.late_minutes > 0:
            message = f"Late arrival recorded: {attendance_record.late_minutes} minutes late on {attendance_record.date}"

            NotificationService.create_notification(
                recipient=attendance_record.employee,
                title="Late Arrival Alert",
                message=message,
                notification_type="ATTENDANCE_ALERT",
            )

            if attendance_record.employee.manager:
                manager_message = f"{attendance_record.employee.get_full_name()} was {attendance_record.late_minutes} minutes late on {attendance_record.date}"
                NotificationService.create_notification(
                    recipient=attendance_record.employee.manager,
                    title="Team Member Late Arrival",
                    message=manager_message,
                    notification_type="MANAGER_ALERT",
                )

    @staticmethod
    def send_leave_request_notification(leave_request):
        if leave_request.employee.manager:
            message = f"New leave request from {leave_request.employee.get_full_name()} for {leave_request.leave_type.name} from {leave_request.start_date} to {leave_request.end_date}"

            NotificationService.create_notification(
                recipient=leave_request.employee.manager,
                title="Leave Request Pending Approval",
                message=message,
                notification_type="LEAVE_REQUEST",
            )

    @staticmethod
    def send_leave_approval_notification(leave_request):
        status_message = (
            "approved" if leave_request.status == "APPROVED" else "rejected"
        )
        message = f"Your leave request for {leave_request.leave_type.name} from {leave_request.start_date} to {leave_request.end_date} has been {status_message}"

        if leave_request.status == "REJECTED" and leave_request.rejection_reason:
            message += f". Reason: {leave_request.rejection_reason}"

        NotificationService.create_notification(
            recipient=leave_request.employee,
            title=f"Leave Request {status_message.title()}",
            message=message,
            notification_type="LEAVE_RESPONSE",
        )

    @staticmethod
    def create_notification(recipient, title, message, notification_type):
        try:
            from accounts.models import Notification

            Notification.objects.create(
                recipient=recipient,
                title=title,
                message=message,
                notification_type=notification_type,
                is_read=False,
            )
        except Exception:
            pass


class HolidayService:
    @staticmethod
    def create_holiday(holiday_data, created_by):
        if not check_attendance_permission(created_by, "manage_holidays"):
            raise ValidationError("You don't have permission to create holidays")

        holiday = Holiday(
            name=holiday_data["name"],
            date=holiday_data["date"],
            holiday_type=holiday_data.get("holiday_type", "NATIONAL"),
            description=holiday_data.get("description", ""),
            is_optional=holiday_data.get("is_optional", False),
            is_paid=holiday_data.get("is_paid", True),
            created_by=created_by,
        )

        holiday.full_clean()
        holiday.save()

        if holiday_data.get("applicable_departments"):
            holiday.applicable_departments.set(holiday_data["applicable_departments"])

        if holiday_data.get("applicable_locations"):
            holiday.applicable_locations = holiday_data["applicable_locations"]
            holiday.save(update_fields=["applicable_locations"])

        HolidayService.update_attendance_for_holiday(holiday)

        return holiday

    @staticmethod
    def update_attendance_for_holiday(holiday):
        affected_employees = CustomUser.active.all()

        if holiday.applicable_departments.exists():
            affected_employees = affected_employees.filter(
                department__in=holiday.applicable_departments.all()
            )

        for employee in affected_employees:
            attendance, created = Attendance.objects.get_or_create(
                employee=employee,
                date=holiday.date,
                defaults={
                    "status": "HOLIDAY",
                    "is_holiday": True,
                    "is_manual_entry": True,
                    "notes": f"Holiday: {holiday.name}",
                },
            )

            if not created and attendance.status == "ABSENT":
                attendance.status = "HOLIDAY"
                attendance.is_holiday = True
                attendance.notes = f"Holiday: {holiday.name}"
                attendance.save(update_fields=["status", "is_holiday", "notes"])

    @staticmethod
    def get_upcoming_holidays(days_ahead=30):
        today = get_current_date()
        future_date = today + timedelta(days=days_ahead)

        return Holiday.active.filter(date__range=[today, future_date]).order_by("date")

    @staticmethod
    def get_holidays_for_year(year):
        return Holiday.active.filter(date__year=year).order_by("date")


class SystemMaintenanceService:
    @staticmethod
    def cleanup_old_data():
        results = {}

        log_retention_days = SystemConfiguration.get_int_setting(
            "LOG_RETENTION_DAYS", 90
        )
        cutoff_date = get_current_datetime() - timedelta(days=log_retention_days)

        old_logs = AttendanceLog.objects.filter(
            created_at__lt=cutoff_date, processing_status="PROCESSED"
        )
        results["deleted_logs"] = old_logs.count()
        old_logs.delete()

        old_corrections = AttendanceCorrection.objects.filter(
            requested_at__lt=cutoff_date, status__in=["APPROVED", "REJECTED"]
        )
        results["deleted_corrections"] = old_corrections.count()
        old_corrections.delete()

        archive_years = SystemConfiguration.get_int_setting("ARCHIVE_YEARS", 3)
        archive_cutoff = get_current_date().year - archive_years

        old_summaries = MonthlyAttendanceSummary.objects.filter(year__lt=archive_cutoff)
        results["archived_summaries"] = old_summaries.count()

        return results

    @staticmethod
    def generate_missing_monthly_summaries():
        current_date = get_current_date()
        last_month = current_date.replace(day=1) - timedelta(days=1)

        active_employees = CustomUser.active.all()
        generated_count = 0

        for employee in active_employees:
            if employee.hire_date and employee.hire_date <= last_month:
                summary, created = MonthlyAttendanceSummary.objects.get_or_create(
                    employee=employee, year=last_month.year, month=last_month.month
                )

                if created:
                    MonthlyAttendanceSummary.generate_for_employee_month(
                        employee, last_month.year, last_month.month
                    )
                    generated_count += 1

        return generated_count

    @staticmethod
    def sync_all_devices():
        active_devices = AttendanceDevice.active.all()
        sync_results = []

        for device in active_devices:
            try:
                result = DeviceService.sync_device_data(device)
                sync_results.append(
                    {
                        "device_id": device.device_id,
                        "device_name": device.device_name,
                        "success": result["success"],
                        "logs_synced": result.get("logs_synced", 0),
                        "error": result.get("error"),
                    }
                )
            except Exception as e:
                sync_results.append(
                    {
                        "device_id": device.device_id,
                        "device_name": device.device_name,
                        "success": False,
                        "logs_synced": 0,
                        "error": str(e),
                    }
                )

        return sync_results

    @staticmethod
    def validate_data_integrity():
        issues = []

        attendance_without_employee = Attendance.objects.filter(employee__isnull=True)
        if attendance_without_employee.exists():
            issues.append(
                f"Found {attendance_without_employee.count()} attendance records without employee"
            )

        logs_without_employee = AttendanceLog.objects.filter(
            employee__isnull=True, processing_status="PROCESSED"
        )
        if logs_without_employee.exists():
            issues.append(
                f"Found {logs_without_employee.count()} processed logs without employee"
            )

        future_attendance = Attendance.objects.filter(date__gt=get_current_date())
        if future_attendance.exists():
            issues.append(
                f"Found {future_attendance.count()} attendance records in the future"
            )

        negative_work_time = Attendance.objects.filter(work_time__lt=timedelta(0))
        if negative_work_time.exists():
            issues.append(
                f"Found {negative_work_time.count()} records with negative work time"
            )

        return issues


class BulkOperationsService:
    @staticmethod
    def bulk_update_attendance_status(
        attendance_ids, new_status, updated_by, notes=None
    ):
        if not check_attendance_permission(updated_by, "edit_employee_attendance"):
            raise ValidationError("You don't have permission to bulk update attendance")

        attendance_records = Attendance.objects.filter(id__in=attendance_ids)
        updated_count = 0

        for attendance in attendance_records:
            if EmployeeAttendancePermission.can_edit_employee_attendance(
                updated_by, attendance.employee
            ):
                old_status = attendance.status
                attendance.status = new_status
                if notes:
                    attendance.notes = notes
                attendance._attendance_changed_by = updated_by
                attendance.save()

                AuditHelper.log_attendance_change(
                    user=updated_by,
                    action="BULK_STATUS_UPDATE",
                    employee=attendance.employee,
                    attendance_date=attendance.date,
                    changes={"old_status": old_status, "new_status": new_status},
                )

                updated_count += 1

        return {
            "total_requested": len(attendance_ids),
            "updated_count": updated_count,
            "skipped_count": len(attendance_ids) - updated_count,
        }

    @staticmethod
    def bulk_create_leave_balances(
        employees, leave_type, year, allocated_days, created_by
    ):
        if not check_attendance_permission(created_by, "manage_leave_types"):
            raise ValidationError("You don't have permission to create leave balances")

        created_balances = []
        errors = []

        for employee in employees:
            try:
                balance, created = LeaveBalance.objects.get_or_create(
                    employee=employee,
                    leave_type=leave_type,
                    year=year,
                    defaults={
                        "allocated_days": Decimal(str(allocated_days)),
                        "used_days": Decimal("0.00"),
                        "carried_forward_days": Decimal("0.00"),
                        "adjustment_days": Decimal("0.00"),
                        "updated_by": created_by,
                    },
                )

                if created:
                    created_balances.append(balance)
                else:
                    errors.append(f"{employee.get_full_name()}: Balance already exists")

            except Exception as e:
                errors.append(f"{employee.get_full_name()}: {str(e)}")

        return {
            "created_count": len(created_balances),
            "error_count": len(errors),
            "errors": errors,
            "created_balances": created_balances,
        }

    @staticmethod
    def bulk_approve_leave_requests(leave_request_ids, approved_by):
        approved_requests = []
        errors = []

        for request_id in leave_request_ids:
            try:
                leave_request = LeaveService.approve_leave_request(
                    request_id, approved_by
                )
                approved_requests.append(leave_request)
            except ValidationError as e:
                errors.append(f"Request {request_id}: {str(e)}")
            except Exception as e:
                errors.append(f"Request {request_id}: Unexpected error - {str(e)}")

        return {
            "approved_count": len(approved_requests),
            "error_count": len(errors),
            "errors": errors,
            "approved_requests": approved_requests,
        }


class IntegrationService:
    @staticmethod
    def export_payroll_data(employees, start_date, end_date, user):
        if not ReportPermission.can_export_attendance_data(user):
            raise ValidationError("You don't have permission to export payroll data")

        payroll_data = []

        for employee in employees:
            attendance_records = Attendance.objects.filter(
                employee=employee, date__range=[start_date, end_date]
            )

            total_work_hours = sum(
                (
                    record.work_time.total_seconds() / 3600
                    for record in attendance_records
                    if record.work_time
                ),
                0,
            )

            total_overtime_hours = sum(
                (
                    record.overtime.total_seconds() / 3600
                    for record in attendance_records
                    if record.overtime
                ),
                0,
            )

            present_days = attendance_records.filter(
                status__in=["PRESENT", "LATE"]
            ).count()
            absent_days = attendance_records.filter(status="ABSENT").count()
            leave_days = attendance_records.filter(status="LEAVE").count()

            payroll_data.append(
                {
                    "employee_code": employee.employee_code,
                    "employee_name": employee.get_full_name(),
                    "department": (
                        employee.department.name if employee.department else "N/A"
                    ),
                    "total_work_hours": round(total_work_hours, 2),
                    "total_overtime_hours": round(total_overtime_hours, 2),
                    "present_days": present_days,
                    "absent_days": absent_days,
                    "leave_days": leave_days,
                    "period": f"{start_date} to {end_date}",
                }
            )

        return payroll_data

    @staticmethod
    def sync_with_hr_system(employee_data):
        sync_results = []

        for emp_data in employee_data:
            try:
                employee = CustomUser.objects.get(
                    employee_code=emp_data["employee_code"]
                )

                if emp_data.get("department_changed"):
                    new_department = Department.objects.get(
                        code=emp_data["new_department_code"]
                    )
                    employee.department = new_department
                    employee.save(update_fields=["department"])

                if emp_data.get("shift_changed"):
                    new_shift = Shift.objects.get(code=emp_data["new_shift_code"])
                    ShiftService.assign_shift_to_employee(
                        employee, new_shift, emp_data["effective_date"]
                    )

                sync_results.append(
                    {
                        "employee_code": emp_data["employee_code"],
                        "success": True,
                        "message": "Synced successfully",
                    }
                )

            except Exception as e:
                sync_results.append(
                    {
                        "employee_code": emp_data.get("employee_code", "Unknown"),
                        "success": False,
                        "message": str(e),
                    }
                )

        return sync_results


class CacheService:
    @staticmethod
    def warm_up_caches():
        current_date = get_current_date()
        current_month = current_date.month
        current_year = current_date.year

        active_employees = CustomUser.active.all()[:100]

        for employee in active_employees:
            schedule = EmployeeDataManager.get_employee_work_schedule(employee)
            CacheManager.cache_employee_schedule(employee.id, schedule)

            try:
                summary = MonthlyAttendanceSummary.objects.get(
                    employee=employee, year=current_year, month=current_month
                )

                summary_data = {
                    "total_work_time": summary.total_work_time,
                    "attendance_percentage": summary.attendance_percentage,
                    "punctuality_score": summary.punctuality_score,
                }

                CacheManager.cache_monthly_summary(
                    employee.id, current_year, current_month, summary_data
                )

            except MonthlyAttendanceSummary.DoesNotExist:
                pass

        return f"Warmed up caches for {len(active_employees)} employees"

    @staticmethod
    def clear_all_attendance_caches():
        from django.core.cache import cache

        cache_patterns = [
            "employee_schedule_*",
            "monthly_summary_*",
            "employee_attendance_*",
            "department_attendance_*",
        ]

        cleared_count = 0
        for pattern in cache_patterns:
            try:
                cache.delete_pattern(pattern)
                cleared_count += 1
            except AttributeError:
                pass

        return f"Cleared {cleared_count} cache patterns"


class ValidationService:
    @staticmethod
    def validate_attendance_import(data):
        errors = []
        valid_records = []

        for row in data:
            try:
                employee = EmployeeDataManager.get_employee_by_code(
                    row["employee_code"]
                )
                if not employee:
                    errors.append(f"Employee {row['employee_code']} not found")
                    continue

                attendance_date = safe_date_conversion(row["date"])
                if not attendance_date:
                    errors.append(f"Invalid date: {row['date']}")
                    continue

                valid_records.append(
                    {"employee": employee, "date": attendance_date, "time_data": row}
                )

            except Exception as e:
                errors.append(f"Row error: {str(e)}")

        return valid_records, errors

    @staticmethod
    def validate_bulk_operation(operation_type, data, user):
        if operation_type == "attendance_update":
            return ValidationService.validate_attendance_bulk_update(data, user)
        elif operation_type == "leave_approval":
            return ValidationService.validate_leave_bulk_approval(data, user)
        else:
            return [], ["Invalid operation type"]

    @staticmethod
    def validate_attendance_bulk_update(data, user):
        valid_items = []
        errors = []

        for item in data:
            try:
                attendance = Attendance.objects.get(id=item["attendance_id"])
                if EmployeeAttendancePermission.can_edit_employee_attendance(
                    user, attendance.employee
                ):
                    valid_items.append(attendance)
                else:
                    errors.append(
                        f"No permission for {attendance.employee.get_full_name()}"
                    )
            except Attendance.DoesNotExist:
                errors.append(f"Attendance {item['attendance_id']} not found")

        return valid_items, errors


class AnalyticsService:
    @staticmethod
    def get_attendance_trends(department=None, months=6):
        end_date = get_current_date()
        start_date = end_date - timedelta(days=months * 30)

        employees = CustomUser.active.all()
        if department:
            employees = employees.filter(department=department)

        monthly_data = []
        current_date = start_date.replace(day=1)

        while current_date <= end_date:
            month_attendance = Attendance.objects.filter(
                employee__in=employees,
                date__year=current_date.year,
                date__month=current_date.month,
            )

            total_records = month_attendance.count()
            present_records = month_attendance.filter(
                status__in=["PRESENT", "LATE"]
            ).count()

            attendance_rate = (
                (present_records / total_records * 100) if total_records > 0 else 0
            )

            monthly_data.append(
                {
                    "month": current_date.strftime("%Y-%m"),
                    "attendance_rate": round(attendance_rate, 2),
                    "total_records": total_records,
                    "present_records": present_records,
                }
            )

            if current_date.month == 12:
                current_date = current_date.replace(year=current_date.year + 1, month=1)
            else:
                current_date = current_date.replace(month=current_date.month + 1)

        return monthly_data


def get_service_health_status():
    status = {
        "database_connection": True,
        "device_connections": [],
        "cache_status": True,
        "last_sync_times": {},
    }

    try:
        CustomUser.objects.first()
    except Exception:
        status["database_connection"] = False

    devices = AttendanceDevice.active.all()
    for device in devices:
        is_connected, message = device.test_connection()
        status["device_connections"].append(
            {
                "device_id": device.device_id,
                "connected": is_connected,
                "message": message,
            }
        )
        status["last_sync_times"][device.device_id] = device.last_sync_time

    return status
