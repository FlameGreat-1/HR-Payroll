from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q, Count, Sum, Avg
from django.utils import timezone
from accounts.models import CustomUser, Department, SystemConfiguration
from attendance.models import (
    Attendance,
    MonthlyAttendanceSummary,
    LeaveRequest,
    Holiday,
    EmployeeShift,
    Shift,
)
from attendance.services import AttendanceService, StatisticsService
from attendance.tasks import generate_monthly_summaries
from attendance.utils import (
    TimeCalculator,
    EmployeeDataManager,
    AttendanceCalculator,
    get_current_date,
    get_current_datetime,
)
from datetime import datetime, date, timedelta
from decimal import Decimal
import calendar
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate monthly attendance summaries for employees"

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            help="Year to generate summaries for (default: current year)",
        )

        parser.add_argument(
            "--month",
            type=int,
            help="Month to generate summaries for (1-12, default: current month)",
        )

        parser.add_argument(
            "--employee-id",
            type=int,
            help="Generate summary for specific employee only",
        )

        parser.add_argument(
            "--employee-code",
            type=str,
            help="Generate summary for specific employee by code",
        )

        parser.add_argument(
            "--department",
            type=str,
            help="Generate summaries for specific department only",
        )

        parser.add_argument(
            "--all-employees",
            action="store_true",
            help="Generate summaries for all active employees",
        )

        parser.add_argument(
            "--regenerate",
            action="store_true",
            help="Regenerate existing summaries (overwrite)",
        )

        parser.add_argument(
            "--date-range", type=str, help="Generate for date range (YYYY-MM:YYYY-MM)"
        )

        parser.add_argument(
            "--async", action="store_true", help="Run generation as background task"
        )

        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of employees to process in each batch",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be generated without making changes",
        )

        parser.add_argument("--verbose", action="store_true", help="Verbose output")

    def handle(self, *args, **options):
        self.verbosity = options.get("verbosity", 1)
        self.verbose = options.get("verbose", False)
        self.dry_run = options.get("dry_run", False)
        self.batch_size = options.get("batch_size", 50)
        self.regenerate = options.get("regenerate", False)

        try:
            if options.get("date_range"):
                return self.generate_for_date_range(options)

            year, month = self.get_target_period(options)
            employees = self.get_target_employees(options)

            if not employees.exists():
                self.stdout.write(
                    self.style.WARNING("No employees found matching criteria")
                )
                return

            if options.get("async"):
                return self.run_async_generation(year, month, options)

            return self.generate_summaries(employees, year, month)

        except Exception as e:
            logger.error(f"Monthly summary generation failed: {str(e)}")
            raise CommandError(f"Generation failed: {str(e)}")

    def get_target_period(self, options):
        if options.get("year") and options.get("month"):
            year = options["year"]
            month = options["month"]

            if month < 1 or month > 12:
                raise CommandError("Month must be between 1 and 12")

        else:
            current_date = get_current_date()

            if current_date.day < 5:
                last_month = current_date.replace(day=1) - timedelta(days=1)
                year = last_month.year
                month = last_month.month
            else:
                year = options.get("year", current_date.year)
                month = options.get("month", current_date.month)

        self.stdout.write(f"📅 Target period: {calendar.month_name[month]} {year}")
        return year, month

    def get_target_employees(self, options):
        employees = CustomUser.active.all()

        if options.get("employee_id"):
            try:
                employee = CustomUser.objects.get(id=options["employee_id"])
                employees = CustomUser.objects.filter(id=employee.id)
                self.stdout.write(f"👤 Target employee: {employee.get_full_name()}")
            except CustomUser.DoesNotExist:
                raise CommandError(
                    f"Employee with ID {options['employee_id']} not found"
                )

        elif options.get("employee_code"):
            employee = EmployeeDataManager.get_employee_by_code(
                options["employee_code"]
            )
            if not employee:
                raise CommandError(
                    f"Employee with code '{options['employee_code']}' not found"
                )
            employees = CustomUser.objects.filter(id=employee.id)
            self.stdout.write(f"👤 Target employee: {employee.get_full_name()}")

        elif options.get("department"):
            try:
                department = Department.objects.get(name__iexact=options["department"])
                employees = employees.filter(department=department)
                self.stdout.write(f"🏢 Target department: {department.name}")
            except Department.DoesNotExist:
                raise CommandError(f"Department '{options['department']}' not found")

        elif options.get("all_employees"):
            self.stdout.write(f"👥 Target: All active employees ({employees.count()})")

        else:
            raise CommandError(
                "Please specify --employee-id, --employee-code, --department, or --all-employees"
            )

        return employees

    def run_async_generation(self, year, month, options):
        task = generate_monthly_summaries.delay(year, month)

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Monthly summary generation task queued with ID: {task.id}\n"
                f"📅 Period: {calendar.month_name[month]} {year}\n"
                f"🔄 Status: Background processing started"
            )
        )

    def generate_summaries(self, employees, year, month):
        total_employees = employees.count()

        self.stdout.write(f"🔄 Generating summaries for {total_employees} employees...")

        if self.dry_run:
            return self.display_generation_preview(employees, year, month)

        generated_count = 0
        updated_count = 0
        error_count = 0
        batch_number = 1

        errors_list = []

        for start_idx in range(0, total_employees, self.batch_size):
            end_idx = min(start_idx + self.batch_size, total_employees)
            batch_employees = employees[start_idx:end_idx]

            self.stdout.write(
                f"🔄 Processing batch {batch_number} ({start_idx + 1}-{end_idx})..."
            )

            try:
                with transaction.atomic():
                    batch_result = self.process_employee_batch(
                        batch_employees, year, month
                    )

                    generated_count += batch_result["generated"]
                    updated_count += batch_result["updated"]
                    error_count += batch_result["errors"]
                    errors_list.extend(batch_result["error_details"])

                    if self.verbose:
                        self.stdout.write(
                            f"   ✅ Batch {batch_number}: "
                            f"{batch_result['generated']} generated, "
                            f"{batch_result['updated']} updated, "
                            f"{batch_result['errors']} errors"
                        )

            except Exception as e:
                error_message = f"Batch {batch_number} failed: {str(e)}"
                errors_list.append(error_message)
                error_count += len(batch_employees)

                self.stdout.write(f"   ❌ {error_message}")

            batch_number += 1

        self.display_generation_summary(
            generated_count, updated_count, error_count, errors_list, year, month
        )

    def process_employee_batch(self, employees, year, month):
        generated = 0
        updated = 0
        errors = 0
        error_details = []

        for employee in employees:
            try:
                result = self.generate_employee_summary(employee, year, month)

                if result["status"] == "generated":
                    generated += 1
                elif result["status"] == "updated":
                    updated += 1
                elif result["status"] == "error":
                    errors += 1
                    error_details.append(
                        f"{employee.get_full_name()}: {result['message']}"
                    )

                if self.verbose and result["status"] != "error":
                    self.stdout.write(
                        f"   ✅ {employee.get_full_name()}: {result['status']}"
                    )

            except Exception as e:
                errors += 1
                error_message = f"{employee.get_full_name()}: {str(e)}"
                error_details.append(error_message)

        return {
            "generated": generated,
            "updated": updated,
            "errors": errors,
            "error_details": error_details,
        }

    def generate_employee_summary(self, employee, year, month):
        try:
            if not self.should_generate_summary(employee, year, month):
                return {
                    "status": "skipped",
                    "message": "Employee not eligible for this period",
                }

            existing_summary = MonthlyAttendanceSummary.objects.filter(
                employee=employee, year=year, month=month
            ).first()

            if existing_summary and not self.regenerate:
                return {"status": "skipped", "message": "Summary already exists"}

            summary_data = self.calculate_monthly_summary(employee, year, month)

            if existing_summary:
                for key, value in summary_data.items():
                    setattr(existing_summary, key, value)
                existing_summary.save()
                return {"status": "updated", "summary": existing_summary}
            else:
                summary = MonthlyAttendanceSummary.objects.create(
                    employee=employee, year=year, month=month, **summary_data
                )
                return {"status": "generated", "summary": summary}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def should_generate_summary(self, employee, year, month):
        if not employee.hire_date:
            return False

        target_date = date(year, month, 1)

        if employee.hire_date > target_date:
            return False

        if employee.termination_date and employee.termination_date < target_date:
            return False

        return True
    
        def calculate_monthly_summary(self, employee, year, month):
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        
        attendance_records = Attendance.objects.filter(
            employee=employee,
            date__range=[month_start, month_end]
        ).order_by('date')
        
        leave_requests = LeaveRequest.objects.filter(
            employee=employee,
            status='APPROVED',
            start_date__lte=month_end,
            end_date__gte=month_start
        )
        
        holidays = Holiday.objects.filter(
            date__range=[month_start, month_end],
            is_active=True
        )
        
        employee_shift = EmployeeShift.objects.filter(
            employee=employee,
            effective_from__lte=month_end,
            effective_to__gte=month_start
        ).first()
        
        standard_shift = employee_shift.shift if employee_shift else Shift.objects.filter(
            shift_type='STANDARD',
            is_active=True
        ).first()
        
        summary_data = {
            'total_working_days': 0,
            'total_present_days': 0,
            'total_absent_days': 0,
            'total_late_days': 0,
            'total_early_departure_days': 0,
            'total_overtime_hours': Decimal('0.00'),
            'total_work_hours': Decimal('0.00'),
            'total_break_hours': Decimal('0.00'),
            'average_daily_hours': Decimal('0.00'),
            'attendance_percentage': Decimal('0.00'),
            'punctuality_percentage': Decimal('0.00'),
            'total_leave_days': Decimal('0.00'),
            'total_holiday_days': 0,
            'longest_continuous_work_streak': 0,
            'total_check_ins': 0,
            'average_first_check_in': None,
            'average_last_check_out': None,
            'most_productive_day': None,
            'least_productive_day': None
        }
        
        working_days_in_month = self.calculate_working_days(month_start, month_end, holidays)
        summary_data['total_working_days'] = working_days_in_month
        
        present_days = 0
        absent_days = 0
        late_days = 0
        early_departure_days = 0
        total_work_minutes = 0
        total_overtime_minutes = 0
        total_break_minutes = 0
        total_check_ins = 0
        
        first_check_in_times = []
        last_check_out_times = []
        daily_work_hours = []
        work_streak = 0
        longest_streak = 0
        
        current_date = month_start
        while current_date <= month_end:
            if self.is_working_day(current_date, holidays):
                attendance = attendance_records.filter(date=current_date).first()
                
                if attendance:
                    if attendance.status in ['PRESENT', 'LATE']:
                        present_days += 1
                        
                        if attendance.status == 'LATE':
                            late_days += 1
                        
                        if attendance.early_departure_minutes and attendance.early_departure_minutes > 0:
                            early_departure_days += 1
                        
                        daily_work_time = self.calculate_daily_work_time(attendance)
                        total_work_minutes += daily_work_time['work_minutes']
                        total_break_minutes += daily_work_time['break_minutes']
                        
                        if attendance.overtime:
                            total_overtime_minutes += attendance.overtime.total_seconds() / 60
                        
                        check_ins_count = self.count_daily_check_ins(attendance)
                        total_check_ins += check_ins_count
                        
                        if attendance.first_in_time:
                            first_check_in_times.append(attendance.first_in_time)
                        
                        if attendance.last_out_time:
                            last_check_out_times.append(attendance.last_out_time)
                        
                        daily_hours = daily_work_time['work_minutes'] / 60
                        daily_work_hours.append({
                            'date': current_date,
                            'hours': daily_hours
                        })
                        
                        work_streak += 1
                        longest_streak = max(longest_streak, work_streak)
                        
                    else:
                        absent_days += 1
                        work_streak = 0
                else:
                    if not self.is_on_approved_leave(current_date, leave_requests):
                        absent_days += 1
                    work_streak = 0
            
            current_date += timedelta(days=1)
        
        summary_data['total_present_days'] = present_days
        summary_data['total_absent_days'] = absent_days
        summary_data['total_late_days'] = late_days
        summary_data['total_early_departure_days'] = early_departure_days
        summary_data['total_work_hours'] = Decimal(str(round(total_work_minutes / 60, 2)))
        summary_data['total_overtime_hours'] = Decimal(str(round(total_overtime_minutes / 60, 2)))
        summary_data['total_break_hours'] = Decimal(str(round(total_break_minutes / 60, 2)))
        summary_data['total_check_ins'] = total_check_ins
        summary_data['longest_continuous_work_streak'] = longest_streak
        
        if present_days > 0:
            summary_data['average_daily_hours'] = Decimal(str(round(total_work_minutes / 60 / present_days, 2)))
        
        if working_days_in_month > 0:
            summary_data['attendance_percentage'] = Decimal(str(round(present_days / working_days_in_month * 100, 2)))
            summary_data['punctuality_percentage'] = Decimal(str(round((present_days - late_days) / working_days_in_month * 100, 2)))
        
        if first_check_in_times:
            avg_check_in = self.calculate_average_time(first_check_in_times)
            summary_data['average_first_check_in'] = avg_check_in
        
        if last_check_out_times:
            avg_check_out = self.calculate_average_time(last_check_out_times)
            summary_data['average_last_check_out'] = avg_check_out
        
        if daily_work_hours:
            most_productive = max(daily_work_hours, key=lambda x: x['hours'])
            least_productive = min(daily_work_hours, key=lambda x: x['hours'])
            summary_data['most_productive_day'] = most_productive['date']
            summary_data['least_productive_day'] = least_productive['date']
        
        leave_days = self.calculate_leave_days(employee, year, month, leave_requests)
        summary_data['total_leave_days'] = leave_days
        
        summary_data['total_holiday_days'] = holidays.count()
        
        return summary_data

    def calculate_working_days(self, start_date, end_date, holidays):
        working_days = 0
        current_date = start_date
        
        holiday_dates = set(holidays.values_list('date', flat=True))
        
        while current_date <= end_date:
            if current_date.weekday() < 5 and current_date not in holiday_dates:
                working_days += 1
            current_date += timedelta(days=1)
        
        return working_days

    def is_working_day(self, target_date, holidays):
        if target_date.weekday() >= 5:
            return False
        
        holiday_dates = set(holidays.values_list('date', flat=True))
        return target_date not in holiday_dates

    def calculate_daily_work_time(self, attendance):
        work_minutes = 0
        break_minutes = 0
        
        time_pairs = [
            (attendance.check_in_1, attendance.check_out_1),
            (attendance.check_in_2, attendance.check_out_2),
            (attendance.check_in_3, attendance.check_out_3),
            (attendance.check_in_4, attendance.check_out_4),
            (attendance.check_in_5, attendance.check_out_5),
            (attendance.check_in_6, attendance.check_out_6),
        ]
        
        valid_pairs = [(in_time, out_time) for in_time, out_time in time_pairs if in_time and out_time]
        
        for in_time, out_time in valid_pairs:
            session_minutes = (
                datetime.combine(date.today(), out_time) -
                datetime.combine(date.today(), in_time)
            ).total_seconds() / 60
            work_minutes += session_minutes
        
        if len(valid_pairs) > 1:
            for i in range(len(valid_pairs) - 1):
                break_start = valid_pairs[i][1]
                break_end = valid_pairs[i + 1][0]
                
                break_duration = (
                    datetime.combine(date.today(), break_end) -
                    datetime.combine(date.today(), break_start)
                ).total_seconds() / 60
                
                break_minutes += break_duration
        
        return {
            'work_minutes': work_minutes,
            'break_minutes': break_minutes
        }

    def count_daily_check_ins(self, attendance):
        check_ins = 0
        
        if attendance.check_in_1:
            check_ins += 1
        if attendance.check_in_2:
            check_ins += 1
        if attendance.check_in_3:
            check_ins += 1
        if attendance.check_in_4:
            check_ins += 1
        if attendance.check_in_5:
            check_ins += 1
        if attendance.check_in_6:
            check_ins += 1
        
        return check_ins

    def is_on_approved_leave(self, target_date, leave_requests):
        for leave_request in leave_requests:
            if leave_request.start_date <= target_date <= leave_request.end_date:
                return True
        return False

    def calculate_average_time(self, time_list):
        if not time_list:
            return None
        
        total_seconds = sum(
            (datetime.combine(date.today(), t) - datetime.combine(date.today(), time(0, 0))).total_seconds()
            for t in time_list
        )
        
        average_seconds = total_seconds / len(time_list)
        average_time = (datetime.combine(date.today(), time(0, 0)) + timedelta(seconds=average_seconds)).time()
        
        return average_time

    def calculate_leave_days(self, employee, year, month, leave_requests):
        total_leave_days = Decimal('0.00')
        
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        
        for leave_request in leave_requests:
            leave_start = max(leave_request.start_date, month_start)
            leave_end = min(leave_request.end_date, month_end)
            
            if leave_start <= leave_end:
                if leave_request.is_half_day:
                    total_leave_days += Decimal('0.5')
                else:
                    days_count = (leave_end - leave_start).days + 1
                    working_days = 0
                    
                    current_date = leave_start
                    while current_date <= leave_end:
                        if current_date.weekday() < 5:
                            working_days += 1
                        current_date += timedelta(days=1)
                    
                    total_leave_days += Decimal(str(working_days))
        
        return total_leave_days

    def display_generation_preview(self, employees, year, month):
        self.stdout.write(self.style.WARNING("DRY RUN - No summaries will be generated\n"))
        
        preview_count = min(5, employees.count())
        
        self.stdout.write(f"📋 GENERATION PREVIEW (showing first {preview_count} employees):")
        
        for employee in employees[:preview_count]:
            existing_summary = MonthlyAttendanceSummary.objects.filter(
                employee=employee,
                year=year,
                month=month
            ).first()
            
            status = "EXISTS" if existing_summary else "NEW"
            if existing_summary and self.regenerate:
                status = "WILL REGENERATE"
            
            self.stdout.write(
                f"\n👤 {employee.get_full_name()} ({employee.employee_code}):\n"
                f"   📊 Status: {status}\n"
                f"   🏢 Department: {employee.department.name if employee.department else 'N/A'}\n"
                f"   📅 Hire Date: {employee.hire_date}\n"
            )
        
        total_existing = MonthlyAttendanceSummary.objects.filter(
            employee__in=employees,
            year=year,
            month=month
        ).count()
        
        self.stdout.write(
            f"\n📊 GENERATION SUMMARY:\n"
            f"   👥 Total employees: {employees.count()}\n"
            f"   📋 Existing summaries: {total_existing}\n"
            f"   ➕ New summaries: {employees.count() - total_existing}\n"
            f"   📅 Period: {calendar.month_name[month]} {year}\n"
            f"   🔄 Regenerate mode: {'Yes' if self.regenerate else 'No'}"
        )
    
        def display_generation_summary(self, generated_count, updated_count, error_count, errors_list, year, month):
        total_processed = generated_count + updated_count + error_count
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 MONTHLY SUMMARY GENERATION COMPLETED:\n"
                f"   ✅ New summaries generated: {generated_count}\n"
                f"   🔄 Existing summaries updated: {updated_count}\n"
                f"   ❌ Errors: {error_count}\n"
                f"   📋 Total processed: {total_processed}\n"
                f"   📅 Period: {calendar.month_name[month]} {year}\n"
                f"   🕐 Completed at: {get_current_datetime()}"
            )
        )
        
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f"\n❌ GENERATION ERRORS ({len(errors_list)} total):"))
            
            display_count = min(10, len(errors_list))
            for i, error in enumerate(errors_list[:display_count], 1):
                self.stdout.write(f"   {i}. {error}")
            
            if len(errors_list) > display_count:
                self.stdout.write(f"   ... and {len(errors_list) - display_count} more errors")
            
            error_log_path = self.save_error_log(errors_list, year, month)
            if error_log_path:
                self.stdout.write(f"   📄 Full error log saved to: {error_log_path}")
        
        if generated_count > 0 or updated_count > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n🎉 Generation successful! {generated_count + updated_count} summaries processed."
                )
            )
            
            self.display_summary_statistics(year, month)

    def save_error_log(self, errors_list, year, month):
        try:
            timestamp = get_current_datetime().strftime('%Y%m%d_%H%M%S')
            error_filename = f"monthly_summary_errors_{year}_{month:02d}_{timestamp}.txt"
            error_path = f"/tmp/{error_filename}"
            
            with open(error_path, 'w') as error_file:
                error_file.write(f"Monthly Summary Generation Error Log\n")
                error_file.write(f"Generated: {get_current_datetime()}\n")
                error_file.write(f"Period: {calendar.month_name[month]} {year}\n")
                error_file.write(f"Total Errors: {len(errors_list)}\n")
                error_file.write("=" * 50 + "\n\n")
                
                for i, error in enumerate(errors_list, 1):
                    error_file.write(f"{i}. {error}\n")
            
            return error_path
            
        except Exception as e:
            self.stdout.write(f"   ⚠️  Could not save error log: {str(e)}")
            return None

    def display_summary_statistics(self, year, month):
        try:
            summaries = MonthlyAttendanceSummary.objects.filter(year=year, month=month)
            
            if not summaries.exists():
                return
            
            total_summaries = summaries.count()
            avg_attendance = summaries.aggregate(Avg('attendance_percentage'))['attendance_percentage__avg'] or 0
            avg_punctuality = summaries.aggregate(Avg('punctuality_percentage'))['punctuality_percentage__avg'] or 0
            total_work_hours = summaries.aggregate(Sum('total_work_hours'))['total_work_hours__sum'] or 0
            total_overtime = summaries.aggregate(Sum('total_overtime_hours'))['total_overtime_hours__sum'] or 0
            
            high_performers = summaries.filter(attendance_percentage__gte=95).count()
            low_performers = summaries.filter(attendance_percentage__lt=80).count()
            
            self.stdout.write(
                f"\n📈 PERIOD STATISTICS:\n"
                f"   📊 Total summaries: {total_summaries}\n"
                f"   📈 Average attendance: {avg_attendance:.1f}%\n"
                f"   ⏰ Average punctuality: {avg_punctuality:.1f}%\n"
                f"   🕐 Total work hours: {total_work_hours:.1f}\n"
                f"   ⏱️  Total overtime: {total_overtime:.1f} hours\n"
                f"   🌟 High performers (≥95%): {high_performers}\n"
                f"   ⚠️  Low performers (<80%): {low_performers}"
            )
            
        except Exception as e:
            if self.verbose:
                self.stdout.write(f"   ⚠️  Could not generate statistics: {str(e)}")

    def generate_for_date_range(self, options):
        try:
            start_period, end_period = options['date_range'].split(':')
            start_year, start_month = map(int, start_period.split('-'))
            end_year, end_month = map(int, end_period.split('-'))
            
            if start_month < 1 or start_month > 12 or end_month < 1 or end_month > 12:
                raise CommandError("Month must be between 1 and 12")
            
            employees = self.get_target_employees(options)
            
            if not employees.exists():
                self.stdout.write(self.style.WARNING("No employees found matching criteria"))
                return
            
            current_year = start_year
            current_month = start_month
            
            total_generated = 0
            total_updated = 0
            total_errors = 0
            
            while (current_year < end_year) or (current_year == end_year and current_month <= end_month):
                self.stdout.write(f"\n📅 Processing {calendar.month_name[current_month]} {current_year}...")
                
                if self.dry_run:
                    self.display_generation_preview(employees, current_year, current_month)
                else:
                    batch_result = self.process_employee_batch(employees, current_year, current_month)
                    total_generated += batch_result['generated']
                    total_updated += batch_result['updated']
                    total_errors += batch_result['errors']
                
                current_month += 1
                if current_month > 12:
                    current_month = 1
                    current_year += 1
            
            if not self.dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n🎉 DATE RANGE GENERATION COMPLETED:\n"
                        f"   ✅ Total generated: {total_generated}\n"
                        f"   🔄 Total updated: {total_updated}\n"
                        f"   ❌ Total errors: {total_errors}\n"
                        f"   📅 Period: {start_period} to {end_period}"
                    )
                )
            
        except ValueError:
            raise CommandError("Invalid date range format. Use YYYY-MM:YYYY-MM")

    def validate_generation_requirements(self, employees, year, month):
        issues = []
        
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        
        for employee in employees[:10]:
            if not employee.hire_date:
                issues.append(f"{employee.get_full_name()}: Missing hire date")
            
            elif employee.hire_date > month_end:
                issues.append(f"{employee.get_full_name()}: Hired after target period")
            
            attendance_count = Attendance.objects.filter(
                employee=employee,
                date__range=[month_start, month_end]
            ).count()
            
            if attendance_count == 0:
                issues.append(f"{employee.get_full_name()}: No attendance records for period")
        
        return issues

    def cleanup_invalid_summaries(self, year, month):
        try:
            invalid_summaries = MonthlyAttendanceSummary.objects.filter(
                year=year,
                month=month,
                total_working_days=0
            )
            
            deleted_count = invalid_summaries.count()
            if deleted_count > 0:
                invalid_summaries.delete()
                self.stdout.write(f"🗑️  Cleaned up {deleted_count} invalid summaries")
            
        except Exception as e:
            if self.verbose:
                self.stdout.write(f"⚠️  Could not cleanup invalid summaries: {str(e)}")

    def generate_summary_report(self, year, month):
        try:
            summaries = MonthlyAttendanceSummary.objects.filter(year=year, month=month)
            
            report_data = {
                'generation_timestamp': str(get_current_datetime()),
                'period': f"{calendar.month_name[month]} {year}",
                'total_summaries': summaries.count(),
                'statistics': {
                    'average_attendance': float(summaries.aggregate(Avg('attendance_percentage'))['attendance_percentage__avg'] or 0),
                    'average_punctuality': float(summaries.aggregate(Avg('punctuality_percentage'))['punctuality_percentage__avg'] or 0),
                    'total_work_hours': float(summaries.aggregate(Sum('total_work_hours'))['total_work_hours__sum'] or 0),
                    'total_overtime_hours': float(summaries.aggregate(Sum('total_overtime_hours'))['total_overtime_hours__sum'] or 0),
                    'high_performers': summaries.filter(attendance_percentage__gte=95).count(),
                    'low_performers': summaries.filter(attendance_percentage__lt=80).count()
                }
            }
            
            timestamp = get_current_datetime().strftime('%Y%m%d_%H%M%S')
            report_filename = f"monthly_summary_report_{year}_{month:02d}_{timestamp}.json"
            report_path = f"/tmp/{report_filename}"
            
            import json
            with open(report_path, 'w') as report_file:
                json.dump(report_data, report_file, indent=2, default=str)
            
            if self.verbose:
                self.stdout.write(f"📄 Summary report saved to: {report_path}")
            
            return report_path
            
        except Exception as e:
            if self.verbose:
                self.stdout.write(f"⚠️  Could not generate summary report: {str(e)}")
            return None

    def verify_summary_accuracy(self, summary, employee, year, month):
        try:
            month_start = date(year, month, 1)
            month_end = date(year, month, calendar.monthrange(year, month)[1])
            
            actual_attendance = Attendance.objects.filter(
                employee=employee,
                date__range=[month_start, month_end],
                status__in=['PRESENT', 'LATE']
            ).count()
            
            if abs(summary.total_present_days - actual_attendance) > 0:
                return False, f"Present days mismatch: Summary={summary.total_present_days}, Actual={actual_attendance}"
            
            return True, "Summary verified successfully"
            
        except Exception as e:
            return False, f"Verification failed: {str(e)}"

    def get_version(self):
        return "1.0.0"

    def add_help_text(self):
        return """
Monthly Summary Generation Command Help:

EXAMPLES:
python manage.py generate_monthly_summary --all-employees --year 2024 --month 1
python manage.py generate_monthly_summary --employee-code EMP001 --year 2024 --month 1
python manage.py generate_monthly_summary --department "Engineering" --regenerate
python manage.py generate_monthly_summary --all-employees --date-range 2024-01:2024-12
python manage.py generate_monthly_summary --all-employees --async --batch-size 25

NOTES:
- Summaries include all 6 IN/OUT pairs data
- Working days exclude weekends and holidays
- Overtime is calculated based on standard shift hours
- Use --regenerate to update existing summaries
- Large batches should use --async for background processing
- Use --dry-run to preview what would be generated
"""


    

