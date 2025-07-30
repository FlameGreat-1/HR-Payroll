from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from accounts.models import CustomUser, Department, SystemConfiguration
from employees.models import EmployeeProfile
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
from .services import (
    AttendanceService,
    DeviceService,
    LeaveService,
    ReportService,
    ExcelService,
    StatisticsService,
    HolidayService,
    SystemMaintenanceService,
    BulkOperationsService,
    NotificationService,
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
from datetime import datetime, date, time, timedelta
from decimal import Decimal
import logging
import json

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def sync_device_data(self, device_id):
    try:
        device = AttendanceDevice.objects.get(id=device_id)
        result = DeviceService.sync_device_data(device)

        if result["success"]:
            logger.info(
                f"Device {device.device_id} synced successfully: {result['logs_synced']} logs"
            )
            return {
                "success": True,
                "device_id": device.device_id,
                "logs_synced": result["logs_synced"],
                "sync_time": str(result["sync_time"]),
            }
        else:
            logger.error(f"Device {device.device_id} sync failed: {result['error']}")
            raise Exception(result["error"])

    except AttendanceDevice.DoesNotExist:
        logger.error(f"Device with ID {device_id} not found")
        return {"success": False, "error": "Device not found"}
    except Exception as exc:
        logger.error(f"Device sync failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=60 * (self.request.retries + 1), exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=3)
def sync_all_devices():
    try:
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

        successful_syncs = len([r for r in sync_results if r["success"]])
        total_logs = sum(r["logs_synced"] for r in sync_results if r["success"])

        logger.info(
            f"Synced {successful_syncs}/{len(active_devices)} devices, {total_logs} total logs"
        )

        return {
            "success": True,
            "total_devices": len(active_devices),
            "successful_syncs": successful_syncs,
            "total_logs_synced": total_logs,
            "results": sync_results,
        }

    except Exception as exc:
        logger.error(f"Bulk device sync failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=3)
def process_pending_attendance_logs():
    try:
        pending_logs = AttendanceLog.objects.filter(
            processing_status="PENDING"
        ).order_by("timestamp")

        if not pending_logs.exists():
            return {
                "success": True,
                "message": "No pending logs to process",
                "processed_count": 0,
            }

        grouped_logs = {}
        for log in pending_logs:
            key = f"{log.employee_code}_{log.timestamp.date()}"
            if key not in grouped_logs:
                grouped_logs[key] = []
            grouped_logs[key].append(log)

        processed_count = 0
        error_count = 0

        for key, logs in grouped_logs.items():
            try:
                DeviceService.process_employee_daily_logs(logs)
                processed_count += len(logs)
            except Exception as e:
                logger.error(f"Failed to process logs for {key}: {str(e)}")
                for log in logs:
                    log.mark_as_error(str(e))
                error_count += len(logs)

        logger.info(f"Processed {processed_count} logs, {error_count} errors")

        return {
            "success": True,
            "processed_count": processed_count,
            "error_count": error_count,
            "total_groups": len(grouped_logs),
        }

    except Exception as exc:
        logger.error(f"Log processing failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=120, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def create_daily_attendance_records(target_date=None):
    try:
        if target_date:
            process_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        else:
            process_date = get_current_date()

        active_employees = CustomUser.active.all()
        created_count = 0

        for employee in active_employees:
            try:
                attendance, created = AttendanceService.create_or_get_attendance_record(
                    employee, process_date
                )
                if created:
                    created_count += 1
            except Exception as e:
                logger.error(
                    f"Failed to create attendance for {employee.employee_code}: {str(e)}"
                )

        logger.info(f"Created {created_count} attendance records for {process_date}")

        return {
            "success": True,
            "date": str(process_date),
            "total_employees": active_employees.count(),
            "created_count": created_count,
        }

    except Exception as exc:
        logger.error(f"Daily attendance creation failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def generate_monthly_summaries(year=None, month=None):
    try:
        if year and month:
            target_year = int(year)
            target_month = int(month)
        else:
            last_month = get_current_date().replace(day=1) - timedelta(days=1)
            target_year = last_month.year
            target_month = last_month.month

        active_employees = CustomUser.active.all()
        generated_count = 0
        updated_count = 0

        for employee in active_employees:
            try:
                if employee.hire_date and employee.hire_date <= date(
                    target_year, target_month, 1
                ):
                    summary = MonthlyAttendanceSummary.generate_for_employee_month(
                        employee, target_year, target_month
                    )

                    if hasattr(summary, "_created") and summary._created:
                        generated_count += 1
                    else:
                        updated_count += 1

            except Exception as e:
                logger.error(
                    f"Failed to generate summary for {employee.employee_code}: {str(e)}"
                )

        logger.info(
            f"Generated {generated_count} new summaries, updated {updated_count} for {target_year}-{target_month:02d}"
        )

        return {
            "success": True,
            "year": target_year,
            "month": target_month,
            "total_employees": active_employees.count(),
            "generated_count": generated_count,
            "updated_count": updated_count,
        }

    except Exception as exc:
        logger.error(f"Monthly summary generation failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=600, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def process_incomplete_attendance():
    try:
        yesterday = get_current_date() - timedelta(days=1)

        incomplete_records = Attendance.objects.filter(
            date=yesterday, status="INCOMPLETE"
        )

        processed_count = 0

        for record in incomplete_records:
            try:
                if record.first_in_time and not record.last_out_time:
                    schedule = EmployeeDataManager.get_employee_work_schedule(
                        record.employee
                    )
                    expected_out_time = (
                        timezone.datetime.combine(yesterday, record.first_in_time)
                        + schedule["standard_work_time"]
                    ).time()

                    record.last_out_time = expected_out_time
                    record.status = "PRESENT"
                    record.notes = "Auto-completed: Missing check-out"
                    record.save()
                    processed_count += 1

            except Exception as e:
                logger.error(
                    f"Failed to process incomplete attendance {record.id}: {str(e)}"
                )

        logger.info(
            f"Processed {processed_count} incomplete attendance records for {yesterday}"
        )

        return {
            "success": True,
            "date": str(yesterday),
            "processed_count": processed_count,
            "total_incomplete": incomplete_records.count(),
        }

    except Exception as exc:
        logger.error(f"Incomplete attendance processing failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def update_leave_balances_for_new_year():
    try:
        current_year = get_current_date().year
        previous_year = current_year - 1

        active_employees = CustomUser.active.all()
        active_leave_types = LeaveType.active.all()

        created_count = 0
        carried_forward_count = 0

        for employee in active_employees:
            for leave_type in active_leave_types:
                try:
                    previous_balance = LeaveBalance.objects.filter(
                        employee=employee, leave_type=leave_type, year=previous_year
                    ).first()

                    carried_forward = Decimal("0.00")
                    if previous_balance and leave_type.carry_forward_allowed:
                        available = previous_balance.available_days
                        max_carry = Decimal(str(leave_type.carry_forward_max_days or 0))
                        carried_forward = min(available, max_carry)
                        if carried_forward > 0:
                            carried_forward_count += 1

                    balance, created = LeaveBalance.objects.get_or_create(
                        employee=employee,
                        leave_type=leave_type,
                        year=current_year,
                        defaults={
                            "allocated_days": Decimal(
                                str(leave_type.days_allowed_per_year)
                            ),
                            "used_days": Decimal("0.00"),
                            "carried_forward_days": carried_forward,
                            "adjustment_days": Decimal("0.00"),
                        },
                    )

                    if created:
                        created_count += 1

                except Exception as e:
                    logger.error(
                        f"Failed to update leave balance for {employee.employee_code}: {str(e)}"
                    )

        logger.info(
            f"Created {created_count} leave balances, {carried_forward_count} with carry forward for {current_year}"
        )

        return {
            "success": True,
            "year": current_year,
            "total_employees": active_employees.count(),
            "total_leave_types": active_leave_types.count(),
            "created_count": created_count,
            "carried_forward_count": carried_forward_count,
        }

    except Exception as exc:
        logger.error(f"Leave balance update failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=600, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def sync_employees_to_devices():
    try:
        active_devices = AttendanceDevice.active.all()
        active_employees = CustomUser.active.all()

        sync_results = []

        for device in active_devices:
            try:
                result = DeviceService.sync_employees_to_device(device)
                sync_results.append(
                    {
                        "device_id": device.device_id,
                        "device_name": device.device_name,
                        "success": result["success"],
                        "employees_synced": result.get("employees_synced", 0),
                        "total_employees": result.get("total_employees", 0),
                        "error": result.get("error"),
                    }
                )
            except Exception as e:
                sync_results.append(
                    {
                        "device_id": device.device_id,
                        "device_name": device.device_name,
                        "success": False,
                        "employees_synced": 0,
                        "total_employees": 0,
                        "error": str(e),
                    }
                )

        successful_syncs = len([r for r in sync_results if r["success"]])
        total_employees_synced = sum(
            r["employees_synced"] for r in sync_results if r["success"]
        )

        logger.info(
            f"Employee sync: {successful_syncs}/{len(active_devices)} devices, {total_employees_synced} employees synced"
        )

        return {
            "success": True,
            "total_devices": len(active_devices),
            "successful_syncs": successful_syncs,
            "total_employees": active_employees.count(),
            "total_employees_synced": total_employees_synced,
            "results": sync_results,
        }

    except Exception as exc:
        logger.error(f"Employee sync to devices failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=600, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def import_attendance_from_excel(self, file_path, user_id, import_options=None):
    try:
        user = CustomUser.objects.get(id=user_id)
        import_options = import_options or {}

        with open(file_path, "rb") as file:
            file_content = file.read()

        result = ExcelService.import_attendance_from_excel(file_content, user)

        if result["imported_count"] > 0:
            logger.info(
                f"Excel import successful: {result['imported_count']} records imported by {user.get_full_name()}"
            )

            NotificationService.create_notification(
                recipient=user,
                title="Excel Import Completed",
                message=f"Successfully imported {result['imported_count']} attendance records",
                notification_type="SYSTEM_NOTIFICATION",
            )

        if result["error_count"] > 0:
            logger.warning(f"Excel import had {result['error_count']} errors")

            NotificationService.create_notification(
                recipient=user,
                title="Excel Import Completed with Errors",
                message=f"Imported {result['imported_count']} records with {result['error_count']} errors",
                notification_type="SYSTEM_WARNING",
            )

        return {
            "success": True,
            "imported_count": result["imported_count"],
            "error_count": result["error_count"],
            "errors": result["errors"][:10],
        }

    except CustomUser.DoesNotExist:
        logger.error(f"User with ID {user_id} not found for Excel import")
        return {"success": False, "error": "User not found"}
    except Exception as exc:
        logger.error(f"Excel import failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def generate_attendance_report(self, report_id):
    try:
        report = AttendanceReport.objects.get(id=report_id)

        if report.report_type == "DAILY":
            report_data = ReportService.generate_daily_attendance_report(
                report.start_date,
                report.departments.all() if report.departments.exists() else None,
                report.generated_by,
            )
        elif report.report_type == "MONTHLY":
            if report.employees.exists():
                employee = report.employees.first()
                report_data = ReportService.generate_monthly_attendance_report(
                    employee,
                    report.start_date.year,
                    report.start_date.month,
                    report.generated_by,
                )
            else:
                raise ValueError("Employee required for monthly report")
        elif report.report_type == "DEPARTMENT":
            if report.departments.exists():
                department = report.departments.first()
                report_data = ReportService.generate_department_monthly_report(
                    department,
                    report.start_date.year,
                    report.start_date.month,
                    report.generated_by,
                )
            else:
                raise ValueError("Department required for department report")
        elif report.report_type == "OVERTIME":
            report_data = ReportService.generate_overtime_report(
                report.start_date,
                report.end_date,
                report.departments.all() if report.departments.exists() else None,
                report.generated_by,
            )
        elif report.report_type == "LEAVE":
            report_data = ReportService.generate_leave_report(
                report.start_date,
                report.end_date,
                report.departments.all() if report.departments.exists() else None,
                report.generated_by,
            )
        else:
            raise ValueError(f"Unsupported report type: {report.report_type}")

        report.report_data = report_data
        report.mark_completed()

        logger.info(f"Report {report.name} generated successfully")

        NotificationService.create_notification(
            recipient=report.generated_by,
            title="Report Generated",
            message=f"Your report '{report.name}' has been generated successfully",
            notification_type="REPORT_READY",
        )

        return {
            "success": True,
            "report_id": str(report.id),
            "report_name": report.name,
            "report_type": report.report_type,
        }

    except AttendanceReport.DoesNotExist:
        logger.error(f"Report with ID {report_id} not found")
        return {"success": False, "error": "Report not found"}
    except Exception as exc:
        logger.error(f"Report generation failed: {str(exc)}")
        try:
            report = AttendanceReport.objects.get(id=report_id)
            report.mark_failed()
        except:
            pass

        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def send_late_arrival_notifications():
    try:
        today = get_current_date()

        late_arrivals = Attendance.objects.filter(
            date=today, status="LATE", late_minutes__gt=0
        ).select_related("employee", "employee__manager")

        notifications_sent = 0

        for attendance in late_arrivals:
            try:
                NotificationService.send_late_arrival_notification(attendance)
                notifications_sent += 1
            except Exception as e:
                logger.error(
                    f"Failed to send late arrival notification for {attendance.employee.employee_code}: {str(e)}"
                )

        logger.info(f"Sent {notifications_sent} late arrival notifications for {today}")

        return {
            "success": True,
            "date": str(today),
            "late_arrivals": late_arrivals.count(),
            "notifications_sent": notifications_sent,
        }

    except Exception as exc:
        logger.error(f"Late arrival notifications failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def send_leave_request_notifications():
    try:
        pending_requests = LeaveRequest.objects.filter(
            status="PENDING",
            applied_at__gte=get_current_datetime() - timedelta(hours=24),
        ).select_related("employee", "employee__manager", "leave_type")

        notifications_sent = 0

        for leave_request in pending_requests:
            try:
                NotificationService.send_leave_request_notification(leave_request)
                notifications_sent += 1
            except Exception as e:
                logger.error(
                    f"Failed to send leave request notification for {leave_request.id}: {str(e)}"
                )

        logger.info(f"Sent {notifications_sent} leave request notifications")

        return {
            "success": True,
            "pending_requests": pending_requests.count(),
            "notifications_sent": notifications_sent,
        }

    except Exception as exc:
        logger.error(f"Leave request notifications failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def cleanup_old_data():
    try:
        cleanup_results = SystemMaintenanceService.cleanup_old_data()

        logger.info(f"Data cleanup completed: {cleanup_results}")

        return {"success": True, "cleanup_results": cleanup_results}

    except Exception as exc:
        logger.error(f"Data cleanup failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=600, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def validate_data_integrity():
    try:
        issues = SystemMaintenanceService.validate_data_integrity()

        if issues:
            logger.warning(f"Data integrity issues found: {len(issues)} issues")

            admin_users = CustomUser.objects.filter(is_superuser=True, is_active=True)

            for admin in admin_users:
                NotificationService.create_notification(
                    recipient=admin,
                    title="Data Integrity Issues Found",
                    message=f"Found {len(issues)} data integrity issues that need attention",
                    notification_type="SYSTEM_ALERT",
                )
        else:
            logger.info("Data integrity validation passed - no issues found")

        return {"success": True, "issues_found": len(issues), "issues": issues}

    except Exception as exc:
        logger.error(f"Data integrity validation failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=600, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def warm_up_caches():
    try:
        from .services import CacheService

        result = CacheService.warm_up_caches()

        logger.info(f"Cache warm-up completed: {result}")

        return {"success": True, "message": result}

    except Exception as exc:
        logger.error(f"Cache warm-up failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def process_attendance_corrections():
    try:
        pending_corrections = AttendanceCorrection.objects.filter(
            status="PENDING",
            requested_at__lte=get_current_datetime() - timedelta(hours=24),
        ).select_related("attendance", "attendance__employee", "requested_by")

        auto_approved_count = 0
        notification_sent_count = 0

        for correction in pending_corrections:
            try:
                if correction.correction_type == "DEVICE_ERROR":
                    if correction.attendance.employee.manager:
                        NotificationService.create_notification(
                            recipient=correction.attendance.employee.manager,
                            title="Attendance Correction Pending",
                            message=f"Device error correction pending for {correction.attendance.employee.get_full_name()}",
                            notification_type="CORRECTION_PENDING",
                        )
                        notification_sent_count += 1

            except Exception as e:
                logger.error(f"Failed to process correction {correction.id}: {str(e)}")

        logger.info(
            f"Processed {pending_corrections.count()} corrections, sent {notification_sent_count} notifications"
        )

        return {
            "success": True,
            "total_corrections": pending_corrections.count(),
            "auto_approved": auto_approved_count,
            "notifications_sent": notification_sent_count,
        }

    except Exception as exc:
        logger.error(f"Attendance corrections processing failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def update_holiday_attendance():
    try:
        today = get_current_date()

        today_holidays = Holiday.active.filter(date=today)

        if not today_holidays.exists():
            return {"success": True, "message": "No holidays today", "updated_count": 0}

        updated_count = 0

        for holiday in today_holidays:
            try:
                HolidayService.update_attendance_for_holiday(holiday)
                updated_count += 1
            except Exception as e:
                logger.error(
                    f"Failed to update attendance for holiday {holiday.name}: {str(e)}"
                )

        logger.info(f"Updated attendance for {updated_count} holidays on {today}")

        return {
            "success": True,
            "date": str(today),
            "holidays_processed": today_holidays.count(),
            "updated_count": updated_count,
        }

    except Exception as exc:
        logger.error(f"Holiday attendance update failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def generate_dashboard_statistics():
    try:
        today = get_current_date()

        departments = Department.active.all()
        department_stats = []

        for department in departments:
            try:
                stats = StatisticsService.get_department_comparison(
                    [department], today, None
                )
                if stats["departments"]:
                    department_stats.append(stats["departments"][0])
            except Exception as e:
                logger.error(
                    f"Failed to generate stats for department {department.name}: {str(e)}"
                )

        overall_stats = {
            "date": str(today),
            "total_departments": len(departments),
            "department_stats": department_stats,
            "generated_at": str(get_current_datetime()),
        }

        CacheManager.cache_dashboard_stats(overall_stats)

        logger.info(
            f"Generated dashboard statistics for {len(department_stats)} departments"
        )

        return {
            "success": True,
            "date": str(today),
            "departments_processed": len(department_stats),
            "stats": overall_stats,
        }

    except Exception as exc:
        logger.error(f"Dashboard statistics generation failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=3)
def backup_attendance_data(self, backup_type="daily"):
    try:
        from django.core.management import call_command
        import os
        from django.conf import settings

        backup_date = get_current_date().strftime("%Y%m%d")
        backup_filename = f"attendance_backup_{backup_type}_{backup_date}.json"
        backup_path = os.path.join(settings.MEDIA_ROOT, "backups", backup_filename)

        os.makedirs(os.path.dirname(backup_path), exist_ok=True)

        if backup_type == "daily":
            yesterday = get_current_date() - timedelta(days=1)
            attendance_data = Attendance.objects.filter(date=yesterday)
        elif backup_type == "weekly":
            week_ago = get_current_date() - timedelta(days=7)
            attendance_data = Attendance.objects.filter(date__gte=week_ago)
        else:
            month_ago = get_current_date() - timedelta(days=30)
            attendance_data = Attendance.objects.filter(date__gte=month_ago)

        backup_count = attendance_data.count()

        with open(backup_path, "w") as backup_file:
            from django.core import serializers

            serializers.serialize("json", attendance_data, stream=backup_file)

        logger.info(
            f"Backup completed: {backup_count} records backed up to {backup_filename}"
        )

        return {
            "success": True,
            "backup_type": backup_type,
            "backup_filename": backup_filename,
            "records_backed_up": backup_count,
            "backup_path": backup_path,
        }

    except Exception as exc:
        logger.error(f"Backup failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=600, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def bulk_update_attendance_status(
    self, attendance_ids, new_status, user_id, notes=None
):
    try:
        user = CustomUser.objects.get(id=user_id)

        result = BulkOperationsService.bulk_update_attendance_status(
            attendance_ids, new_status, user, notes
        )

        logger.info(
            f"Bulk status update: {result['updated_count']}/{result['total_requested']} records updated by {user.get_full_name()}"
        )

        NotificationService.create_notification(
            recipient=user,
            title="Bulk Update Completed",
            message=f"Updated {result['updated_count']} attendance records to {new_status}",
            notification_type="BULK_OPERATION",
        )

        return {
            "success": True,
            "total_requested": result["total_requested"],
            "updated_count": result["updated_count"],
            "skipped_count": result["skipped_count"],
        }

    except CustomUser.DoesNotExist:
        logger.error(f"User with ID {user_id} not found for bulk update")
        return {"success": False, "error": "User not found"}
    except Exception as exc:
        logger.error(f"Bulk update failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def process_leave_request_approvals(
    self, leave_request_ids, approved_by_id, action="approve"
):
    try:
        approved_by = CustomUser.objects.get(id=approved_by_id)

        if action == "approve":
            result = BulkOperationsService.bulk_approve_leave_requests(
                leave_request_ids, approved_by
            )
        else:
            result = {
                "approved_count": 0,
                "error_count": len(leave_request_ids),
                "errors": ["Bulk rejection not implemented"],
            }

        logger.info(
            f"Bulk leave {action}: {result['approved_count']} requests processed by {approved_by.get_full_name()}"
        )

        NotificationService.create_notification(
            recipient=approved_by,
            title=f"Bulk Leave {action.title()} Completed",
            message=f"Processed {result['approved_count']} leave requests",
            notification_type="BULK_OPERATION",
        )

        return {
            "success": True,
            "action": action,
            "processed_count": result["approved_count"],
            "error_count": result["error_count"],
            "errors": result["errors"][:5],
        }

    except CustomUser.DoesNotExist:
        logger.error(f"User with ID {approved_by_id} not found for leave approval")
        return {"success": False, "error": "User not found"}
    except Exception as exc:
        logger.error(f"Bulk leave approval failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def calculate_overtime_for_period(self, start_date, end_date, department_ids=None):
    try:
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()

        employees = CustomUser.active.all()
        if department_ids:
            departments = Department.objects.filter(id__in=department_ids)
            employees = employees.filter(department__in=departments)

        overtime_data = []
        total_overtime_hours = 0

        for employee in employees:
            attendance_records = Attendance.objects.filter(
                employee=employee,
                date__range=[start_date, end_date],
                overtime__gt=timedelta(0),
            )

            employee_overtime = sum(
                (
                    record.overtime.total_seconds() / 3600
                    for record in attendance_records
                ),
                0,
            )

            if employee_overtime > 0:
                overtime_data.append(
                    {
                        "employee_id": employee.id,
                        "employee_code": employee.employee_code,
                        "employee_name": employee.get_full_name(),
                        "department": (
                            employee.department.name if employee.department else "N/A"
                        ),
                        "total_overtime_hours": round(employee_overtime, 2),
                        "overtime_days": attendance_records.count(),
                    }
                )
                total_overtime_hours += employee_overtime

        overtime_data.sort(key=lambda x: x["total_overtime_hours"], reverse=True)

        logger.info(
            f"Calculated overtime for {len(overtime_data)} employees: {total_overtime_hours:.2f} total hours"
        )

        return {
            "success": True,
            "period": f"{start_date} to {end_date}",
            "employees_with_overtime": len(overtime_data),
            "total_overtime_hours": round(total_overtime_hours, 2),
            "overtime_data": overtime_data[:100],
        }

    except Exception as exc:
        logger.error(f"Overtime calculation failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def generate_payroll_export(self, employee_ids, start_date, end_date, user_id):
    try:
        user = CustomUser.objects.get(id=user_id)
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()

        employees = CustomUser.objects.filter(id__in=employee_ids)

        from .services import IntegrationService

        payroll_data = IntegrationService.export_payroll_data(
            employees, start_date, end_date, user
        )

        export_filename = f"payroll_export_{start_date}_{end_date}_{get_current_datetime().strftime('%Y%m%d_%H%M%S')}.json"
        export_path = f"/tmp/{export_filename}"

        with open(export_path, "w") as export_file:
            json.dump(payroll_data, export_file, indent=2, default=str)

        logger.info(f"Payroll export generated: {len(payroll_data)} employee records")

        NotificationService.create_notification(
            recipient=user,
            title="Payroll Export Ready",
            message=f"Payroll data for {len(payroll_data)} employees is ready for download",
            notification_type="EXPORT_READY",
        )

        return {
            "success": True,
            "employee_count": len(payroll_data),
            "period": f"{start_date} to {end_date}",
            "export_filename": export_filename,
            "export_path": export_path,
        }

    except CustomUser.DoesNotExist:
        logger.error(f"User with ID {user_id} not found for payroll export")
        return {"success": False, "error": "User not found"}
    except Exception as exc:
        logger.error(f"Payroll export failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def sync_hr_system_data(self, employee_data_list):
    try:
        from .services import IntegrationService

        sync_results = IntegrationService.sync_with_hr_system(employee_data_list)

        successful_syncs = len([r for r in sync_results if r["success"]])
        failed_syncs = len([r for r in sync_results if not r["success"]])

        logger.info(
            f"HR system sync: {successful_syncs} successful, {failed_syncs} failed"
        )

        return {
            "success": True,
            "total_employees": len(employee_data_list),
            "successful_syncs": successful_syncs,
            "failed_syncs": failed_syncs,
            "sync_results": sync_results,
        }

    except Exception as exc:
        logger.error(f"HR system sync failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def process_device_maintenance(self, device_id, maintenance_type="health_check"):
    try:
        device = AttendanceDevice.objects.get(id=device_id)

        if maintenance_type == "health_check":
            is_connected, message = device.test_connection()

            if is_connected:
                device.status = "ACTIVE"
                device.last_sync_time = get_current_datetime()
                result_message = "Device health check passed"
            else:
                device.status = "ERROR"
                result_message = f"Device health check failed: {message}"

            device.save(update_fields=["status", "last_sync_time"])

        elif maintenance_type == "restart":
            try:
                sock = DeviceManager.connect_to_realand_device(
                    device.ip_address, device.port
                )
                if sock:
                    DeviceManager.restart_device(sock)
                    sock.close()
                    result_message = "Device restart command sent"
                else:
                    result_message = "Failed to connect for restart"
            except Exception as e:
                result_message = f"Restart failed: {str(e)}"

        elif maintenance_type == "clear_logs":
            try:
                old_logs = AttendanceLog.objects.filter(
                    device=device,
                    processing_status="PROCESSED",
                    created_at__lt=get_current_datetime() - timedelta(days=30),
                )
                deleted_count = old_logs.count()
                old_logs.delete()
                result_message = f"Cleared {deleted_count} old logs"
            except Exception as e:
                result_message = f"Log clearing failed: {str(e)}"

        else:
            result_message = f"Unknown maintenance type: {maintenance_type}"

        logger.info(
            f"Device maintenance {maintenance_type} for {device.device_id}: {result_message}"
        )

        return {
            "success": True,
            "device_id": device.device_id,
            "maintenance_type": maintenance_type,
            "result": result_message,
            "device_status": device.status,
        }

    except AttendanceDevice.DoesNotExist:
        logger.error(f"Device with ID {device_id} not found for maintenance")
        return {"success": False, "error": "Device not found"}
    except Exception as exc:
        logger.error(f"Device maintenance failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def generate_employee_performance_report(
    self, employee_id, start_date, end_date, user_id
):
    try:
        employee = CustomUser.objects.get(id=employee_id)
        user = CustomUser.objects.get(id=user_id)
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()

        performance_metrics = StatisticsService.get_employee_performance_metrics(
            employee, start_date, end_date, user
        )

        report_data = {
            "employee": {
                "id": employee.id,
                "code": employee.employee_code,
                "name": employee.get_full_name(),
                "department": (
                    employee.department.name if employee.department else "N/A"
                ),
            },
            "period": f"{start_date} to {end_date}",
            "metrics": performance_metrics,
            "generated_at": str(get_current_datetime()),
            "generated_by": user.get_full_name(),
        }

        logger.info(f"Performance report generated for {employee.get_full_name()}")

        NotificationService.create_notification(
            recipient=user,
            title="Performance Report Ready",
            message=f"Performance report for {employee.get_full_name()} is ready",
            notification_type="REPORT_READY",
        )

        return {
            "success": True,
            "employee_name": employee.get_full_name(),
            "period": f"{start_date} to {end_date}",
            "report_data": report_data,
        }

    except CustomUser.DoesNotExist:
        logger.error("Employee or user not found for performance report")
        return {"success": False, "error": "Employee or user not found"}
    except Exception as exc:
        logger.error(f"Performance report generation failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=2)
def archive_old_attendance_records(self, cutoff_years=3):
    try:
        cutoff_date = get_current_date().replace(
            year=get_current_date().year - cutoff_years
        )

        old_attendance = Attendance.objects.filter(date__lt=cutoff_date)
        old_logs = AttendanceLog.objects.filter(timestamp__date__lt=cutoff_date)
        old_summaries = MonthlyAttendanceSummary.objects.filter(
            year__lt=cutoff_date.year
        )

        attendance_count = old_attendance.count()
        logs_count = old_logs.count()
        summaries_count = old_summaries.count()

        archive_data = {
            "cutoff_date": str(cutoff_date),
            "attendance_records": attendance_count,
            "log_records": logs_count,
            "summary_records": summaries_count,
            "archived_at": str(get_current_datetime()),
        }

        archive_filename = f"attendance_archive_{cutoff_date.year}_{get_current_datetime().strftime('%Y%m%d')}.json"
        archive_path = f"/tmp/{archive_filename}"

        with open(archive_path, "w") as archive_file:
            json.dump(archive_data, archive_file, indent=2, default=str)

        if attendance_count > 0:
            old_attendance.delete()
        if logs_count > 0:
            old_logs.delete()
        if summaries_count > 0:
            old_summaries.delete()

        logger.info(
            f"Archived {attendance_count + logs_count + summaries_count} old records"
        )

        return {
            "success": True,
            "cutoff_date": str(cutoff_date),
            "archived_records": {
                "attendance": attendance_count,
                "logs": logs_count,
                "summaries": summaries_count,
            },
            "archive_filename": archive_filename,
        }

    except Exception as exc:
        logger.error(f"Archiving failed: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=600, exc=exc)
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=1)
def schedule_periodic_tasks():
    try:
        from celery import current_app

        scheduled_tasks = [
            {
                "name": "sync_all_devices",
                "schedule": "every 30 minutes",
                "task": "attendance.tasks.sync_all_devices",
            },
            {
                "name": "process_pending_logs",
                "schedule": "every 15 minutes",
                "task": "attendance.tasks.process_pending_attendance_logs",
            },
            {
                "name": "create_daily_records",
                "schedule": "daily at 00:30",
                "task": "attendance.tasks.create_daily_attendance_records",
            },
            {
                "name": "generate_monthly_summaries",
                "schedule": "monthly on 1st at 02:00",
                "task": "attendance.tasks.generate_monthly_summaries",
            },
            {
                "name": "cleanup_old_data",
                "schedule": "weekly on sunday at 03:00",
                "task": "attendance.tasks.cleanup_old_data",
            },
        ]

        for task_config in scheduled_tasks:
            current_app.conf.beat_schedule[task_config["name"]] = {
                "task": task_config["task"],
                "schedule": task_config["schedule"],
            }

        return {
            "success": True,
            "scheduled_tasks": len(scheduled_tasks),
            "tasks": [t["name"] for t in scheduled_tasks],
        }

    except Exception as exc:
        logger.error(f"Task scheduling failed: {str(exc)}")
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=1)
def emergency_device_reset(self, device_id):
    try:
        device = AttendanceDevice.objects.get(id=device_id)

        device.status = "MAINTENANCE"
        device.save(update_fields=["status"])

        pending_logs = AttendanceLog.objects.filter(
            device=device, processing_status="PENDING"
        )

        for log in pending_logs:
            log.processing_status = "ERROR"
            log.error_message = "Device emergency reset"
            log.save(update_fields=["processing_status", "error_message"])

        try:
            sock = DeviceManager.connect_to_realand_device(
                device.ip_address, device.port
            )
            if sock:
                DeviceManager.clear_device_data(sock)
                DeviceManager.restart_device(sock)
                sock.close()

                device.status = "ACTIVE"
                device.last_sync_time = get_current_datetime()
                device.save(update_fields=["status", "last_sync_time"])

                result_message = "Emergency reset completed successfully"
            else:
                result_message = "Reset command sent but connection failed"

        except Exception as e:
            result_message = f"Emergency reset failed: {str(e)}"
            device.status = "ERROR"
            device.save(update_fields=["status"])

        logger.warning(
            f"Emergency reset for device {device.device_id}: {result_message}"
        )

        admin_users = CustomUser.objects.filter(is_superuser=True, is_active=True)
        for admin in admin_users:
            NotificationService.create_notification(
                recipient=admin,
                title="Emergency Device Reset",
                message=f"Device {device.device_name} emergency reset: {result_message}",
                notification_type="EMERGENCY_ALERT",
            )

        return {
            "success": True,
            "device_id": device.device_id,
            "result": result_message,
            "pending_logs_affected": pending_logs.count(),
        }

    except AttendanceDevice.DoesNotExist:
        return {"success": False, "error": "Device not found"}
    except Exception as exc:
        logger.error(f"Emergency device reset failed: {str(exc)}")
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=1)
def system_health_monitor():
    try:
        health_status = {
            "timestamp": str(get_current_datetime()),
            "database_status": "healthy",
            "device_status": {},
            "task_queue_status": "healthy",
            "memory_usage": "normal",
            "alerts": [],
        }

        try:
            CustomUser.objects.first()
        except Exception:
            health_status["database_status"] = "error"
            health_status["alerts"].append("Database connection failed")

        devices = AttendanceDevice.active.all()
        for device in devices:
            try:
                is_connected, message = device.test_connection()
                health_status["device_status"][device.device_id] = {
                    "connected": is_connected,
                    "status": device.status,
                    "last_sync": (
                        str(device.last_sync_time) if device.last_sync_time else None
                    ),
                }

                if not is_connected:
                    health_status["alerts"].append(
                        f"Device {device.device_name} disconnected"
                    )

            except Exception as e:
                health_status["device_status"][device.device_id] = {
                    "connected": False,
                    "error": str(e),
                }

        try:
            from celery import current_app

            inspect = current_app.control.inspect()
            active_tasks = inspect.active()
            if not active_tasks:
                health_status["task_queue_status"] = "warning"
        except Exception:
            health_status["task_queue_status"] = "error"
            health_status["alerts"].append("Task queue monitoring failed")

        if health_status["alerts"]:
            admin_users = CustomUser.objects.filter(is_superuser=True, is_active=True)
            for admin in admin_users:
                NotificationService.create_notification(
                    recipient=admin,
                    title="System Health Alert",
                    message=f"System health issues detected: {len(health_status['alerts'])} alerts",
                    notification_type="SYSTEM_ALERT",
                )

        CacheManager.cache_system_health(health_status)

        return health_status

    except Exception as exc:
        logger.error(f"System health monitoring failed: {str(exc)}")
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=1)
def realtime_attendance_processor():
    try:
        current_time = get_current_datetime()
        five_minutes_ago = current_time - timedelta(minutes=5)

        recent_logs = AttendanceLog.objects.filter(
            timestamp__gte=five_minutes_ago, processing_status="PENDING"
        ).order_by("timestamp")

        if not recent_logs.exists():
            return {
                "success": True,
                "message": "No recent logs to process",
                "processed_count": 0,
            }

        processed_count = 0

        for log in recent_logs:
            try:
                if not log.employee:
                    employee = EmployeeDataManager.get_employee_by_code(
                        log.employee_code
                    )
                    if employee:
                        log.employee = employee
                        log.save(update_fields=["employee"])

                if log.employee:
                    attendance_date = log.timestamp.date()
                    attendance, created = (
                        AttendanceService.create_or_get_attendance_record(
                            log.employee, attendance_date
                        )
                    )

                    attendance.process_realtime_log(log)
                    log.mark_as_processed()
                    processed_count += 1

                    if log.log_type == "CHECK_IN" and attendance.status == "LATE":
                        NotificationService.send_late_arrival_notification(attendance)

            except Exception as e:
                log.mark_as_error(str(e))

        return {
            "success": True,
            "recent_logs": recent_logs.count(),
            "processed_count": processed_count,
            "processing_window": "5 minutes",
        }

    except Exception as exc:
        logger.error(f"Realtime processing failed: {str(exc)}")
        return {"success": False, "error": str(exc)}


@shared_task(bind=True, max_retries=1)
def attendance_anomaly_detector():
    try:
        today = get_current_date()
        yesterday = today - timedelta(days=1)

        anomalies = []

        duplicate_entries = (
            Attendance.objects.filter(date=yesterday)
            .values("employee", "date")
            .annotate(count=Count("id"))
            .filter(count__gt=1)
        )

        for dup in duplicate_entries:
            anomalies.append(
                {
                    "type": "duplicate_attendance",
                    "employee_id": dup["employee"],
                    "date": str(dup["date"]),
                    "count": dup["count"],
                }
            )

        impossible_times = Attendance.objects.filter(
            date=yesterday, first_in_time__isnull=False, last_out_time__isnull=False
        ).extra(
            where=["TIME_TO_SEC(last_out_time) - TIME_TO_SEC(first_in_time) > 86400"]
        )

        for record in impossible_times:
            anomalies.append(
                {
                    "type": "impossible_work_hours",
                    "employee_id": record.employee.id,
                    "date": str(record.date),
                    "first_in": str(record.first_in_time),
                    "last_out": str(record.last_out_time),
                }
            )

        missing_checkout = Attendance.objects.filter(
            date=yesterday,
            first_in_time__isnull=False,
            last_out_time__isnull=True,
            status="PRESENT",
        )

        for record in missing_checkout:
            anomalies.append(
                {
                    "type": "missing_checkout",
                    "employee_id": record.employee.id,
                    "date": str(record.date),
                    "first_in": str(record.first_in_time),
                }
            )

        if anomalies:
            admin_users = CustomUser.objects.filter(
                role__name__in=["HR_ADMIN", "HR_MANAGER"], is_active=True
            )

            for admin in admin_users:
                NotificationService.create_notification(
                    recipient=admin,
                    title="Attendance Anomalies Detected",
                    message=f"Found {len(anomalies)} attendance anomalies for {yesterday}",
                    notification_type="DATA_ANOMALY",
                )

        logger.info(
            f"Anomaly detection: {len(anomalies)} anomalies found for {yesterday}"
        )

        return {
            "success": True,
            "date": str(yesterday),
            "anomalies_found": len(anomalies),
            "anomalies": anomalies[:20],
        }

    except Exception as exc:
        logger.error(f"Anomaly detection failed: {str(exc)}")
        return {"success": False, "error": str(exc)}


def get_task_status(task_id):
    try:
        from celery.result import AsyncResult

        result = AsyncResult(task_id)

        return {
            "task_id": task_id,
            "status": result.status,
            "result": result.result if result.ready() else None,
            "traceback": result.traceback if result.failed() else None,
        }
    except Exception as e:
        return {"task_id": task_id, "status": "ERROR", "error": str(e)}


def cancel_task(task_id):
    try:
        from celery import current_app

        current_app.control.revoke(task_id, terminate=True)
        return {"success": True, "message": f"Task {task_id} cancelled"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_active_tasks():
    try:
        from celery import current_app

        inspect = current_app.control.inspect()

        active = inspect.active()
        scheduled = inspect.scheduled()
        reserved = inspect.reserved()

        return {
            "active": active or {},
            "scheduled": scheduled or {},
            "reserved": reserved or {},
        }
    except Exception as e:
        return {"error": str(e)}


def purge_task_queue():
    try:
        from celery import current_app

        current_app.control.purge()
        return {"success": True, "message": "Task queue purged"}
    except Exception as e:
        return {"success": False, "error": str(e)}
