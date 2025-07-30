from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from accounts.models import CustomUser, SystemConfiguration
from attendance.models import Attendance, AttendanceDevice
from attendance.services import ExcelService, AttendanceService
from attendance.tasks import import_attendance_from_excel
from attendance.utils import (
    ValidationHelper,
    EmployeeDataManager,
    ExcelProcessor,
    get_current_date,
    get_current_datetime,
    safe_date_conversion,
    safe_time_conversion,
)
from datetime import datetime, date, time, timedelta
import pandas as pd
import os
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    help = "Import attendance data from Excel files with 6 IN/OUT pairs"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="Path to Excel file to import")

        parser.add_argument(
            "--user-id",
            type=int,
            help="User ID performing the import (required for audit trail)",
        )

        parser.add_argument(
            "--user-email", type=str, help="Email of user performing the import"
        )

        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite existing attendance records",
        )

        parser.add_argument(
            "--validate-only",
            action="store_true",
            help="Only validate the file without importing",
        )

        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of records to process in each batch",
        )

        parser.add_argument(
            "--skip-errors",
            action="store_true",
            help="Continue processing even if some records fail",
        )

        parser.add_argument(
            "--date-range",
            type=str,
            help="Only import records within date range (YYYY-MM-DD:YYYY-MM-DD)",
        )

        parser.add_argument(
            "--department", type=str, help="Only import records for specific department"
        )

        parser.add_argument(
            "--async", action="store_true", help="Run import as background task"
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be imported without making changes",
        )

        parser.add_argument("--verbose", action="store_true", help="Verbose output")

    def handle(self, *args, **options):
        self.verbosity = options.get("verbosity", 1)
        self.verbose = options.get("verbose", False)
        self.dry_run = options.get("dry_run", False)
        self.batch_size = options.get("batch_size", 50)
        self.skip_errors = options.get("skip_errors", False)
        self.overwrite = options.get("overwrite", False)

        try:
            file_path = options["file_path"]

            if not os.path.exists(file_path):
                raise CommandError(f"File not found: {file_path}")

            if not file_path.lower().endswith((".xlsx", ".xls")):
                raise CommandError("File must be an Excel file (.xlsx or .xls)")

            user = self.get_import_user(options)

            if options["validate_only"]:
                return self.validate_excel_file(file_path)

            if options["async"]:
                return self.run_async_import(file_path, user, options)

            return self.import_excel_file(file_path, user, options)

        except Exception as e:
            logger.error(f"Excel import command failed: {str(e)}")
            raise CommandError(f"Import failed: {str(e)}")

    def get_import_user(self, options):
        if options.get("user_id"):
            try:
                return User.objects.get(id=options["user_id"])
            except User.DoesNotExist:
                raise CommandError(f"User with ID {options['user_id']} not found")

        if options.get("user_email"):
            try:
                return User.objects.get(email=options["user_email"])
            except User.DoesNotExist:
                raise CommandError(f"User with email {options['user_email']} not found")

        superusers = User.objects.filter(is_superuser=True, is_active=True)
        if superusers.exists():
            user = superusers.first()
            self.stdout.write(
                self.style.WARNING(f"No user specified, using superuser: {user.email}")
            )
            return user

        raise CommandError("No user specified and no active superuser found")

    def validate_excel_file(self, file_path):
        self.stdout.write(f"🔍 Validating Excel file: {file_path}")

        try:
            with open(file_path, "rb") as file:
                file_content = file.read()

            is_valid, message, df = ValidationHelper.validate_excel_file(file_content)

            if not is_valid:
                self.stdout.write(self.style.ERROR(f"❌ Validation failed: {message}"))
                return

            self.stdout.write(self.style.SUCCESS(f"✅ File validation passed"))
            self.stdout.write(f"📊 Found {len(df)} rows to process")

            self.display_file_summary(df)

            validation_errors = self.validate_data_content(df)

            if validation_errors:
                self.stdout.write(
                    self.style.ERROR(f"\n❌ Data validation errors found:")
                )
                for i, error in enumerate(validation_errors[:10], 1):
                    self.stdout.write(f"   {i}. {error}")

                if len(validation_errors) > 10:
                    self.stdout.write(
                        f"   ... and {len(validation_errors) - 10} more errors"
                    )

                return

            self.stdout.write(
                self.style.SUCCESS("✅ All data validation checks passed")
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Validation error: {str(e)}"))

    def display_file_summary(self, df):
        self.stdout.write("\n📋 FILE SUMMARY:")
        self.stdout.write(f"   📊 Total rows: {len(df)}")

        if "ID" in df.columns:
            unique_employees = df["ID"].nunique()
            self.stdout.write(f"   👥 Unique employees: {unique_employees}")

        if "Date" in df.columns:
            date_range = self.get_date_range_from_df(df)
            if date_range:
                self.stdout.write(
                    f"   📅 Date range: {date_range['start']} to {date_range['end']}"
                )

        self.stdout.write(f"   📋 Columns: {', '.join(df.columns.tolist())}")

    def get_date_range_from_df(self, df):
        try:
            dates = []
            for date_val in df["Date"].dropna():
                converted_date = safe_date_conversion(date_val)
                if converted_date:
                    dates.append(converted_date)

            if dates:
                return {"start": min(dates), "end": max(dates)}
        except Exception:
            pass

        return None

    def validate_data_content(self, df):
        errors = []

        required_columns = ["ID", "Date"]
        for col in required_columns:
            if col not in df.columns:
                errors.append(f"Missing required column: {col}")

        if errors:
            return errors

        for index, row in df.iterrows():
            row_num = index + 2

            employee_code = ValidationHelper.sanitize_employee_code(
                str(row.get("ID", ""))
            )
            if not employee_code:
                errors.append(f"Row {row_num}: Missing or invalid employee ID")
                continue

            employee = EmployeeDataManager.get_employee_by_code(employee_code)
            if not employee:
                errors.append(f"Row {row_num}: Employee {employee_code} not found")

            attendance_date = safe_date_conversion(row.get("Date"))
            if not attendance_date:
                errors.append(f"Row {row_num}: Invalid date format")

            time_pairs = []
            for i in range(1, 7):
                in_time = safe_time_conversion(row.get(f"In{i}"))
                out_time = safe_time_conversion(row.get(f"Out{i}"))

                if in_time and out_time:
                    if out_time <= in_time:
                        errors.append(
                            f"Row {row_num}: Out{i} time must be after In{i} time"
                        )

                time_pairs.append((in_time, out_time))

            is_valid, time_errors = ValidationHelper.validate_attendance_consistency(
                time_pairs
            )
            if not is_valid:
                for error in time_errors:
                    errors.append(f"Row {row_num}: {error}")

        return errors
    
        def run_async_import(self, file_path, user, options):
        try:
            with open(file_path, 'rb') as file:
                file_content = file.read()
            
            temp_filename = f"import_{get_current_datetime().strftime('%Y%m%d_%H%M%S')}.xlsx"
            temp_path = default_storage.save(f"temp/{temp_filename}", ContentFile(file_content))
            
            task = import_attendance_from_excel.delay(
                default_storage.path(temp_path),
                user.id,
                {
                    'overwrite': self.overwrite,
                    'skip_errors': self.skip_errors,
                    'batch_size': self.batch_size,
                    'date_range': options.get('date_range'),
                    'department': options.get('department')
                }
            )
            
            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ Import task queued with ID: {task.id}\n"
                    f"📁 File: {file_path}\n"
                    f"👤 User: {user.get_full_name()}\n"
                    f"🔄 Status: Background processing started"
                )
            )
            
        except Exception as e:
            raise CommandError(f"Failed to queue async import: {str(e)}")

    def import_excel_file(self, file_path, user, options):
        self.stdout.write(f"📥 Starting Excel import: {file_path}")
        self.stdout.write(f"👤 Import user: {user.get_full_name()}")
        
        try:
            with open(file_path, 'rb') as file:
                file_content = file.read()
            
            is_valid, message, df = ValidationHelper.validate_excel_file(file_content)
            
            if not is_valid:
                raise CommandError(f"File validation failed: {message}")
            
            if options.get('date_range'):
                df = self.filter_by_date_range(df, options['date_range'])
            
            if options.get('department'):
                df = self.filter_by_department(df, options['department'])
            
            total_rows = len(df)
            
            if total_rows == 0:
                self.stdout.write(self.style.WARNING("No records to import after filtering"))
                return
            
            self.stdout.write(f"📊 Processing {total_rows} records...")
            
            if self.dry_run:
                return self.display_import_preview(df, user)
            
            return self.process_import_batches(df, user, total_rows)
            
        except Exception as e:
            logger.error(f"Excel import failed: {str(e)}")
            raise CommandError(f"Import failed: {str(e)}")

    def filter_by_date_range(self, df, date_range):
        try:
            start_date_str, end_date_str = date_range.split(':')
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            
            filtered_df = df[df['Date'].apply(
                lambda x: start_date <= safe_date_conversion(x) <= end_date
                if safe_date_conversion(x) else False
            )]
            
            self.stdout.write(f"📅 Date filter applied: {len(filtered_df)}/{len(df)} records")
            return filtered_df
            
        except Exception as e:
            raise CommandError(f"Invalid date range format: {str(e)}")

    def filter_by_department(self, df, department_name):
        try:
            from accounts.models import Department
            department = Department.objects.get(name__iexact=department_name)
            
            department_employees = CustomUser.objects.filter(
                department=department,
                is_active=True
            ).values_list('employee_code', flat=True)
            
            filtered_df = df[df['ID'].apply(
                lambda x: ValidationHelper.sanitize_employee_code(str(x)) in department_employees
            )]
            
            self.stdout.write(f"🏢 Department filter applied: {len(filtered_df)}/{len(df)} records")
            return filtered_df
            
        except Department.DoesNotExist:
            raise CommandError(f"Department '{department_name}' not found")

    def display_import_preview(self, df, user):
        self.stdout.write(self.style.WARNING("DRY RUN - No changes will be made\n"))
        
        preview_count = min(5, len(df))
        
        self.stdout.write(f"📋 IMPORT PREVIEW (showing first {preview_count} records):")
        
        for index, row in df.head(preview_count).iterrows():
            employee_code = ValidationHelper.sanitize_employee_code(str(row.get('ID', '')))
            attendance_date = safe_date_conversion(row.get('Date'))
            
            self.stdout.write(f"\n📝 Record {index + 1}:")
            self.stdout.write(f"   👤 Employee: {employee_code}")
            self.stdout.write(f"   📅 Date: {attendance_date}")
            
            for i in range(1, 7):
                in_time = safe_time_conversion(row.get(f'In{i}'))
                out_time = safe_time_conversion(row.get(f'Out{i}'))
                
                if in_time or out_time:
                    self.stdout.write(f"   🕐 Pair {i}: {in_time or 'N/A'} - {out_time or 'N/A'}")
        
        existing_records = self.count_existing_records(df)
        
        self.stdout.write(f"\n📊 IMPORT SUMMARY:")
        self.stdout.write(f"   📋 Total records: {len(df)}")
        self.stdout.write(f"   🔄 Existing records: {existing_records}")
        self.stdout.write(f"   ➕ New records: {len(df) - existing_records}")
        self.stdout.write(f"   👤 Import user: {user.get_full_name()}")
        self.stdout.write(f"   🔄 Overwrite mode: {'Yes' if self.overwrite else 'No'}")

    def count_existing_records(self, df):
        existing_count = 0
        
        for index, row in df.iterrows():
            employee_code = ValidationHelper.sanitize_employee_code(str(row.get('ID', '')))
            attendance_date = safe_date_conversion(row.get('Date'))
            
            if employee_code and attendance_date:
                employee = EmployeeDataManager.get_employee_by_code(employee_code)
                if employee:
                    if Attendance.objects.filter(employee=employee, date=attendance_date).exists():
                        existing_count += 1
        
        return existing_count

    def process_import_batches(self, df, user, total_rows):
        imported_count = 0
        error_count = 0
        skipped_count = 0
        batch_number = 1
        
        errors_list = []
        
        for start_idx in range(0, total_rows, self.batch_size):
            end_idx = min(start_idx + self.batch_size, total_rows)
            batch_df = df.iloc[start_idx:end_idx]
            
            self.stdout.write(f"🔄 Processing batch {batch_number} ({start_idx + 1}-{end_idx})...")
            
            try:
                with transaction.atomic():
                    batch_result = self.process_batch(batch_df, user)
                    
                    imported_count += batch_result['imported']
                    error_count += batch_result['errors']
                    skipped_count += batch_result['skipped']
                    errors_list.extend(batch_result['error_details'])
                    
                    if self.verbose:
                        self.stdout.write(
                            f"   ✅ Batch {batch_number}: "
                            f"{batch_result['imported']} imported, "
                            f"{batch_result['errors']} errors, "
                            f"{batch_result['skipped']} skipped"
                        )
                
            except Exception as e:
                error_message = f"Batch {batch_number} failed: {str(e)}"
                errors_list.append(error_message)
                error_count += len(batch_df)
                
                self.stdout.write(f"   ❌ {error_message}")
                
                if not self.skip_errors:
                    raise CommandError(f"Import stopped due to batch error: {str(e)}")
            
            batch_number += 1
        
        self.display_import_summary(imported_count, error_count, skipped_count, errors_list, user)

    def process_batch(self, batch_df, user):
        imported = 0
        errors = 0
        skipped = 0
        error_details = []
        
        for index, row in batch_df.iterrows():
            try:
                result = self.process_single_record(row, user, index + 2)
                
                if result['status'] == 'imported':
                    imported += 1
                elif result['status'] == 'skipped':
                    skipped += 1
                    if self.verbose:
                        self.stdout.write(f"   ⏭️  Row {index + 2}: {result['message']}")
                elif result['status'] == 'error':
                    errors += 1
                    error_details.append(f"Row {index + 2}: {result['message']}")
                    
                    if not self.skip_errors:
                        raise Exception(result['message'])
                
            except Exception as e:
                errors += 1
                error_message = f"Row {index + 2}: {str(e)}"
                error_details.append(error_message)
                
                if not self.skip_errors:
                    raise Exception(error_message)
        
        return {
            'imported': imported,
            'errors': errors,
            'skipped': skipped,
            'error_details': error_details
        }

    def process_single_record(self, row, user, row_number):
        employee_code = ValidationHelper.sanitize_employee_code(str(row.get('ID', '')))
        attendance_date = safe_date_conversion(row.get('Date'))
        
        if not employee_code:
            return {'status': 'error', 'message': 'Missing employee ID'}
        
        if not attendance_date:
            return {'status': 'error', 'message': 'Invalid date'}
        
        employee = EmployeeDataManager.get_employee_by_code(employee_code)
        if not employee:
            return {'status': 'error', 'message': f'Employee {employee_code} not found'}
        
        existing_attendance = Attendance.objects.filter(
            employee=employee,
            date=attendance_date
        ).first()
        
        if existing_attendance and not self.overwrite:
            return {'status': 'skipped', 'message': 'Record already exists'}
        
        attendance_data = {
            'employee': employee,
            'date': attendance_date,
            'is_manual_entry': True,
            'created_by': user
        }
        
        for i in range(1, 7):
            in_time = safe_time_conversion(row.get(f'In{i}'))
            out_time = safe_time_conversion(row.get(f'Out{i}'))
            
            if in_time:
                attendance_data[f'check_in_{i}'] = in_time
            if out_time:
                attendance_data[f'check_out_{i}'] = out_time
        
        if existing_attendance:
            for key, value in attendance_data.items():
                if key != 'employee' and key != 'date':
                    setattr(existing_attendance, key, value)
            existing_attendance.save()
        else:
            Attendance.objects.create(**attendance_data)
        
        return {'status': 'imported', 'message': 'Success'}
    
        def display_import_summary(self, imported_count, error_count, skipped_count, errors_list, user):
        total_processed = imported_count + error_count + skipped_count
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 IMPORT COMPLETED:\n"
                f"   ✅ Successfully imported: {imported_count}\n"
                f"   ❌ Errors: {error_count}\n"
                f"   ⏭️  Skipped: {skipped_count}\n"
                f"   📋 Total processed: {total_processed}\n"
                f"   👤 Import user: {user.get_full_name()}\n"
                f"   🕐 Completed at: {get_current_datetime()}"
            )
        )
        
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f"\n❌ IMPORT ERRORS ({len(errors_list)} total):"))
            
            display_count = min(10, len(errors_list))
            for i, error in enumerate(errors_list[:display_count], 1):
                self.stdout.write(f"   {i}. {error}")
            
            if len(errors_list) > display_count:
                self.stdout.write(f"   ... and {len(errors_list) - display_count} more errors")
            
            error_log_path = self.save_error_log(errors_list, user)
            if error_log_path:
                self.stdout.write(f"   📄 Full error log saved to: {error_log_path}")
        
        if imported_count > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n🎉 Import successful! {imported_count} attendance records imported."
                )
            )
            
            self.display_post_import_statistics(user)

    def save_error_log(self, errors_list, user):
        try:
            timestamp = get_current_datetime().strftime('%Y%m%d_%H%M%S')
            error_filename = f"import_errors_{timestamp}.txt"
            error_path = os.path.join('/tmp', error_filename)
            
            with open(error_path, 'w') as error_file:
                error_file.write(f"Excel Import Error Log\n")
                error_file.write(f"Generated: {get_current_datetime()}\n")
                error_file.write(f"Import User: {user.get_full_name()}\n")
                error_file.write(f"Total Errors: {len(errors_list)}\n")
                error_file.write("=" * 50 + "\n\n")
                
                for i, error in enumerate(errors_list, 1):
                    error_file.write(f"{i}. {error}\n")
            
            return error_path
            
        except Exception as e:
            self.stdout.write(f"   ⚠️  Could not save error log: {str(e)}")
            return None

    def display_post_import_statistics(self, user):
        try:
            today = get_current_date()
            
            today_imports = Attendance.objects.filter(
                created_by=user,
                created_at__date=today,
                is_manual_entry=True
            ).count()
            
            total_user_imports = Attendance.objects.filter(
                created_by=user,
                is_manual_entry=True
            ).count()
            
            self.stdout.write(
                f"\n📈 IMPORT STATISTICS:\n"
                f"   📅 Today's imports by {user.get_full_name()}: {today_imports}\n"
                f"   📊 Total imports by {user.get_full_name()}: {total_user_imports}"
            )
            
        except Exception as e:
            if self.verbose:
                self.stdout.write(f"   ⚠️  Could not generate statistics: {str(e)}")

    def validate_import_permissions(self, user):
        if not user.is_active:
            raise CommandError("Import user is not active")
        
        if not user.has_perm('attendance.add_attendance'):
            raise CommandError("Import user does not have permission to add attendance records")
        
        if not user.has_perm('attendance.change_attendance') and self.overwrite:
            raise CommandError("Import user does not have permission to modify existing attendance records")

    def check_system_limits(self, total_records):
        max_import_size = SystemConfiguration.get_int_setting('MAX_EXCEL_IMPORT_SIZE', 1000)
        
        if total_records > max_import_size:
            raise CommandError(
                f"Import size ({total_records}) exceeds system limit ({max_import_size}). "
                f"Please split the file or contact administrator."
            )
        
        max_daily_imports = SystemConfiguration.get_int_setting('MAX_DAILY_IMPORTS_PER_USER', 5000)
        today = get_current_date()
        
        existing_imports_today = Attendance.objects.filter(
            created_at__date=today,
            is_manual_entry=True
        ).count()
        
        if existing_imports_today + total_records > max_daily_imports:
            raise CommandError(
                f"Daily import limit would be exceeded. "
                f"Current: {existing_imports_today}, Attempting: {total_records}, "
                f"Limit: {max_daily_imports}"
            )

    def cleanup_temp_files(self, file_paths):
        for file_path in file_paths:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    if self.verbose:
                        self.stdout.write(f"🗑️  Cleaned up temp file: {file_path}")
            except Exception as e:
                if self.verbose:
                    self.stdout.write(f"⚠️  Could not clean up {file_path}: {str(e)}")

    def generate_import_report(self, imported_count, error_count, skipped_count, user):
        try:
            report_data = {
                'import_timestamp': str(get_current_datetime()),
                'import_user': {
                    'id': user.id,
                    'name': user.get_full_name(),
                    'email': user.email
                },
                'statistics': {
                    'imported': imported_count,
                    'errors': error_count,
                    'skipped': skipped_count,
                    'total_processed': imported_count + error_count + skipped_count
                },
                'settings': {
                    'overwrite_existing': self.overwrite,
                    'skip_errors': self.skip_errors,
                    'batch_size': self.batch_size,
                    'dry_run': self.dry_run
                }
            }
            
            timestamp = get_current_datetime().strftime('%Y%m%d_%H%M%S')
            report_filename = f"import_report_{timestamp}.json"
            report_path = os.path.join('/tmp', report_filename)
            
            import json
            with open(report_path, 'w') as report_file:
                json.dump(report_data, report_file, indent=2, default=str)
            
            if self.verbose:
                self.stdout.write(f"📄 Import report saved to: {report_path}")
            
            return report_path
            
        except Exception as e:
            if self.verbose:
                self.stdout.write(f"⚠️  Could not generate import report: {str(e)}")
            return None

    def validate_business_rules(self, df):
        business_errors = []
        
        max_work_hours = SystemConfiguration.get_int_setting('MAX_DAILY_WORK_HOURS', 16)
        
        for index, row in df.iterrows():
            row_num = index + 2
            
            total_work_minutes = 0
            
            for i in range(1, 7):
                in_time = safe_time_conversion(row.get(f'In{i}'))
                out_time = safe_time_conversion(row.get(f'Out{i}'))
                
                if in_time and out_time:
                    work_minutes = (
                        datetime.combine(date.today(), out_time) -
                        datetime.combine(date.today(), in_time)
                    ).total_seconds() / 60
                    
                    total_work_minutes += work_minutes
            
            total_work_hours = total_work_minutes / 60
            
            if total_work_hours > max_work_hours:
                business_errors.append(
                    f"Row {row_num}: Total work hours ({total_work_hours:.1f}) "
                    f"exceeds maximum allowed ({max_work_hours})"
                )
        
        return business_errors

    def get_version(self):
        return "1.0.0"

    def add_help_text(self):
        return """
Excel Import Command Help:

REQUIRED COLUMNS:
- ID: Employee code/ID
- Date: Attendance date (YYYY-MM-DD format)
- In1, Out1: First check-in/out pair
- In2, Out2: Second check-in/out pair
- In3, Out3: Third check-in/out pair
- In4, Out4: Fourth check-in/out pair
- In5, Out5: Fifth check-in/out pair
- In6, Out6: Sixth check-in/out pair

EXAMPLES:
python manage.py import_excel attendance_data.xlsx --user-email admin@company.com
python manage.py import_excel data.xlsx --user-id 1 --overwrite --verbose
python manage.py import_excel data.xlsx --validate-only
python manage.py import_excel data.xlsx --dry-run --date-range 2024-01-01:2024-01-31
python manage.py import_excel data.xlsx --async --batch-size 25

NOTES:
- Time format: HH:MM (24-hour format)
- All 6 IN/OUT pairs are optional
- Existing records are skipped unless --overwrite is used
- Use --validate-only to check file without importing
- Use --dry-run to preview what would be imported
- Large files should use --async for background processing
"""


