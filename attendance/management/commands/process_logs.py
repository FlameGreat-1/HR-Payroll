from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.db.models import Q, Count
from accounts.models import CustomUser, SystemConfiguration
from attendance.models import AttendanceLog, Attendance, AttendanceDevice
from attendance.services import DeviceService, AttendanceService
from attendance.tasks import (
    process_pending_attendance_logs,
    process_incomplete_attendance,
)
from attendance.utils import (
    EmployeeDataManager,
    ValidationHelper,
    AuditHelper,
    get_current_date,
    get_current_datetime,
)
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process attendance logs from REALAND A-F011 devices"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", type=str, help="Process logs for specific date (YYYY-MM-DD)"
        )

        parser.add_argument(
            "--date-range",
            type=str,
            help="Process logs for date range (YYYY-MM-DD:YYYY-MM-DD)",
        )

        parser.add_argument(
            "--device-id", type=str, help="Process logs from specific device only"
        )

        parser.add_argument(
            "--employee-code", type=str, help="Process logs for specific employee only"
        )

        parser.add_argument(
            "--status",
            type=str,
            choices=["PENDING", "PROCESSED", "ERROR"],
            help="Process logs with specific status",
        )

        parser.add_argument(
            "--reprocess-errors",
            action="store_true",
            help="Reprocess logs that previously failed",
        )

        parser.add_argument(
            "--fix-incomplete",
            action="store_true",
            help="Fix incomplete attendance records",
        )

        parser.add_argument(
            "--async", action="store_true", help="Run processing as background task"
        )

        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Number of logs to process in each batch",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be processed without making changes",
        )

        parser.add_argument("--verbose", action="store_true", help="Verbose output")

    def handle(self, *args, **options):
        self.verbosity = options.get("verbosity", 1)
        self.verbose = options.get("verbose", False)
        self.dry_run = options.get("dry_run", False)
        self.batch_size = options.get("batch_size", 100)

        try:
            if options["fix_incomplete"]:
                return self.fix_incomplete_attendance(options)

            if options["reprocess_errors"]:
                return self.reprocess_error_logs(options)

            return self.process_attendance_logs(options)

        except Exception as e:
            logger.error(f"Log processing command failed: {str(e)}")
            raise CommandError(f"Command failed: {str(e)}")

    def process_attendance_logs(self, options):
        logs_query = AttendanceLog.objects.all()

        if options["status"]:
            logs_query = logs_query.filter(processing_status=options["status"])
        else:
            logs_query = logs_query.filter(processing_status="PENDING")

        if options["date"]:
            target_date = datetime.strptime(options["date"], "%Y-%m-%d").date()
            logs_query = logs_query.filter(timestamp__date=target_date)

        if options["date_range"]:
            start_date_str, end_date_str = options["date_range"].split(":")
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            logs_query = logs_query.filter(
                timestamp__date__range=[start_date, end_date]
            )

        if options["device_id"]:
            try:
                device = AttendanceDevice.objects.get(device_id=options["device_id"])
                logs_query = logs_query.filter(device=device)
            except AttendanceDevice.DoesNotExist:
                raise CommandError(f"Device with ID '{options['device_id']}' not found")

        if options["employee_code"]:
            logs_query = logs_query.filter(employee_code=options["employee_code"])

        logs_query = logs_query.order_by("timestamp")
        total_logs = logs_query.count()

        if total_logs == 0:
            self.stdout.write(self.style.WARNING("No logs found matching the criteria"))
            return

        self.stdout.write(f"Found {total_logs} logs to process")

        if self.dry_run:
            self.display_dry_run_summary(logs_query)
            return

        if options["async"]:
            task = process_pending_attendance_logs.delay()
            self.stdout.write(
                self.style.SUCCESS(f"Log processing task queued with ID: {task.id}")
            )
            return

        self.process_logs_in_batches(logs_query, total_logs)

    def process_logs_in_batches(self, logs_query, total_logs):
        processed_count = 0
        error_count = 0
        batch_number = 1

        grouped_logs = self.group_logs_by_employee_date(logs_query)

        self.stdout.write(f"Processing {len(grouped_logs)} employee-date groups...")

        for group_key, logs in grouped_logs.items():
            employee_code, log_date = group_key.split("_", 1)

            try:
                if self.verbose:
                    self.stdout.write(
                        f"🔄 Processing {len(logs)} logs for {employee_code} on {log_date}"
                    )

                with transaction.atomic():
                    DeviceService.process_employee_daily_logs(logs)
                    processed_count += len(logs)

                    if self.verbose:
                        self.stdout.write(f"   ✅ Success: {len(logs)} logs processed")

            except Exception as e:
                error_count += len(logs)
                error_message = str(e)

                self.stdout.write(
                    f"   ❌ Error processing {employee_code} on {log_date}: {error_message}"
                )

                for log in logs:
                    log.mark_as_error(error_message)

            if batch_number % 10 == 0:
                self.stdout.write(
                    f"📊 Progress: {processed_count + error_count}/{total_logs} logs"
                )

            batch_number += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 PROCESSING SUMMARY:\n"
                f"   ✅ Processed: {processed_count}\n"
                f"   ❌ Errors: {error_count}\n"
                f"   📋 Total: {total_logs}\n"
                f"   🕐 Completed at: {get_current_datetime()}"
            )
        )

    def group_logs_by_employee_date(self, logs_query):
        grouped_logs = {}

        for log in logs_query:
            key = f"{log.employee_code}_{log.timestamp.date()}"
            if key not in grouped_logs:
                grouped_logs[key] = []
            grouped_logs[key].append(log)

        return grouped_logs

    def reprocess_error_logs(self, options):
        error_logs = AttendanceLog.objects.filter(processing_status="ERROR")

        if options["date"]:
            target_date = datetime.strptime(options["date"], "%Y-%m-%d").date()
            error_logs = error_logs.filter(timestamp__date=target_date)

        if options["device_id"]:
            try:
                device = AttendanceDevice.objects.get(device_id=options["device_id"])
                error_logs = error_logs.filter(device=device)
            except AttendanceDevice.DoesNotExist:
                raise CommandError(f"Device with ID '{options['device_id']}' not found")

        total_errors = error_logs.count()

        if total_errors == 0:
            self.stdout.write(self.style.WARNING("No error logs found"))
            return

        self.stdout.write(f"Found {total_errors} error logs to reprocess")

        if self.dry_run:
            self.display_error_log_summary(error_logs)
            return

        reprocessed_count = 0
        still_error_count = 0

        grouped_logs = self.group_logs_by_employee_date(error_logs)

        for group_key, logs in grouped_logs.items():
            employee_code, log_date = group_key.split("_", 1)

            try:
                if self.verbose:
                    self.stdout.write(
                        f"🔄 Reprocessing {len(logs)} error logs for {employee_code} on {log_date}"
                    )

                for log in logs:
                    log.processing_status = "PENDING"
                    log.error_message = None
                    log.save(update_fields=["processing_status", "error_message"])

                with transaction.atomic():
                    DeviceService.process_employee_daily_logs(logs)
                    reprocessed_count += len(logs)

                    if self.verbose:
                        self.stdout.write(
                            f"   ✅ Success: {len(logs)} logs reprocessed"
                        )

            except Exception as e:
                still_error_count += len(logs)
                error_message = str(e)

                self.stdout.write(
                    f"   ❌ Still failing {employee_code} on {log_date}: {error_message}"
                )

                for log in logs:
                    log.mark_as_error(f"Reprocess failed: {error_message}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 REPROCESSING SUMMARY:\n"
                f"   ✅ Successfully reprocessed: {reprocessed_count}\n"
                f"   ❌ Still failing: {still_error_count}\n"
                f"   📋 Total attempted: {total_errors}\n"
                f"   🕐 Completed at: {get_current_datetime()}"
            )
        )

    def fix_incomplete_attendance(self, options):
        target_date = get_current_date() - timedelta(days=1)

        if options["date"]:
            target_date = datetime.strptime(options["date"], "%Y-%m-%d").date()

        incomplete_records = Attendance.objects.filter(
            date=target_date, status="INCOMPLETE"
        )

        if options["employee_code"]:
            employee = EmployeeDataManager.get_employee_by_code(
                options["employee_code"]
            )
            if not employee:
                raise CommandError(
                    f"Employee with code '{options['employee_code']}' not found"
                )
            incomplete_records = incomplete_records.filter(employee=employee)

        total_incomplete = incomplete_records.count()

        if total_incomplete == 0:
            self.stdout.write(
                self.style.WARNING(
                    f"No incomplete attendance records found for {target_date}"
                )
            )
            return

        self.stdout.write(
            f"Found {total_incomplete} incomplete attendance records for {target_date}"
        )

        if self.dry_run:
            self.display_incomplete_records_summary(incomplete_records)
            return

        if options["async"]:
            task = process_incomplete_attendance.delay()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Incomplete attendance processing task queued with ID: {task.id}"
                )
            )
            return

        fixed_count = 0

        for record in incomplete_records:
            try:
                if self.verbose:
                    self.stdout.write(
                        f"🔄 Fixing incomplete record for {record.employee.get_full_name()}"
                    )

                if record.first_in_time and not record.last_out_time:
                    schedule = EmployeeDataManager.get_employee_work_schedule(
                        record.employee
                    )
                    expected_out_time = (
                        timezone.datetime.combine(target_date, record.first_in_time)
                        + schedule["standard_work_time"]
                    ).time()

                    record.last_out_time = expected_out_time
                    record.status = "PRESENT"
                    record.notes = "Auto-completed: Missing check-out"
                    record.save()

                    fixed_count += 1

                    if self.verbose:
                        self.stdout.write(
                            f"   ✅ Fixed: Added check-out time {expected_out_time}"
                        )

            except Exception as e:
                self.stdout.write(
                    f"   ❌ Failed to fix record for {record.employee.get_full_name()}: {str(e)}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 INCOMPLETE RECORDS FIX SUMMARY:\n"
                f"   ✅ Fixed: {fixed_count}\n"
                f"   📋 Total incomplete: {total_incomplete}\n"
                f"   📅 Date: {target_date}\n"
                f"   🕐 Completed at: {get_current_datetime()}"
            )
        )

    def display_dry_run_summary(self, logs_query):
        self.stdout.write(self.style.WARNING("DRY RUN - No changes will be made\n"))

        status_counts = logs_query.values("processing_status").annotate(
            count=Count("id")
        )
        device_counts = logs_query.values("device__device_name").annotate(
            count=Count("id")
        )
        date_counts = (
            logs_query.extra({"date": "DATE(timestamp)"})
            .values("date")
            .annotate(count=Count("id"))
        )

        self.stdout.write("📊 LOGS BY STATUS:")
        for status in status_counts:
            self.stdout.write(f"   {status['processing_status']}: {status['count']}")

        self.stdout.write("\n📊 LOGS BY DEVICE:")
        for device in device_counts:
            device_name = device["device__device_name"] or "Unknown"
            self.stdout.write(f"   {device_name}: {device['count']}")

        self.stdout.write("\n📊 LOGS BY DATE:")
        for date_count in date_counts:
            self.stdout.write(f"   {date_count['date']}: {date_count['count']}")

    def display_error_log_summary(self, error_logs):
        self.stdout.write(
            self.style.WARNING("DRY RUN - Error logs that would be reprocessed\n")
        )

        error_reasons = error_logs.values("error_message").annotate(count=Count("id"))

        self.stdout.write("📊 ERROR REASONS:")
        for error in error_reasons:
            message = error["error_message"] or "Unknown error"
            self.stdout.write(f"   {message}: {error['count']}")

    def display_incomplete_records_summary(self, incomplete_records):
        self.stdout.write(
            self.style.WARNING("DRY RUN - Incomplete records that would be fixed\n")
        )

        for record in incomplete_records:
            self.stdout.write(
                f"📋 {record.employee.get_full_name()} ({record.employee.employee_code}):\n"
                f"   First In: {record.first_in_time}\n"
                f"   Last Out: {record.last_out_time}\n"
                f"   Status: {record.status}\n"
            )

    def get_version(self):
        return "1.0.0"
