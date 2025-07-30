from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.db import transaction
from accounts.models import CustomUser, Department, AuditLog
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
)
from .utils import (
    EmployeeDataManager,
    AttendanceCalculator,
    AuditHelper,
    CacheManager,
    get_current_date,
    get_current_datetime,
)
from datetime import timedelta
from decimal import Decimal


@receiver(post_save, sender=CustomUser)
def handle_employee_creation(sender, instance, created, **kwargs):
    if created and instance.is_active and instance.status == "ACTIVE":
        try:
            profile = EmployeeDataManager.get_employee_profile(instance)
            if profile and profile.is_active:
                create_initial_attendance_records(instance)
                create_initial_leave_balances(instance)
        except Exception as e:
            pass


@receiver(post_save, sender=EmployeeProfile)
def handle_employee_profile_update(sender, instance, created, **kwargs):
    if created:
        create_initial_attendance_records(instance.user)
        create_initial_leave_balances(instance.user)

    CacheManager.invalidate_employee_cache(instance.user.id)


@receiver(post_save, sender=AttendanceLog)
def process_attendance_log(sender, instance, created, **kwargs):
    if created and instance.processing_status == "PENDING":
        try:
            process_single_attendance_log(instance)
        except Exception as e:
            instance.mark_as_error(str(e))


@receiver(post_save, sender=Attendance)
def handle_attendance_update(sender, instance, created, **kwargs):
    if not created:
        update_monthly_summary_on_attendance_change(instance)
        invalidate_related_caches(instance)

        if hasattr(instance, "_attendance_changed_by"):
            log_attendance_modification(instance, instance._attendance_changed_by)


