from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from accounts.models import CustomUser, Department, ActiveManager, SystemConfiguration
from employees.models import EmployeeProfile, Contract
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date, time, timedelta
from django.utils import timezone

import uuid
from .utils import (
    TimeCalculator,
    EmployeeDataManager,
    AttendanceCalculator,
    DeviceDataProcessor,
    ValidationHelper,
    AuditHelper,
    CacheManager,
    get_current_date,
    get_current_datetime,
    generate_unique_id,
    safe_decimal_conversion,
    safe_time_conversion,
    safe_date_conversion,
)

get_current_datetime = timezone.now

class AttendanceDevice(models.Model):
    DEVICE_TYPES = [
        ("REALAND_A_F011", "REALAND A-F011"),
        ("ZKTECO", "ZKTeco Device"),
        ("MANUAL", "Manual Entry"),
        ("MOBILE", "Mobile App"),
        ("WEB", "Web Portal"),
    ]

    DEVICE_STATUS = [
        ("ACTIVE", "Active"),
        ("INACTIVE", "Inactive"),
        ("MAINTENANCE", "Under Maintenance"),
        ("ERROR", "Error"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device_id = models.CharField(max_length=50, unique=True)
    device_name = models.CharField(max_length=100)
    device_type = models.CharField(
        max_length=20, choices=DEVICE_TYPES, default="REALAND_A_F011"
    )
    ip_address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(default=4370)
    location = models.CharField(max_length=255)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True
    )
    status = models.CharField(max_length=20, choices=DEVICE_STATUS, default="ACTIVE")
    last_sync_time = models.DateTimeField(null=True, blank=True)
    sync_interval_minutes = models.PositiveIntegerField(default=5)
    max_users = models.PositiveIntegerField(default=1000)
    max_transactions = models.PositiveIntegerField(default=100000)
    firmware_version = models.CharField(max_length=50, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_devices",
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "attendance_devices"
        ordering = ["device_name"]
        indexes = [
            models.Index(fields=["device_id"]),
            models.Index(fields=["device_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.device_name} ({self.device_id})"

    def clean(self):
        if self.port < 1 or self.port > 65535:
            raise ValidationError("Port must be between 1 and 65535")

        if self.sync_interval_minutes < 1:
            raise ValidationError("Sync interval must be at least 1 minute")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def test_connection(self):
        from .utils import DeviceManager

        return DeviceManager.test_device_connection(self.ip_address, self.port)

    def sync_employees(self):
        from .services import DeviceService

        return DeviceService.sync_employees_to_device(self)

    def get_attendance_logs(self, start_date, end_date):
        from .services import DeviceService

        return DeviceService.get_logs_from_device(self, start_date, end_date)


class Shift(models.Model):
    SHIFT_TYPES = [
        ("REGULAR", "Regular Shift"),
        ("MORNING", "Morning Shift"),
        ("AFTERNOON", "Afternoon Shift"),
        ("NIGHT", "Night Shift"),
        ("FLEXIBLE", "Flexible Hours"),
        ("PART_TIME", "Part Time"),
    ]

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    shift_type = models.CharField(max_length=20, choices=SHIFT_TYPES, default="REGULAR")
    code = models.CharField(max_length=10, unique=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_duration_minutes = models.PositiveIntegerField(default=60)
    grace_period_minutes = models.PositiveIntegerField(default=15)
    overtime_threshold_minutes = models.PositiveIntegerField(default=0)
    working_hours = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("8.00")
    )
    is_night_shift = models.BooleanField(default=False)
    weekend_applicable = models.BooleanField(default=False)
    holiday_applicable = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_shifts",
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "attendance_shifts"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["shift_type"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.start_time} - {self.end_time})"

    def clean(self):
        if not self.is_night_shift and self.end_time <= self.start_time:
            raise ValidationError(
                "End time must be after start time for regular shifts"
            )

        if self.break_duration_minutes >= (self.working_hours * 60):
            raise ValidationError("Break duration cannot exceed working hours")

        if self.grace_period_minutes > 60:
            raise ValidationError("Grace period cannot exceed 60 minutes")

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_shift_code()
        self.full_clean()
        super().save(*args, **kwargs)

    def generate_shift_code(self):
        base_code = self.name[:3].upper()
        existing_codes = Shift.objects.filter(code__startswith=base_code).values_list(
            "code", flat=True
        )

        counter = 1
        while f"{base_code}{counter:02d}" in existing_codes:
            counter += 1

        return f"{base_code}{counter:02d}"

    @property
    def total_shift_duration(self):
        if self.is_night_shift:
            start_datetime = datetime.combine(date.today(), self.start_time)
            end_datetime = datetime.combine(
                date.today() + timedelta(days=1), self.end_time
            )
        else:
            start_datetime = datetime.combine(date.today(), self.start_time)
            end_datetime = datetime.combine(date.today(), self.end_time)

        return end_datetime - start_datetime

    @property
    def effective_working_duration(self):
        return self.total_shift_duration - timedelta(
            minutes=self.break_duration_minutes
        )


class EmployeeShift(models.Model):
    id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="employee_shifts"
    )
    shift = models.ForeignKey(
        Shift, on_delete=models.CASCADE, related_name="assigned_employees"
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_temporary = models.BooleanField(default=False)
    assigned_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_shifts",
    )
    notes = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "employee_shifts"
        ordering = ["-effective_from"]
        indexes = [
            models.Index(fields=["employee", "effective_from"]),
            models.Index(fields=["shift", "is_active"]),
            models.Index(fields=["effective_from", "effective_to"]),
        ]
        unique_together = ["employee", "effective_from"]

    def __str__(self):
        return f"{self.employee.get_full_name()} - {self.shift.name}"

    def clean(self):
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValidationError("Effective to date must be after effective from date")

        if self.effective_from > get_current_date():
            if not self.is_temporary:
                raise ValidationError(
                    "Future shift assignments must be marked as temporary"
                )

        overlapping_shifts = EmployeeShift.objects.filter(
            employee=self.employee,
            is_active=True,
            effective_from__lte=self.effective_to or date(2099, 12, 31),
            effective_to__gte=self.effective_from,
        ).exclude(pk=self.pk)

        if overlapping_shifts.exists():
            raise ValidationError(
                "Employee already has a shift assigned for this period"
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_current(self):
        today = get_current_date()
        return self.effective_from <= today and (
            self.effective_to is None or self.effective_to >= today
        )

    @property
    def duration_days(self):
        if self.effective_to:
            return (self.effective_to - self.effective_from).days + 1
        return None


class AttendanceLog(models.Model):
    LOG_TYPES = [
        ("CHECK_IN", "Check In"),
        ("CHECK_OUT", "Check Out"),
        ("BREAK_START", "Break Start"),
        ("BREAK_END", "Break End"),
        ("OVERTIME_IN", "Overtime In"),
        ("OVERTIME_OUT", "Overtime Out"),
        ("MANUAL_ENTRY", "Manual Entry"),
    ]

    PROCESSING_STATUS = [
        ("PENDING", "Pending"),
        ("PROCESSED", "Processed"),
        ("ERROR", "Error"),
        ("DUPLICATE", "Duplicate"),
        ("IGNORED", "Ignored"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="attendance_logs",
        null=True,
        blank=True,
    )
    employee_code = models.CharField(max_length=20)
    device = models.ForeignKey(
        AttendanceDevice, on_delete=models.CASCADE, related_name="logs"
    )
    timestamp = models.DateTimeField()
    log_type = models.CharField(max_length=20, choices=LOG_TYPES)
    device_location = models.CharField(max_length=255, blank=True, null=True)
    raw_data = models.JSONField(default=dict)
    processing_status = models.CharField(
        max_length=20, choices=PROCESSING_STATUS, default="PENDING"
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_logs"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["employee_code", "timestamp"]),
            models.Index(fields=["employee", "timestamp"]),
            models.Index(fields=["device", "timestamp"]),
            models.Index(fields=["processing_status"]),
            models.Index(fields=["log_type"]),
            models.Index(fields=["timestamp"]),
        ]

    def __str__(self):
        employee_name = (
            self.employee.get_full_name() if self.employee else self.employee_code
        )
        return f"{employee_name} - {self.log_type} - {self.timestamp}"

    def save(self, *args, **kwargs):
        if not self.employee and self.employee_code:
            self.employee = EmployeeDataManager.get_employee_by_code(self.employee_code)
        super().save(*args, **kwargs)

    def mark_as_processed(self):
        self.processing_status = "PROCESSED"
        self.processed_at = get_current_datetime()
        self.save(update_fields=["processing_status", "processed_at"])

    def mark_as_error(self, error_message):
        self.processing_status = "ERROR"
        self.error_message = error_message
        self.processed_at = get_current_datetime()
        self.save(update_fields=["processing_status", "error_message", "processed_at"])


class Holiday(models.Model):
    HOLIDAY_TYPES = [
        ("NATIONAL", "National Holiday"),
        ("RELIGIOUS", "Religious Holiday"),
        ("COMPANY", "Company Holiday"),
        ("OPTIONAL", "Optional Holiday"),
        ("LOCAL", "Local Holiday"),
    ]

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    date = models.DateField()
    holiday_type = models.CharField(
        max_length=20, choices=HOLIDAY_TYPES, default="NATIONAL"
    )
    description = models.TextField(blank=True, null=True)
    is_optional = models.BooleanField(default=False)
    applicable_departments = models.ManyToManyField(
        Department, blank=True, related_name="holidays"
    )
    applicable_locations = models.JSONField(default=list, blank=True)
    is_paid = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_holidays",
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "attendance_holidays"
        ordering = ["date"]
        indexes = [
            models.Index(fields=["date"]),
            models.Index(fields=["holiday_type"]),
            models.Index(fields=["is_active"]),
        ]
        unique_together = ["name", "date"]

    def __str__(self):
        return f"{self.name} - {self.date}"

    def clean(self):
        if self.date < get_current_date() - timedelta(days=365):
            raise ValidationError("Holiday date cannot be more than 1 year in the past")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_upcoming(self):
        return self.date > get_current_date()

    @property
    def days_until(self):
        if self.is_upcoming:
            return (self.date - get_current_date()).days
        return 0


class LeaveType(models.Model):
    LEAVE_CATEGORIES = [
        ("ANNUAL", "Annual Leave"),
        ("SICK", "Sick Leave"),
        ("MATERNITY", "Maternity Leave"),
        ("PATERNITY", "Paternity Leave"),
        ("EMERGENCY", "Emergency Leave"),
        ("STUDY", "Study Leave"),
        ("UNPAID", "Unpaid Leave"),
        ("COMPENSATORY", "Compensatory Leave"),
    ]

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=10, unique=True)
    category = models.CharField(max_length=20, choices=LEAVE_CATEGORIES)
    description = models.TextField(blank=True, null=True)
    days_allowed_per_year = models.PositiveIntegerField()
    max_consecutive_days = models.PositiveIntegerField(null=True, blank=True)
    min_notice_days = models.PositiveIntegerField(default=1)
    requires_approval = models.BooleanField(default=True)
    requires_medical_certificate = models.BooleanField(default=False)
    is_paid = models.BooleanField(default=True)
    carry_forward_allowed = models.BooleanField(default=False)
    carry_forward_max_days = models.PositiveIntegerField(null=True, blank=True)
    applicable_after_probation_only = models.BooleanField(default=False)
    gender_specific = models.CharField(
        max_length=1,
        choices=[("M", "Male"), ("F", "Female"), ("A", "All")],
        default="A",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_leave_types",
    )

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        db_table = "attendance_leave_types"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["category"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def clean(self):
        if self.carry_forward_allowed and not self.carry_forward_max_days:
            raise ValidationError(
                "Carry forward max days is required when carry forward is allowed"
            )

        if (
            self.carry_forward_max_days
            and self.carry_forward_max_days > self.days_allowed_per_year
        ):
            raise ValidationError(
                "Carry forward max days cannot exceed annual allowance"
            )

        if (
            self.max_consecutive_days
            and self.max_consecutive_days > self.days_allowed_per_year
        ):
            raise ValidationError("Max consecutive days cannot exceed annual allowance")

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_leave_code()
        self.full_clean()
        super().save(*args, **kwargs)

    def generate_leave_code(self):
        base_code = self.name[:2].upper()
        existing_codes = LeaveType.objects.filter(
            code__startswith=base_code
        ).values_list("code", flat=True)

        counter = 1
        while f"{base_code}{counter:02d}" in existing_codes:
            counter += 1

        return f"{base_code}{counter:02d}"


class LeaveBalance(models.Model):
    id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="leave_balances"
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.CASCADE, related_name="employee_balances"
    )
    year = models.PositiveIntegerField()
    allocated_days = models.DecimalField(max_digits=5, decimal_places=2)
    used_days = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )
    carried_forward_days = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )
    adjustment_days = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )
    last_updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_leave_balances",
    )

    class Meta:
        db_table = "attendance_leave_balances"
        ordering = ["-year", "leave_type__name"]
        indexes = [
            models.Index(fields=["employee", "year"]),
            models.Index(fields=["leave_type", "year"]),
        ]
        unique_together = ["employee", "leave_type", "year"]

    def __str__(self):
        return f"{self.employee.get_full_name()} - {self.leave_type.name} - {self.year}"

    @property
    def available_days(self):
        total_available = (
            self.allocated_days + self.carried_forward_days + self.adjustment_days
        )
        return max(total_available - self.used_days, Decimal("0.00"))

    @property
    def utilization_percentage(self):
        if self.allocated_days > 0:
            return (self.used_days / self.allocated_days * 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        return Decimal("0.00")

    def can_apply_leave(self, days_requested):
        return self.available_days >= Decimal(str(days_requested))

    def deduct_leave(self, days_used):
        self.used_days += Decimal(str(days_used))
        self.save(update_fields=["used_days", "last_updated"])

    def add_leave(self, days_added):
        self.used_days = max(self.used_days - Decimal(str(days_added)), Decimal("0.00"))
        self.save(update_fields=["used_days", "last_updated"])


class LeaveRequest(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
        ("CANCELLED", "Cancelled"),
        ("WITHDRAWN", "Withdrawn"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="leave_requests"
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.CASCADE, related_name="requests"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    total_days = models.DecimalField(max_digits=5, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    applied_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_leave_requests",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, null=True)
    medical_certificate = models.FileField(
        upload_to="leave_certificates/%Y/%m/", null=True, blank=True
    )
    emergency_contact_during_leave = models.CharField(
        max_length=100, blank=True, null=True
    )
    handover_notes = models.TextField(blank=True, null=True)
    is_half_day = models.BooleanField(default=False)
    half_day_period = models.CharField(
        max_length=10,
        choices=[("MORNING", "Morning"), ("AFTERNOON", "Afternoon")],
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "attendance_leave_requests"
        ordering = ["-applied_at"]
        indexes = [
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["leave_type", "status"]),
            models.Index(fields=["start_date", "end_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["applied_at"]),
        ]

    def __str__(self):
        return f"{self.employee.get_full_name()} - {self.leave_type.name} - {self.start_date} to {self.end_date}"

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError("End date cannot be before start date")

        if self.start_date < get_current_date():
            raise ValidationError("Leave cannot be applied for past dates")

        if self.is_half_day and self.start_date != self.end_date:
            raise ValidationError("Half day leave must be for a single date")

        if self.is_half_day and not self.half_day_period:
            raise ValidationError("Half day period is required for half day leave")

        if (
            self.leave_type.requires_medical_certificate
            and not self.medical_certificate
        ):
            if self.total_days >= Decimal("3.00"):
                raise ValidationError(
                    "Medical certificate is required for this leave type"
                )

        notice_days = (self.start_date - get_current_date()).days
        if notice_days < self.leave_type.min_notice_days:
            raise ValidationError(
                f"Minimum {self.leave_type.min_notice_days} days notice required"
            )

        if (
            self.leave_type.max_consecutive_days
            and self.total_days > self.leave_type.max_consecutive_days
        ):
            raise ValidationError(
                f"Maximum {self.leave_type.max_consecutive_days} consecutive days allowed"
            )

        overlapping_requests = LeaveRequest.objects.filter(
            employee=self.employee,
            status__in=["PENDING", "APPROVED"],
            start_date__lte=self.end_date,
            end_date__gte=self.start_date,
        ).exclude(pk=self.pk)

        if overlapping_requests.exists():
            raise ValidationError("Leave request overlaps with existing request")

    def save(self, *args, **kwargs):
        if not self.total_days:
            self.calculate_total_days()
        self.full_clean()
        super().save(*args, **kwargs)

    def calculate_total_days(self):
        if self.is_half_day:
            self.total_days = Decimal("0.5")
        else:
            business_days = 0
            current_date = self.start_date
            while current_date <= self.end_date:
                if current_date.weekday() < 5:
                    if not Holiday.active.filter(date=current_date).exists():
                        business_days += 1
                current_date += timedelta(days=1)
            self.total_days = Decimal(str(business_days))

    def approve(self, approved_by_user):
        leave_balance = LeaveBalance.objects.get(
            employee=self.employee,
            leave_type=self.leave_type,
            year=self.start_date.year,
        )

        if not leave_balance.can_apply_leave(self.total_days):
            raise ValidationError("Insufficient leave balance")

        self.status = "APPROVED"
        self.approved_by = approved_by_user
        self.approved_at = get_current_datetime()
        self.save(update_fields=["status", "approved_by", "approved_at"])

        leave_balance.deduct_leave(self.total_days)

        AuditHelper.log_attendance_change(
            user=approved_by_user,
            action="LEAVE_APPROVED",
            employee=self.employee,
            attendance_date=self.start_date,
            changes={
                "leave_request_id": str(self.id),
                "total_days": float(self.total_days),
            },
        )

    def reject(self, rejected_by_user, reason):
        self.status = "REJECTED"
        self.approved_by = rejected_by_user
        self.approved_at = get_current_datetime()
        self.rejection_reason = reason
        self.save(
            update_fields=["status", "approved_by", "approved_at", "rejection_reason"]
        )

        AuditHelper.log_attendance_change(
            user=rejected_by_user,
            action="LEAVE_REJECTED",
            employee=self.employee,
            attendance_date=self.start_date,
            changes={"leave_request_id": str(self.id), "reason": reason},
        )

    @property
    def is_pending(self):
        return self.status == "PENDING"

    @property
    def can_be_cancelled(self):
        return (
            self.status in ["PENDING", "APPROVED"]
            and self.start_date > get_current_date()
        )


class Attendance(models.Model):
    STATUS_CHOICES = [
        ("PRESENT", "Present"),
        ("ABSENT", "Absent"),
        ("LATE", "Late"),
        ("HALF_DAY", "Half Day"),
        ("LEAVE", "On Leave"),
        ("HOLIDAY", "Holiday"),
        ("INCOMPLETE", "Incomplete"),
        ("EARLY_DEPARTURE", "Early Departure"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="attendance_records"
    )
    date = models.DateField()
    shift = models.ForeignKey(
        Shift,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_records",
    )

    check_in_1 = models.TimeField(null=True, blank=True)
    check_out_1 = models.TimeField(null=True, blank=True)
    check_in_2 = models.TimeField(null=True, blank=True)
    check_out_2 = models.TimeField(null=True, blank=True)
    check_in_3 = models.TimeField(null=True, blank=True)
    check_out_3 = models.TimeField(null=True, blank=True)
    check_in_4 = models.TimeField(null=True, blank=True)
    check_out_4 = models.TimeField(null=True, blank=True)
    check_in_5 = models.TimeField(null=True, blank=True)
    check_out_5 = models.TimeField(null=True, blank=True)
    check_in_6 = models.TimeField(null=True, blank=True)
    check_out_6 = models.TimeField(null=True, blank=True)

    total_time = models.DurationField(default=timedelta(0))
    break_time = models.DurationField(default=timedelta(0))
    work_time = models.DurationField(default=timedelta(0))
    overtime = models.DurationField(default=timedelta(0))
    undertime = models.DurationField(default=timedelta(0))

    first_in_time = models.TimeField(null=True, blank=True)
    last_out_time = models.TimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ABSENT")
    late_minutes = models.PositiveIntegerField(default=0)
    early_departure_minutes = models.PositiveIntegerField(default=0)

    device = models.ForeignKey(
        AttendanceDevice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_records",
    )
    location = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    is_manual_entry = models.BooleanField(default=False)
    is_holiday = models.BooleanField(default=False)
    is_weekend = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_attendance_records",
    )

    class Meta:
        db_table = "attendance_records"
        ordering = ["-date", "employee__employee_code"]
        indexes = [
            models.Index(fields=["employee", "date"]),
            models.Index(fields=["date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["is_manual_entry"]),
        ]
        unique_together = ["employee", "date"]

    def __str__(self):
        return f"{self.employee.get_full_name()} - {self.date} - {self.status}"
    
        def clean(self):
        if self.date > get_current_date():
            raise ValidationError('Attendance date cannot be in the future')
        
        time_pairs = self.get_time_pairs()
        is_valid, errors = ValidationHelper.validate_attendance_consistency(time_pairs)
        if not is_valid:
            raise ValidationError('; '.join(errors))
        
        is_employee_valid, message = EmployeeDataManager.validate_employee_for_attendance(self.employee)
        if not is_employee_valid:
            raise ValidationError(message)
    
    def save(self, *args, **kwargs):
        if not self.shift:
            self.shift = self.get_employee_shift()
        
        self.is_weekend = self.date.weekday() >= 5
        self.is_holiday = Holiday.active.filter(date=self.date).exists()
        
        self.calculate_attendance_metrics()
        self.full_clean()
        super().save(*args, **kwargs)
        
        CacheManager.invalidate_employee_cache(self.employee.id)
    
    def get_time_pairs(self):
        return [
            (self.check_in_1, self.check_out_1),
            (self.check_in_2, self.check_out_2),
            (self.check_in_3, self.check_out_3),
            (self.check_in_4, self.check_out_4),
            (self.check_in_5, self.check_out_5),
            (self.check_in_6, self.check_out_6),
        ]
    
    def set_time_pairs(self, time_pairs):
        for i, (in_time, out_time) in enumerate(time_pairs[:6]):
            setattr(self, f'check_in_{i+1}', in_time)
            setattr(self, f'check_out_{i+1}', out_time)
    
    def get_employee_shift(self):
        employee_shift = EmployeeShift.objects.filter(
            employee=self.employee,
            effective_from__lte=self.date,
            effective_to__gte=self.date,
            is_active=True
        ).first()
        
        if employee_shift:
            return employee_shift.shift
        
        return EmployeeShift.objects.filter(
            employee=self.employee,
            effective_from__lte=self.date,
            effective_to__isnull=True,
            is_active=True
        ).first()
    
    def calculate_attendance_metrics(self):
        time_pairs = self.get_time_pairs()
        metrics = AttendanceCalculator.calculate_attendance_metrics(
            time_pairs, self.employee, self.date
        )
        
        self.total_time = metrics['total_time']
        self.break_time = metrics['break_time']
        self.work_time = metrics['work_time']
        self.overtime = metrics['overtime']
        self.undertime = metrics['undertime']
        self.first_in_time = metrics['first_in_time']
        self.last_out_time = metrics['last_out_time']
        self.status = metrics['status']
        self.late_minutes = metrics['late_minutes']
        self.early_departure_minutes = metrics['early_departure_minutes']
    
    def update_from_device_logs(self, logs):
        time_pairs = DeviceDataProcessor.create_attendance_pairs(logs)
        self.set_time_pairs(time_pairs)
        self.calculate_attendance_metrics()
        self.is_manual_entry = False
        self.device = logs[0].get('device') if logs else None
        self.save()
    
    @property
    def department(self):
        return self.employee.department
    
    @property
    def employee_profile(self):
        return EmployeeDataManager.get_employee_profile(self.employee)
    
    @property
    def is_complete_day(self):
        schedule = EmployeeDataManager.get_employee_work_schedule(self.employee)
        return AttendanceCalculator.is_complete_working_day(
            self.work_time, schedule['standard_work_time']
        )
    
    @property
    def attendance_percentage(self):
        schedule = EmployeeDataManager.get_employee_work_schedule(self.employee)
        return AttendanceCalculator.calculate_attendance_percentage(
            self.work_time, schedule['standard_work_time']
        )
    
    @property
    def formatted_total_time(self):
        return TimeCalculator.format_duration_to_excel_time(self.total_time)
    
    @property
    def formatted_work_time(self):
        return TimeCalculator.format_duration_to_excel_time(self.work_time)
    
    @property
    def formatted_break_time(self):
        return TimeCalculator.format_duration_to_excel_time(self.break_time)
    
    @property
    def formatted_overtime(self):
        return TimeCalculator.format_duration_to_excel_time(self.overtime)

class MonthlyAttendanceSummary(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='monthly_summaries')
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    
    total_work_time = models.DurationField(default=timedelta(0))
    total_break_time = models.DurationField(default=timedelta(0))
    total_overtime = models.DurationField(default=timedelta(0))
    total_undertime = models.DurationField(default=timedelta(0))
    
    working_days = models.PositiveIntegerField(default=0)
    attended_days = models.PositiveIntegerField(default=0)
    half_days = models.PositiveIntegerField(default=0)
    late_days = models.PositiveIntegerField(default=0)
    early_days = models.PositiveIntegerField(default=0)
    absent_days = models.PositiveIntegerField(default=0)
    leave_days = models.PositiveIntegerField(default=0)
    holiday_days = models.PositiveIntegerField(default=0)
    
    attendance_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    punctuality_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('100.00'))
    average_work_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    
    earliest_in_time = models.TimeField(null=True, blank=True)
    latest_out_time = models.TimeField(null=True, blank=True)
    
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    generated_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='generated_summaries')
    
    class Meta:
        db_table = 'attendance_monthly_summaries'
        ordering = ['-year', '-month', 'employee__employee_code']
        indexes = [
            models.Index(fields=['employee', 'year', 'month']),
            models.Index(fields=['year', 'month']),
        ]
        unique_together = ['employee', 'year', 'month']
    
    def __str__(self):
        return f"{self.employee.get_full_name()} - {self.year}/{self.month:02d}"
    
    def clean(self):
        if self.month < 1 or self.month > 12:
            raise ValidationError('Month must be between 1 and 12')
        
        if self.year < 2020 or self.year > get_current_date().year + 1:
            raise ValidationError('Invalid year')
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
    
    @classmethod
    def generate_for_employee_month(cls, employee, year, month, generated_by=None):
        from .utils import MonthlyCalculator
        
        summary_data = MonthlyCalculator.calculate_monthly_summary(employee, year, month)
        
        summary, created = cls.objects.update_or_create(
            employee=employee,
            year=year,
            month=month,
            defaults={
                'total_work_time': summary_data['total_work_time'],
                'total_break_time': summary_data['total_break_time'],
                'total_overtime': summary_data['total_overtime'],
                'total_undertime': summary_data['total_undertime'],
                'working_days': summary_data['working_days'],
                'attended_days': summary_data['attended_days'],
                'half_days': summary_data['half_days'],
                'late_days': summary_data['late_days'],
                'early_days': summary_data['early_days'],
                'absent_days': summary_data['absent_days'],
                'attendance_percentage': summary_data['attendance_percentage'],
                'punctuality_score': summary_data['punctuality_score'],
                'average_work_hours': summary_data['average_work_hours'],
                'earliest_in_time': summary_data['earliest_in_time'],
                'latest_out_time': summary_data['latest_out_time'],
                'generated_by': generated_by,
            }
        )
        
        return summary
    
    @property
    def formatted_total_work_time(self):
        return TimeCalculator.format_duration_to_excel_time(self.total_work_time)
    
    @property
    def formatted_total_overtime(self):
        return TimeCalculator.format_duration_to_excel_time(self.total_overtime)
    
    @property
    def efficiency_score(self):
        if self.working_days == 0:
            return Decimal('0.00')
        
        attendance_weight = Decimal('0.6')
        punctuality_weight = Decimal('0.4')
        
        score = (self.attendance_percentage * attendance_weight + 
                self.punctuality_score * punctuality_weight)
        return score.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

class AttendanceCorrection(models.Model):
    CORRECTION_TYPES = [
        ('TIME_ADJUSTMENT', 'Time Adjustment'),
        ('STATUS_CHANGE', 'Status Change'),
        ('MANUAL_ENTRY', 'Manual Entry'),
        ('DEVICE_ERROR', 'Device Error Correction'),
        ('LEAVE_ADJUSTMENT', 'Leave Adjustment'),
    ]
    
    CORRECTION_STATUS = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name='corrections')
    correction_type = models.CharField(max_length=20, choices=CORRECTION_TYPES)
    reason = models.TextField()
    
    original_data = models.JSONField(default=dict)
    corrected_data = models.JSONField(default=dict)
    
    requested_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='requested_corrections')
    requested_at = models.DateTimeField(auto_now_add=True)
    
    status = models.CharField(max_length=20, choices=CORRECTION_STATUS, default='PENDING')
    approved_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_corrections')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'attendance_corrections'
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['attendance', 'status']),
            models.Index(fields=['requested_by', 'status']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"Correction for {self.attendance} - {self.correction_type}"
    
    def save(self, *args, **kwargs):
        if not self.original_data:
            self.capture_original_data()
        super().save(*args, **kwargs)
    
    def capture_original_data(self):
        attendance = self.attendance
        self.original_data = {
            'check_in_1': attendance.check_in_1.strftime('%H:%M:%S') if attendance.check_in_1 else None,
            'check_out_1': attendance.check_out_1.strftime('%H:%M:%S') if attendance.check_out_1 else None,
            'check_in_2': attendance.check_in_2.strftime('%H:%M:%S') if attendance.check_in_2 else None,
            'check_out_2': attendance.check_out_2.strftime('%H:%M:%S') if attendance.check_out_2 else None,
            'check_in_3': attendance.check_in_3.strftime('%H:%M:%S') if attendance.check_in_3 else None,
            'check_out_3': attendance.check_out_3.strftime('%H:%M:%S') if attendance.check_out_3 else None,
            'check_in_4': attendance.check_in_4.strftime('%H:%M:%S') if attendance.check_in_4 else None,
            'check_out_4': attendance.check_out_4.strftime('%H:%M:%S') if attendance.check_out_4 else None,
            'check_in_5': attendance.check_in_5.strftime('%H:%M:%S') if attendance.check_in_5 else None,
            'check_out_5': attendance.check_out_5.strftime('%H:%M:%S') if attendance.check_out_5 else None,
            'check_in_6': attendance.check_in_6.strftime('%H:%M:%S') if attendance.check_in_6 else None,
            'check_out_6': attendance.check_out_6.strftime('%H:%M:%S') if attendance.check_out_6 else None,
            'status': attendance.status,
            'notes': attendance.notes,
        }
    
    def approve(self, approved_by_user):
        self.status = 'APPROVED'
        self.approved_by = approved_by_user
        self.approved_at = get_current_datetime()
        self.save(update_fields=['status', 'approved_by', 'approved_at'])
        
        self.apply_correction()
        
        AuditHelper.log_attendance_change(
            user=approved_by_user,
            action='CORRECTION_APPROVED',
            employee=self.attendance.employee,
            attendance_date=self.attendance.date,
            changes={
                'correction_id': str(self.id),
                'correction_type': self.correction_type,
                'original_data': self.original_data,
                'corrected_data': self.corrected_data
            }
        )
    
    def reject(self, rejected_by_user, reason):
        self.status = 'REJECTED'
        self.approved_by = rejected_by_user
        self.approved_at = get_current_datetime()
        self.rejection_reason = reason
        self.save(update_fields=['status', 'approved_by', 'approved_at', 'rejection_reason'])
    
    def apply_correction(self):
        attendance = self.attendance
        
        for field, value in self.corrected_data.items():
            if hasattr(attendance, field):
                if field.startswith('check_'):
                    setattr(attendance, field, safe_time_conversion(value))
                else:
                    setattr(attendance, field, value)
        
        attendance.calculate_attendance_metrics()
        attendance.save()

class AttendanceReport(models.Model):
    REPORT_TYPES = [
        ('DAILY', 'Daily Report'),
        ('WEEKLY', 'Weekly Report'),
        ('MONTHLY', 'Monthly Report'),
        ('CUSTOM', 'Custom Date Range'),
        ('EMPLOYEE', 'Employee Report'),
        ('DEPARTMENT', 'Department Report'),
    ]
    
    REPORT_STATUS = [
        ('GENERATING', 'Generating'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    report_type = models.CharField(max_length=20, choices=REPORT_TYPES)
    start_date = models.DateField()
    end_date = models.DateField()
    
    employees = models.ManyToManyField(CustomUser, blank=True, related_name='attendance_reports')
    departments = models.ManyToManyField(Department, blank=True, related_name='attendance_reports')
    
    filters = models.JSONField(default=dict, blank=True)
    report_data = models.JSONField(default=dict, blank=True)
    
    status = models.CharField(max_length=20, choices=REPORT_STATUS, default='GENERATING')
    file_path = models.CharField(max_length=500, blank=True, null=True)
    
    generated_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='generated_reports')
    generated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'attendance_reports'
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['report_type', 'status']),
            models.Index(fields=['generated_by', 'status']),
            models.Index(fields=['start_date', 'end_date']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.report_type} - {self.start_date} to {self.end_date}"
    
    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError('End date cannot be before start date')
        
        max_range_days = SystemConfiguration.get_int_setting('MAX_REPORT_RANGE_DAYS', 365)
        if (self.end_date - self.start_date).days > max_range_days:
            raise ValidationError(f'Report date range cannot exceed {max_range_days} days')
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
    
    def mark_completed(self, file_path=None):
        self.status = 'COMPLETED'
        self.completed_at = get_current_datetime()
        if file_path:
            self.file_path = file_path
        self.save(update_fields=['status', 'completed_at', 'file_path'])
    
    def mark_failed(self):
        self.status = 'FAILED'
        self.completed_at = get_current_datetime()
        self.save(update_fields=['status', 'completed_at'])

class AttendanceSettings(models.Model):
    SETTING_TYPES = [
        ('SYSTEM', 'System Setting'),
        ('CALCULATION', 'Calculation Setting'),
        ('NOTIFICATION', 'Notification Setting'),
        ('DEVICE', 'Device Setting'),
    ]
    
    id = models.AutoField(primary_key=True)
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    setting_type = models.CharField(max_length=20, choices=SETTING_TYPES, default='SYSTEM')
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    
    objects = models.Manager()
    active = ActiveManager()
    
    class Meta:
        db_table = 'attendance_settings'
        ordering = ['key']
    
    def __str__(self):
        return f"{self.key}: {self.value[:50]}"
    
    @classmethod
    def get_setting(cls, key, default=None):
        try:
            setting = cls.objects.get(key=key.upper(), is_active=True)
            return setting.value
        except cls.DoesNotExist:
            return default
    
    @classmethod
    def set_setting(cls, key, value, setting_type='SYSTEM', description=None, user=None):
        key = key.upper()
        setting, created = cls.objects.update_or_create(
            key=key,
            defaults={
                'value': str(value),
                'setting_type': setting_type,
                'description': description or f'Attendance setting for {key}',
                'updated_by': user,
                'is_active': True
            }
        )
        return setting
    
    @classmethod
    def get_int_setting(cls, key, default=0):
        try:
            value = cls.get_setting(key, str(default))
            return int(value)
        except (ValueError, TypeError):
            return default
    
    @classmethod
    def get_bool_setting(cls, key, default=False):
        value = cls.get_setting(key, str(default).lower())
        return value.lower() in ['true', '1', 'yes', 'on', 'enabled']

def create_daily_attendance_records():
    from .services import AttendanceService
    today = get_current_date()
    active_employees = EmployeeDataManager.get_active_employees()
    
    for employee in active_employees:
        AttendanceService.create_or_get_attendance_record(employee, today)

def process_pending_device_logs():
    from .services import DeviceService
    pending_logs = AttendanceLog.objects.filter(processing_status='PENDING')
    
    grouped_logs = {}
    for log in pending_logs:
        key = f"{log.employee_code}_{log.timestamp.date()}"
        if key not in grouped_logs:
            grouped_logs[key] = []
        grouped_logs[key].append(log)
    
    for key, logs in grouped_logs.items():
        DeviceService.process_employee_daily_logs(logs)

def generate_monthly_summaries(year, month):
    active_employees = EmployeeDataManager.get_active_employees()
    
    for employee in active_employees:
        MonthlyAttendanceSummary.generate_for_employee_month(employee, year, month)

def cleanup_old_attendance_logs():
    retention_days = SystemConfiguration.get_int_setting('ATTENDANCE_LOG_RETENTION_DAYS', 90)
    cutoff_date = get_current_date() - timedelta(days=retention_days)
    
    old_logs = AttendanceLog.objects.filter(
        timestamp__date__lt=cutoff_date,
        processing_status='PROCESSED'
    )
    
    deleted_count = old_logs.count()
    old_logs.delete()
    
    return deleted_count

def sync_all_devices():
    from .services import DeviceService
    active_devices = AttendanceDevice.active.all()
    
    results = {}
    for device in active_devices:
        try:
            result = DeviceService.sync_device_data(device)
            results[device.device_id] = result
        except Exception as e:
            results[device.device_id] = {'success': False, 'error': str(e)}
    
    return results