@receiver(pre_save, sender=Attendance)
def capture_attendance_changes(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = Attendance.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
            instance._old_work_time = old_instance.work_time
            instance._old_overtime = old_instance.overtime
        except Attendance.DoesNotExist:
            pass


@receiver(post_save, sender=LeaveRequest)
def handle_leave_request_update(sender, instance, created, **kwargs):
    if not created and instance.status == "APPROVED":
        create_leave_attendance_records(instance)
        update_leave_balance_on_approval(instance)

    if instance.status == "REJECTED" and hasattr(instance, "_was_approved"):
        restore_leave_balance(instance)


@receiver(pre_save, sender=LeaveRequest)
def capture_leave_request_changes(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = LeaveRequest.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
            if old_instance.status == "APPROVED":
                instance._was_approved = True
        except LeaveRequest.DoesNotExist:
            pass


@receiver(post_save, sender=Holiday)
def handle_holiday_creation(sender, instance, created, **kwargs):
    if created:
        update_attendance_records_for_holiday(instance)


@receiver(post_save, sender=EmployeeShift)
def handle_shift_assignment(sender, instance, created, **kwargs):
    if created or instance.is_active:
        update_future_attendance_with_new_shift(instance)
        CacheManager.invalidate_employee_cache(instance.employee.id)


@receiver(post_delete, sender=EmployeeShift)
def handle_shift_removal(sender, instance, **kwargs):
    CacheManager.invalidate_employee_cache(instance.employee.id)


@receiver(post_save, sender=AttendanceDevice)
def handle_device_update(sender, instance, created, **kwargs):
    if created:
        AuditHelper.log_device_sync(
            user=instance.created_by,
            device_id=instance.device_id,
            sync_result={"action": "device_created", "status": "success"},
        )


@receiver(post_save, sender=AttendanceCorrection)
def handle_attendance_correction(sender, instance, created, **kwargs):
    if not created and instance.status == "APPROVED":
        apply_attendance_correction(instance)
        invalidate_related_caches(instance.attendance)


def create_initial_attendance_records(employee):
    today = get_current_date()
    start_date = max(employee.hire_date or today, today - timedelta(days=30))

    current_date = start_date
    while current_date <= today:
        if current_date.weekday() < 5:
            attendance, created = Attendance.objects.get_or_create(
                employee=employee,
                date=current_date,
                defaults={
                    "status": "ABSENT",
                    "is_manual_entry": True,
                    "notes": "Auto-created record",
                },
            )
        current_date += timedelta(days=1)


def create_initial_leave_balances(employee):
    current_year = get_current_date().year
    active_leave_types = LeaveType.active.all()

    for leave_type in active_leave_types:
        if leave_type.applicable_after_probation_only:
            profile = EmployeeDataManager.get_employee_profile(employee)
            if profile and profile.employment_status == "PROBATION":
                continue

        if leave_type.gender_specific != "A":
            if employee.gender != leave_type.gender_specific:
                continue

        LeaveBalance.objects.get_or_create(
            employee=employee,
            leave_type=leave_type,
            year=current_year,
            defaults={
                "allocated_days": Decimal(str(leave_type.days_allowed_per_year)),
                "used_days": Decimal("0.00"),
                "carried_forward_days": Decimal("0.00"),
                "adjustment_days": Decimal("0.00"),
            },
        )


def process_single_attendance_log(log_instance):
    if not log_instance.employee:
        employee = EmployeeDataManager.get_employee_by_code(log_instance.employee_code)
        if not employee:
            log_instance.mark_as_error(
                f"Employee not found: {log_instance.employee_code}"
            )
            return
        log_instance.employee = employee
        log_instance.save(update_fields=["employee"])

    attendance_date = log_instance.timestamp.date()

    attendance, created = Attendance.objects.get_or_create(
        employee=log_instance.employee,
        date=attendance_date,
        defaults={
            "status": "ABSENT",
            "device": log_instance.device,
            "location": log_instance.device_location,
        },
    )

    daily_logs = AttendanceLog.objects.filter(
        employee=log_instance.employee,
        timestamp__date=attendance_date,
        processing_status="PENDING",
    ).order_by("timestamp")

    if daily_logs.count() >= 2:
        process_daily_attendance_logs(attendance, daily_logs)


def process_daily_attendance_logs(attendance, logs):
    from .utils import DeviceDataProcessor

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

    attendance.update_from_device_logs(log_data)

    for log in logs:
        log.mark_as_processed()


def update_monthly_summary_on_attendance_change(attendance):
    try:
        summary = MonthlyAttendanceSummary.objects.get(
            employee=attendance.employee,
            year=attendance.date.year,
            month=attendance.date.month,
        )

        updated_summary = MonthlyAttendanceSummary.generate_for_employee_month(
            attendance.employee, attendance.date.year, attendance.date.month
        )

    except MonthlyAttendanceSummary.DoesNotExist:
        MonthlyAttendanceSummary.generate_for_employee_month(
            attendance.employee, attendance.date.year, attendance.date.month
        )


def invalidate_related_caches(attendance):
    CacheManager.invalidate_employee_cache(attendance.employee.id)

    cache_keys = [
        f"monthly_summary_{attendance.employee.id}_{attendance.date.year}_{attendance.date.month}",
        f"employee_attendance_{attendance.employee.id}_{attendance.date}",
        f"department_attendance_{attendance.employee.department.id if attendance.employee.department else 'none'}_{attendance.date}",
    ]

    from django.core.cache import cache

    cache.delete_many(cache_keys)


def log_attendance_modification(attendance, changed_by):
    changes = {}

    if (
        hasattr(attendance, "_old_status")
        and attendance._old_status != attendance.status
    ):
        changes["status"] = {"old": attendance._old_status, "new": attendance.status}

    if (
        hasattr(attendance, "_old_work_time")
        and attendance._old_work_time != attendance.work_time
    ):
        changes["work_time"] = {
            "old": str(attendance._old_work_time),
            "new": str(attendance.work_time),
        }

    if changes:
        AuditHelper.log_attendance_change(
            user=changed_by,
            action="MODIFIED",
            employee=attendance.employee,
            attendance_date=attendance.date,
            changes=changes,
        )


def create_leave_attendance_records(leave_request):
    current_date = leave_request.start_date

    while current_date <= leave_request.end_date:
        if current_date.weekday() < 5:
            attendance, created = Attendance.objects.get_or_create(
                employee=leave_request.employee,
                date=current_date,
                defaults={
                    "status": "LEAVE",
                    "is_manual_entry": True,
                    "notes": f"On {leave_request.leave_type.name} - {leave_request.reason[:100]}",
                },
            )

            if not created and attendance.status == "ABSENT":
                attendance.status = "LEAVE"
                attendance.notes = (
                    f"On {leave_request.leave_type.name} - {leave_request.reason[:100]}"
                )
                attendance.save(update_fields=["status", "notes"])

        current_date += timedelta(days=1)


def update_leave_balance_on_approval(leave_request):
    try:
        leave_balance = LeaveBalance.objects.get(
            employee=leave_request.employee,
            leave_type=leave_request.leave_type,
            year=leave_request.start_date.year,
        )
        leave_balance.deduct_leave(leave_request.total_days)
    except LeaveBalance.DoesNotExist:
        pass


def restore_leave_balance(leave_request):
    try:
        leave_balance = LeaveBalance.objects.get(
            employee=leave_request.employee,
            leave_type=leave_request.leave_type,
            year=leave_request.start_date.year,
        )
        leave_balance.add_leave(leave_request.total_days)
    except LeaveBalance.DoesNotExist:
        pass


def update_attendance_records_for_holiday(holiday):
    if holiday.date >= get_current_date():
        return

    affected_employees = CustomUser.active.all()

    if holiday.applicable_departments.exists():
        affected_employees = affected_employees.filter(
            department__in=holiday.applicable_departments.all()
        )

    for employee in affected_employees:
        try:
            attendance = Attendance.objects.get(employee=employee, date=holiday.date)

            if attendance.status == "ABSENT":
                attendance.status = "HOLIDAY"
                attendance.is_holiday = True
                attendance.notes = f"Holiday: {holiday.name}"
                attendance.save(update_fields=["status", "is_holiday", "notes"])

        except Attendance.DoesNotExist:
            Attendance.objects.create(
                employee=employee,
                date=holiday.date,
                status="HOLIDAY",
                is_holiday=True,
                is_manual_entry=True,
                notes=f"Holiday: {holiday.name}",
            )


def update_future_attendance_with_new_shift(employee_shift):
    if not employee_shift.is_active:
        return

    start_date = max(employee_shift.effective_from, get_current_date())
    end_date = employee_shift.effective_to or (get_current_date() + timedelta(days=365))

    future_attendance = Attendance.objects.filter(
        employee=employee_shift.employee,
        date__range=[start_date, end_date],
        shift__isnull=True,
    )

    future_attendance.update(shift=employee_shift.shift)


def apply_attendance_correction(correction):
    attendance = correction.attendance

    for field, value in correction.corrected_data.items():
        if hasattr(attendance, field):
            if field.startswith("check_"):
                from .utils import safe_time_conversion

                setattr(attendance, field, safe_time_conversion(value))
            else:
                setattr(attendance, field, value)

    attendance._attendance_changed_by = correction.approved_by
    attendance.save()


@receiver(post_save, sender=LeaveType)
def handle_leave_type_creation(sender, instance, created, **kwargs):
    if created:
        create_leave_balances_for_all_employees(instance)


@receiver(post_save, sender=Contract)
def handle_contract_update(sender, instance, created, **kwargs):
    if instance.status == "ACTIVE":
        ensure_employee_attendance_records(instance.employee)
        update_employee_shift_from_contract(instance)


@receiver(pre_delete, sender=CustomUser)
def handle_employee_deletion(sender, instance, **kwargs):
    if instance.attendance_records.exists():
        raise ValueError("Cannot delete employee with existing attendance records")


@receiver(post_delete, sender=Attendance)
def handle_attendance_deletion(sender, instance, **kwargs):
    update_monthly_summary_on_attendance_change(instance)
    invalidate_related_caches(instance)

    AuditHelper.log_attendance_change(
        user=getattr(instance, "_deleted_by", None),
        action="DELETED",
        employee=instance.employee,
        attendance_date=instance.date,
        changes={"deleted_record_id": str(instance.id)},
    )


@receiver(post_save, sender=Department)
def handle_department_update(sender, instance, created, **kwargs):
    if not created:
        invalidate_department_caches(instance)


@receiver(post_save, sender=MonthlyAttendanceSummary)
def handle_monthly_summary_update(sender, instance, created, **kwargs):
    CacheManager.cache_monthly_summary(
        instance.employee.id,
        instance.year,
        instance.month,
        {
            "total_work_time": instance.total_work_time,
            "total_overtime": instance.total_overtime,
            "attendance_percentage": instance.attendance_percentage,
            "punctuality_score": instance.punctuality_score,
        },
    )


@receiver(pre_save, sender=AttendanceDevice)
def validate_device_before_save(sender, instance, **kwargs):
    if instance.status == "ACTIVE":
        is_connected, message = instance.test_connection()
        if not is_connected:
            instance.status = "ERROR"


@receiver(post_delete, sender=AttendanceDevice)
def handle_device_deletion(sender, instance, **kwargs):
    AttendanceLog.objects.filter(device=instance).update(
        processing_status="IGNORED", error_message="Device deleted"
    )


def create_leave_balances_for_all_employees(leave_type):
    current_year = get_current_date().year
    active_employees = CustomUser.active.all()

    for employee in active_employees:
        if leave_type.applicable_after_probation_only:
            profile = EmployeeDataManager.get_employee_profile(employee)
            if profile and profile.employment_status == "PROBATION":
                continue

        if leave_type.gender_specific != "A":
            if employee.gender != leave_type.gender_specific:
                continue

        LeaveBalance.objects.get_or_create(
            employee=employee,
            leave_type=leave_type,
            year=current_year,
            defaults={
                "allocated_days": Decimal(str(leave_type.days_allowed_per_year)),
                "used_days": Decimal("0.00"),
                "carried_forward_days": Decimal("0.00"),
                "adjustment_days": Decimal("0.00"),
            },
        )


def ensure_employee_attendance_records(employee):
    today = get_current_date()
    start_date = max(employee.hire_date or today, today - timedelta(days=7))

    current_date = start_date
    while current_date <= today:
        if current_date.weekday() < 5:
            Attendance.objects.get_or_create(
                employee=employee,
                date=current_date,
                defaults={
                    "status": "ABSENT",
                    "is_manual_entry": True,
                    "notes": "Auto-created from contract activation",
                },
            )
        current_date += timedelta(days=1)


def update_employee_shift_from_contract(contract):
    if contract.working_hours and contract.working_hours != Decimal("8.00"):
        try:
            shift = Shift.objects.get(
                working_hours=contract.working_hours, is_active=True
            )

            EmployeeShift.objects.get_or_create(
                employee=contract.employee,
                effective_from=contract.start_date,
                defaults={
                    "shift": shift,
                    "effective_to": contract.end_date,
                    "assigned_by": contract.created_by,
                    "notes": f"Auto-assigned from contract {contract.contract_number}",
                },
            )
        except Shift.DoesNotExist:
            pass


def invalidate_department_caches(department):
    department_employees = department.employees.all()
    for employee in department_employees:
        CacheManager.invalidate_employee_cache(employee.id)


@receiver(post_save, sender=Shift)
def handle_shift_update(sender, instance, created, **kwargs):
    if not created:
        affected_assignments = EmployeeShift.objects.filter(
            shift=instance, is_active=True
        )

        for assignment in affected_assignments:
            CacheManager.invalidate_employee_cache(assignment.employee.id)


def auto_create_monthly_summaries():
    from django.db.models import Q

    current_date = get_current_date()
    last_month = current_date.replace(day=1) - timedelta(days=1)

    employees_needing_summary = CustomUser.active.exclude(
        monthly_summaries__year=last_month.year,
        monthly_summaries__month=last_month.month,
    )

    for employee in employees_needing_summary:
        if employee.hire_date and employee.hire_date <= last_month:
            MonthlyAttendanceSummary.generate_for_employee_month(
                employee, last_month.year, last_month.month
            )


def cleanup_processed_logs():
    from accounts.models import SystemConfiguration

    retention_days = SystemConfiguration.get_int_setting("LOG_RETENTION_DAYS", 30)
    cutoff_date = get_current_datetime() - timedelta(days=retention_days)

    old_logs = AttendanceLog.objects.filter(
        processed_at__lt=cutoff_date, processing_status="PROCESSED"
    )

    deleted_count = old_logs.count()
    old_logs.delete()

    return deleted_count


def sync_employee_data_to_devices():
    active_devices = AttendanceDevice.active.all()
    active_employees = CustomUser.active.all()

    for device in active_devices:
        try:
            for employee in active_employees:
                device.sync_employees()
        except Exception as e:
            AuditHelper.log_device_sync(
                user=None,
                device_id=device.device_id,
                sync_result={
                    "action": "employee_sync",
                    "status": "failed",
                    "error": str(e),
                },
            )


def process_incomplete_attendance():
    today = get_current_date()
    yesterday = today - timedelta(days=1)

    incomplete_records = Attendance.objects.filter(date=yesterday, status="INCOMPLETE")

    for record in incomplete_records:
        if record.first_in_time and not record.last_out_time:
            schedule = EmployeeDataManager.get_employee_work_schedule(record.employee)
            expected_out_time = (
                timezone.datetime.combine(yesterday, record.first_in_time)
                + schedule["standard_work_time"]
            ).time()

            record.last_out_time = expected_out_time
            record.status = "PRESENT"
            record.notes = "Auto-completed: Missing check-out"
            record.save()


def update_leave_balances_for_new_year():
    current_year = get_current_date().year
    previous_year = current_year - 1

    active_employees = CustomUser.active.all()
    active_leave_types = LeaveType.active.all()

    for employee in active_employees:
        for leave_type in active_leave_types:
            try:
                previous_balance = LeaveBalance.objects.get(
                    employee=employee, leave_type=leave_type, year=previous_year
                )

                carried_forward = Decimal("0.00")
                if leave_type.carry_forward_allowed:
                    available = previous_balance.available_days
                    max_carry = Decimal(str(leave_type.carry_forward_max_days or 0))
                    carried_forward = min(available, max_carry)

                LeaveBalance.objects.get_or_create(
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

            except LeaveBalance.DoesNotExist:
                LeaveBalance.objects.get_or_create(
                    employee=employee,
                    leave_type=leave_type,
                    year=current_year,
                    defaults={
                        "allocated_days": Decimal(
                            str(leave_type.days_allowed_per_year)
                        ),
                        "used_days": Decimal("0.00"),
                        "carried_forward_days": Decimal("0.00"),
                        "adjustment_days": Decimal("0.00"),
                    },
                )


def generate_daily_attendance_records():
    today = get_current_date()
    active_employees = CustomUser.active.all()

    for employee in active_employees:
        if today.weekday() < 5:
            is_holiday = Holiday.active.filter(date=today).exists()

            status = "HOLIDAY" if is_holiday else "ABSENT"

            Attendance.objects.get_or_create(
                employee=employee,
                date=today,
                defaults={
                    "status": status,
                    "is_holiday": is_holiday,
                    "is_weekend": False,
                    "is_manual_entry": True,
                    "notes": "Auto-created daily record",
                },
            )
        else:
            Attendance.objects.get_or_create(
                employee=employee,
                date=today,
                defaults={
                    "status": "ABSENT",
                    "is_holiday": False,
                    "is_weekend": True,
                    "is_manual_entry": True,
                    "notes": "Weekend - Auto-created",
                },
            )
