from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
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
)
from .services import AttendanceService, LeaveService, DeviceService
from .permissions import EmployeeAttendancePermission, LeavePermission, DevicePermission
from .utils import (
    TimeCalculator,
    EmployeeDataManager,
    ValidationHelper,
    get_current_date,
    get_current_datetime,
    safe_time_conversion,
)
from datetime import datetime, date, time, timedelta
from decimal import Decimal

User = get_user_model()


class AttendanceForm(forms.ModelForm):
    check_in_1 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_out_1 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_in_2 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_out_2 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_in_3 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_out_3 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_in_4 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_out_4 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_in_5 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_out_5 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_in_6 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    check_out_6 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )

    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Additional notes...",
            }
        ),
    )

    class Meta:
        model = Attendance
        fields = [
            "employee",
            "date",
            "check_in_1",
            "check_out_1",
            "check_in_2",
            "check_out_2",
            "check_in_3",
            "check_out_3",
            "check_in_4",
            "check_out_4",
            "check_in_5",
            "check_out_5",
            "check_in_6",
            "check_out_6",
            "notes",
        ]
        widgets = {
            "employee": forms.Select(attrs={"class": "form-control select2"}),
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if self.user:
            accessible_employees = self.get_accessible_employees()
            self.fields["employee"].queryset = accessible_employees

        if self.instance and self.instance.pk:
            self.fields["employee"].widget.attrs["readonly"] = True
            self.fields["date"].widget.attrs["readonly"] = True

    def get_accessible_employees(self):
        if not self.user:
            return CustomUser.objects.none()

        if self.user.is_superuser:
            return CustomUser.active.all()

        if self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return CustomUser.active.all()

        if self.user.role and self.user.role.can_view_all_data:
            return CustomUser.active.all()

        accessible_employees = [self.user]

        if self.user.role and self.user.role.name == "DEPARTMENT_MANAGER":
            if self.user.department:
                dept_employees = self.user.department.employees.filter(is_active=True)
                accessible_employees.extend(dept_employees)

        subordinates = self.user.get_subordinates()
        accessible_employees.extend(subordinates)

        return CustomUser.objects.filter(
            id__in=[emp.id for emp in accessible_employees]
        )

    def clean(self):
        cleaned_data = super().clean()
        employee = cleaned_data.get("employee")
        attendance_date = cleaned_data.get("date")

        if not employee or not attendance_date:
            return cleaned_data

        if self.user and not EmployeeAttendancePermission.can_edit_employee_attendance(
            self.user, employee
        ):
            raise ValidationError(
                "You don't have permission to edit attendance for this employee"
            )

        if attendance_date > get_current_date():
            raise ValidationError("Attendance date cannot be in the future")

        time_pairs = [
            (cleaned_data.get("check_in_1"), cleaned_data.get("check_out_1")),
            (cleaned_data.get("check_in_2"), cleaned_data.get("check_out_2")),
            (cleaned_data.get("check_in_3"), cleaned_data.get("check_out_3")),
            (cleaned_data.get("check_in_4"), cleaned_data.get("check_out_4")),
            (cleaned_data.get("check_in_5"), cleaned_data.get("check_out_5")),
            (cleaned_data.get("check_in_6"), cleaned_data.get("check_out_6")),
        ]

        is_valid, errors = ValidationHelper.validate_attendance_consistency(time_pairs)
        if not is_valid:
            raise ValidationError("; ".join(errors))

        is_employee_valid, message = (
            EmployeeDataManager.validate_employee_for_attendance(employee)
        )
        if not is_employee_valid:
            raise ValidationError(message)

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.is_manual_entry = True

        if self.user:
            instance._attendance_changed_by = self.user
            if not instance.created_by:
                instance.created_by = self.user

        if commit:
            instance.save()

        return instance


class BulkAttendanceForm(forms.Form):
    employees = forms.ModelMultipleChoiceField(
        queryset=CustomUser.objects.none(),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        required=True,
    )
    date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        required=True,
    )
    status = forms.ChoiceField(
        choices=Attendance.STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "form-control"}),
        required=True,
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Bulk update notes...",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if self.user:
            accessible_employees = self.get_accessible_employees()
            self.fields["employees"].queryset = accessible_employees

    def get_accessible_employees(self):
        if not self.user:
            return CustomUser.objects.none()

        if self.user.is_superuser:
            return CustomUser.active.all()

        if self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return CustomUser.active.all()

        if self.user.role and self.user.role.can_view_all_data:
            return CustomUser.active.all()

        accessible_employees = []

        if self.user.role and self.user.role.name == "DEPARTMENT_MANAGER":
            if self.user.department:
                dept_employees = self.user.department.employees.filter(is_active=True)
                accessible_employees.extend(dept_employees)

        subordinates = self.user.get_subordinates()
        accessible_employees.extend(subordinates)

        return CustomUser.objects.filter(
            id__in=[emp.id for emp in accessible_employees]
        )

    def clean_date(self):
        attendance_date = self.cleaned_data["date"]

        if attendance_date > get_current_date():
            raise ValidationError("Attendance date cannot be in the future")

        return attendance_date

    def clean(self):
        cleaned_data = super().clean()
        employees = cleaned_data.get("employees")

        if employees and self.user:
            for employee in employees:
                if not EmployeeAttendancePermission.can_edit_employee_attendance(
                    self.user, employee
                ):
                    raise ValidationError(
                        f"You don't have permission to edit attendance for {employee.get_full_name()}"
                    )

        return cleaned_data


class AttendanceDeviceForm(forms.ModelForm):
    test_connection = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="Test device connection after saving",
    )

    class Meta:
        model = AttendanceDevice
        fields = [
            "device_id",
            "device_name",
            "device_type",
            "ip_address",
            "port",
            "location",
            "department",
            "status",
            "is_active",
        ]
        widgets = {
            "device_id": forms.TextInput(attrs={"class": "form-control"}),
            "device_name": forms.TextInput(attrs={"class": "form-control"}),
            "device_type": forms.Select(attrs={"class": "form-control"}),
            "ip_address": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "192.168.1.100"}
            ),
            "port": forms.NumberInput(
                attrs={"class": "form-control", "placeholder": "4370"}
            ),
            "location": forms.TextInput(attrs={"class": "form-control"}),
            "department": forms.Select(attrs={"class": "form-control select2"}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        self.fields["department"].queryset = Department.active.all()

        if self.instance and self.instance.pk:
            self.fields["device_id"].widget.attrs["readonly"] = True

    def clean_device_id(self):
        device_id = self.cleaned_data["device_id"]

        if not device_id:
            raise ValidationError("Device ID is required")

        existing_device = AttendanceDevice.objects.filter(device_id=device_id).exclude(
            pk=self.instance.pk if self.instance else None
        )
        if existing_device.exists():
            raise ValidationError("Device with this ID already exists")

        return device_id

    def clean_ip_address(self):
        ip_address = self.cleaned_data["ip_address"]

        if not ip_address:
            raise ValidationError("IP address is required")

        import re

        ip_pattern = r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
        if not re.match(ip_pattern, ip_address):
            raise ValidationError("Invalid IP address format")

        return ip_address

    def clean_port(self):
        port = self.cleaned_data["port"]

        if not port:
            raise ValidationError("Port is required")

        if port < 1 or port > 65535:
            raise ValidationError("Port must be between 1 and 65535")

        return port

    def clean(self):
        cleaned_data = super().clean()

        if self.user and not DevicePermission.can_manage_devices(self.user):
            raise ValidationError(
                "You don't have permission to manage attendance devices"
            )

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        if self.user and not instance.created_by:
            instance.created_by = self.user

        if commit:
            instance.save()

            if self.cleaned_data.get("test_connection"):
                is_connected, message = instance.test_connection()
                if not is_connected:
                    instance.status = "ERROR"
                    instance.save(update_fields=["status"])

        return instance


class LeaveRequestForm(forms.ModelForm):
    start_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "min": get_current_date().isoformat(),
            }
        )
    )
    end_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "min": get_current_date().isoformat(),
            }
        )
    )
    medical_certificate = forms.FileField(
        required=False,
        widget=forms.FileInput(
            attrs={"class": "form-control", "accept": ".pdf,.jpg,.jpeg,.png,.doc,.docx"}
        ),
    )

    class Meta:
        model = LeaveRequest
        fields = [
            "employee",
            "leave_type",
            "start_date",
            "end_date",
            "reason",
            "is_half_day",
            "half_day_period",
            "emergency_contact_during_leave",
            "handover_notes",
            "medical_certificate",
        ]
        widgets = {
            "employee": forms.Select(attrs={"class": "form-control select2"}),
            "leave_type": forms.Select(attrs={"class": "form-control select2"}),
            "reason": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "is_half_day": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "half_day_period": forms.Select(attrs={"class": "form-control"}),
            "emergency_contact_during_leave": forms.TextInput(
                attrs={"class": "form-control"}
            ),
            "handover_notes": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if self.user:
            accessible_employees = self.get_accessible_employees()
            self.fields["employee"].queryset = accessible_employees

            if not self.user.is_superuser and not (
                self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]
            ):
                self.fields["employee"].initial = self.user
                self.fields["employee"].widget.attrs["readonly"] = True

        self.fields["leave_type"].queryset = LeaveType.active.all()

        self.fields["half_day_period"].widget.attrs["style"] = "display: none;"

    def get_accessible_employees(self):
        if not self.user:
            return CustomUser.objects.none()

        if self.user.is_superuser:
            return CustomUser.active.all()

        if self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return CustomUser.active.all()

        accessible_employees = [self.user]

        if self.user.role and self.user.role.name == "DEPARTMENT_MANAGER":
            if self.user.department:
                dept_employees = self.user.department.employees.filter(is_active=True)
                accessible_employees.extend(dept_employees)

        return CustomUser.objects.filter(
            id__in=[emp.id for emp in accessible_employees]
        )

    def clean_start_date(self):
        start_date = self.cleaned_data["start_date"]

        if start_date < get_current_date():
            raise ValidationError("Leave start date cannot be in the past")

        return start_date

    def clean_end_date(self):
        end_date = self.cleaned_data["end_date"]
        start_date = self.cleaned_data.get("start_date")

        if start_date and end_date < start_date:
            raise ValidationError("End date cannot be before start date")

        return end_date

    def clean(self):
        cleaned_data = super().clean()
        employee = cleaned_data.get("employee")
        leave_type = cleaned_data.get("leave_type")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        is_half_day = cleaned_data.get("is_half_day")
        half_day_period = cleaned_data.get("half_day_period")

        if not employee or not leave_type or not start_date or not end_date:
            return cleaned_data

        if self.user and not LeavePermission.can_apply_leave(self.user, employee):
            raise ValidationError(
                "You don't have permission to apply leave for this employee"
            )

        if is_half_day and start_date != end_date:
            raise ValidationError("Half day leave must be for a single date")

        if is_half_day and not half_day_period:
            raise ValidationError("Half day period is required for half day leave")

        if leave_type.requires_medical_certificate:
            total_days = (end_date - start_date).days + 1
            if total_days >= 3 and not self.cleaned_data.get("medical_certificate"):
                raise ValidationError(
                    "Medical certificate is required for this leave type"
                )

        notice_days = (start_date - get_current_date()).days
        if notice_days < leave_type.min_notice_days:
            raise ValidationError(
                f"Minimum {leave_type.min_notice_days} days notice required"
            )

        overlapping_requests = LeaveRequest.objects.filter(
            employee=employee,
            status__in=["PENDING", "APPROVED"],
            start_date__lte=end_date,
            end_date__gte=start_date,
        ).exclude(pk=self.instance.pk if self.instance else None)

        if overlapping_requests.exists():
            raise ValidationError("Leave request overlaps with existing request")

        leave_balance = LeaveService.get_employee_leave_balance(
            employee, leave_type, start_date.year
        )
        total_days = (
            Decimal("0.5")
            if is_half_day
            else Decimal(str((end_date - start_date).days + 1))
        )

        if not leave_balance.can_apply_leave(total_days):
            raise ValidationError(
                f"Insufficient leave balance. Available: {leave_balance.available_days} days"
            )

        return cleaned_data


class LeaveApprovalForm(forms.ModelForm):
    approval_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Approval notes (optional)...",
            }
        ),
    )

    class Meta:
        model = LeaveRequest
        fields = ["approval_notes"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.action = kwargs.pop("action", "approve")
        super().__init__(*args, **kwargs)

        if self.action == "reject":
            self.fields["approval_notes"].required = True
            self.fields["approval_notes"].widget.attrs[
                "placeholder"
            ] = "Rejection reason (required)..."

    def clean(self):
        cleaned_data = super().clean()

        if not self.instance or not self.instance.pk:
            raise ValidationError("Invalid leave request")

        if self.user and not LeavePermission.can_approve_leave(
            self.user, self.instance
        ):
            raise ValidationError(
                "You don't have permission to approve/reject this leave request"
            )

        if self.instance.status != "PENDING":
            raise ValidationError("Leave request is not in pending status")

        return cleaned_data


class ShiftForm(forms.ModelForm):
    class Meta:
        model = Shift
        fields = [
            "name",
            "shift_type",
            "start_time",
            "end_time",
            "break_duration_minutes",
            "grace_period_minutes",
            "working_hours",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "shift_type": forms.Select(attrs={"class": "form-control"}),
            "start_time": forms.TimeInput(
                attrs={"type": "time", "class": "form-control"}
            ),
            "end_time": forms.TimeInput(
                attrs={"type": "time", "class": "form-control"}
            ),
            "break_duration_minutes": forms.NumberInput(
                attrs={"class": "form-control", "min": "0"}
            ),
            "grace_period_minutes": forms.NumberInput(
                attrs={"class": "form-control", "min": "0"}
            ),
            "working_hours": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.5", "min": "1"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_end_time(self):
        end_time = self.cleaned_data["end_time"]
        start_time = self.cleaned_data.get("start_time")

        if start_time and end_time <= start_time:
            raise ValidationError("End time must be after start time")

        return end_time

    def clean_working_hours(self):
        working_hours = self.cleaned_data["working_hours"]

        if working_hours <= 0:
            raise ValidationError("Working hours must be greater than 0")

        if working_hours > 24:
            raise ValidationError("Working hours cannot exceed 24 hours")

        return working_hours

    def clean(self):
        cleaned_data = super().clean()

        if self.user and not self.user.has_permission("change_shift"):
            if not (
                self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]
            ):
                raise ValidationError("You don't have permission to manage shifts")

        return cleaned_data


class EmployeeShiftAssignmentForm(forms.ModelForm):
    class Meta:
        model = EmployeeShift
        fields = ["employee", "shift", "effective_from", "effective_to", "notes"]
        widgets = {
            "employee": forms.Select(attrs={"class": "form-control select2"}),
            "shift": forms.Select(attrs={"class": "form-control select2"}),
            "effective_from": forms.DateInput(
                attrs={"type": "date", "class": "form-control"}
            ),
            "effective_to": forms.DateInput(
                attrs={"type": "date", "class": "form-control"}
            ),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if self.user:
            accessible_employees = self.get_accessible_employees()
            self.fields["employee"].queryset = accessible_employees

        self.fields["shift"].queryset = Shift.active.all()

    def get_accessible_employees(self):
        if not self.user:
            return CustomUser.objects.none()

        if self.user.is_superuser:
            return CustomUser.active.all()

        if self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return CustomUser.active.all()

        if self.user.role and self.user.role.can_manage_employees:
            return CustomUser.active.all()

        accessible_employees = []

        if self.user.role and self.user.role.name == "DEPARTMENT_MANAGER":
            if self.user.department:
                dept_employees = self.user.department.employees.filter(is_active=True)
                accessible_employees.extend(dept_employees)

        subordinates = self.user.get_subordinates()
        accessible_employees.extend(subordinates)

        return CustomUser.objects.filter(
            id__in=[emp.id for emp in accessible_employees]
        )

    def clean_effective_from(self):
        effective_from = self.cleaned_data["effective_from"]

        if effective_from < get_current_date():
            raise ValidationError("Effective from date cannot be in the past")

        return effective_from

    def clean_effective_to(self):
        effective_to = self.cleaned_data.get("effective_to")
        effective_from = self.cleaned_data.get("effective_from")

        if effective_to and effective_from and effective_to <= effective_from:
            raise ValidationError("Effective to date must be after effective from date")

        return effective_to

    def clean(self):
        cleaned_data = super().clean()
        employee = cleaned_data.get("employee")
        effective_from = cleaned_data.get("effective_from")
        effective_to = cleaned_data.get("effective_to")

        if employee and effective_from:
            overlapping_assignments = EmployeeShift.objects.filter(
                employee=employee,
                is_active=True,
                effective_from__lte=effective_to or date(2099, 12, 31),
                effective_to__gte=effective_from,
            ).exclude(pk=self.instance.pk if self.instance else None)

            if overlapping_assignments.exists():
                raise ValidationError(
                    "Employee already has a shift assigned for this period"
                )

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        if self.user:
            instance.assigned_by = self.user

        if commit:
            instance.save()

        return instance


class HolidayForm(forms.ModelForm):
    applicable_departments = forms.ModelMultipleChoiceField(
        queryset=Department.active.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = Holiday
        fields = [
            "name",
            "date",
            "holiday_type",
            "description",
            "is_optional",
            "applicable_departments",
            "is_paid",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "holiday_type": forms.Select(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "is_optional": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_paid": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_date(self):
        holiday_date = self.cleaned_data["date"]

        existing_holiday = Holiday.objects.filter(
            date=holiday_date, name=self.cleaned_data.get("name", "")
        ).exclude(pk=self.instance.pk if self.instance else None)

        if existing_holiday.exists():
            raise ValidationError("Holiday with this name already exists on this date")

        return holiday_date

    def clean(self):
        cleaned_data = super().clean()

        if self.user and not self.user.has_permission("change_holiday"):
            if not (
                self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]
            ):
                raise ValidationError("You don't have permission to manage holidays")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        if self.user and not instance.created_by:
            instance.created_by = self.user

        if commit:
            instance.save()
            self.save_m2m()

        return instance


class AttendanceCorrectionForm(forms.ModelForm):
    corrected_check_in_1 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_out_1 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_in_2 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_out_2 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_in_3 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_out_3 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_in_4 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_out_4 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_in_5 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_out_5 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_in_6 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )
    corrected_check_out_6 = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={"type": "time", "class": "form-control", "step": "1"}
        ),
    )

    class Meta:
        model = AttendanceCorrection
        fields = ["attendance", "correction_type", "reason"]
        widgets = {
            "attendance": forms.HiddenInput(),
            "correction_type": forms.Select(attrs={"class": "form-control"}),
            "reason": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.attendance_instance = kwargs.pop("attendance_instance", None)
        super().__init__(*args, **kwargs)

        if self.attendance_instance:
            self.fields["attendance"].initial = self.attendance_instance

            self.fields["corrected_check_in_1"].initial = (
                self.attendance_instance.check_in_1
            )
            self.fields["corrected_check_out_1"].initial = (
                self.attendance_instance.check_out_1
            )
            self.fields["corrected_check_in_2"].initial = (
                self.attendance_instance.check_in_2
            )
            self.fields["corrected_check_out_2"].initial = (
                self.attendance_instance.check_out_2
            )
            self.fields["corrected_check_in_3"].initial = (
                self.attendance_instance.check_in_3
            )
            self.fields["corrected_check_out_3"].initial = (
                self.attendance_instance.check_out_3
            )
            self.fields["corrected_check_in_4"].initial = (
                self.attendance_instance.check_in_4
            )
            self.fields["corrected_check_out_4"].initial = (
                self.attendance_instance.check_out_4
            )
            self.fields["corrected_check_in_5"].initial = (
                self.attendance_instance.check_in_5
            )
            self.fields["corrected_check_out_5"].initial = (
                self.attendance_instance.check_out_5
            )
            self.fields["corrected_check_in_6"].initial = (
                self.attendance_instance.check_in_6
            )
            self.fields["corrected_check_out_6"].initial = (
                self.attendance_instance.check_out_6
            )

    def clean(self):
        cleaned_data = super().clean()
        attendance = cleaned_data.get("attendance") or self.attendance_instance

        if not attendance:
            raise ValidationError("Attendance record is required")

        if self.user and not EmployeeAttendancePermission.can_edit_employee_attendance(
            self.user, attendance.employee
        ):
            raise ValidationError(
                "You don't have permission to request correction for this attendance"
            )

        corrected_time_pairs = [
            (
                cleaned_data.get("corrected_check_in_1"),
                cleaned_data.get("corrected_check_out_1"),
            ),
            (
                cleaned_data.get("corrected_check_in_2"),
                cleaned_data.get("corrected_check_out_2"),
            ),
            (
                cleaned_data.get("corrected_check_in_3"),
                cleaned_data.get("corrected_check_out_3"),
            ),
            (
                cleaned_data.get("corrected_check_in_4"),
                cleaned_data.get("corrected_check_out_4"),
            ),
            (
                cleaned_data.get("corrected_check_in_5"),
                cleaned_data.get("corrected_check_out_5"),
            ),
            (
                cleaned_data.get("corrected_check_in_6"),
                cleaned_data.get("corrected_check_out_6"),
            ),
        ]

        is_valid, errors = ValidationHelper.validate_attendance_consistency(
            corrected_time_pairs
        )
        if not is_valid:
            raise ValidationError("; ".join(errors))

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        corrected_data = {}
        for i in range(1, 7):
            in_time = self.cleaned_data.get(f"corrected_check_in_{i}")
            out_time = self.cleaned_data.get(f"corrected_check_out_{i}")

            if in_time:
                corrected_data[f"check_in_{i}"] = in_time.strftime("%H:%M:%S")
            if out_time:
                corrected_data[f"check_out_{i}"] = out_time.strftime("%H:%M:%S")

        instance.corrected_data = corrected_data

        if self.user:
            instance.requested_by = self.user

        if commit:
            instance.save()

        return instance


class AttendanceReportForm(forms.Form):
    REPORT_TYPE_CHOICES = [
        ("daily", "Daily Report"),
        ("weekly", "Weekly Report"),
        ("monthly", "Monthly Report"),
        ("custom", "Custom Date Range"),
        ("employee", "Employee Report"),
        ("department", "Department Report"),
        ("overtime", "Overtime Report"),
        ("leave", "Leave Report"),
    ]

    report_type = forms.ChoiceField(
        choices=REPORT_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )
    employees = forms.ModelMultipleChoiceField(
        queryset=CustomUser.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-control select2-multiple"}),
    )
    departments = forms.ModelMultipleChoiceField(
        queryset=Department.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-control select2-multiple"}),
    )
    export_format = forms.ChoiceField(
        choices=[("excel", "Excel"), ("pdf", "PDF"), ("csv", "CSV")],
        widget=forms.Select(attrs={"class": "form-control"}),
        initial="excel",
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if self.user:
            accessible_employees = self.get_accessible_employees()
            accessible_departments = self.get_accessible_departments()

            self.fields["employees"].queryset = accessible_employees
            self.fields["departments"].queryset = accessible_departments

    def get_accessible_employees(self):
        if not self.user:
            return CustomUser.objects.none()

        if self.user.is_superuser:
            return CustomUser.active.all()

        if self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return CustomUser.active.all()

        if self.user.role and self.user.role.can_view_all_data:
            return CustomUser.active.all()

        accessible_employees = [self.user]

        if self.user.role and self.user.role.name == "DEPARTMENT_MANAGER":
            if self.user.department:
                dept_employees = self.user.department.employees.filter(is_active=True)
                accessible_employees.extend(dept_employees)

        subordinates = self.user.get_subordinates()
        accessible_employees.extend(subordinates)

        return CustomUser.objects.filter(
            id__in=[emp.id for emp in accessible_employees]
        )

    def get_accessible_departments(self):
        if not self.user:
            return Department.objects.none()

        if self.user.is_superuser:
            return Department.active.all()

        if self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return Department.active.all()

        if self.user.role and self.user.role.can_view_all_data:
            return Department.active.all()

        accessible_departments = []

        if self.user.department:
            accessible_departments.append(self.user.department)

        return Department.objects.filter(
            id__in=[dept.id for dept in accessible_departments]
        )

    def clean_end_date(self):
        end_date = self.cleaned_data["end_date"]
        start_date = self.cleaned_data.get("start_date")

        if start_date and end_date < start_date:
            raise ValidationError("End date cannot be before start date")

        if start_date:
            max_range_days = SystemConfiguration.get_int_setting(
                "MAX_REPORT_RANGE_DAYS", 365
            )
            if (end_date - start_date).days > max_range_days:
                raise ValidationError(
                    f"Report date range cannot exceed {max_range_days} days"
                )

        return end_date

    def clean(self):
        cleaned_data = super().clean()
        report_type = cleaned_data.get("report_type")
        employees = cleaned_data.get("employees")
        departments = cleaned_data.get("departments")

        if report_type == "employee" and not employees:
            raise ValidationError(
                "At least one employee must be selected for employee report"
            )

        if report_type == "department" and not departments:
            raise ValidationError(
                "At least one department must be selected for department report"
            )

        return cleaned_data


class ExcelImportForm(forms.Form):
    excel_file = forms.FileField(
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".xlsx,.xls"})
    )
    overwrite_existing = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="Overwrite existing attendance records",
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_excel_file(self):
        excel_file = self.cleaned_data["excel_file"]

        if not excel_file.name.endswith((".xlsx", ".xls")):
            raise ValidationError("Only Excel files (.xlsx, .xls) are allowed")

        max_file_size = (
            SystemConfiguration.get_int_setting("MAX_EXCEL_FILE_SIZE_MB", 10)
            * 1024
            * 1024
        )
        if excel_file.size > max_file_size:
            raise ValidationError(
                f"File size cannot exceed {max_file_size // (1024 * 1024)}MB"
            )

        return excel_file

    def clean(self):
        cleaned_data = super().clean()

        if self.user and not self.user.has_permission("change_attendance"):
            if not (
                self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]
            ):
                raise ValidationError(
                    "You don't have permission to import attendance data"
                )

        return cleaned_data


class DeviceSyncForm(forms.Form):
    devices = forms.ModelMultipleChoiceField(
        queryset=AttendanceDevice.objects.none(),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        required=False,
    )
    sync_all = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="Sync all active devices",
    )
    sync_employees = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="Also sync employee data to devices",
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        self.fields["devices"].queryset = AttendanceDevice.active.all()

    def clean(self):
        cleaned_data = super().clean()
        devices = cleaned_data.get("devices")
        sync_all = cleaned_data.get("sync_all")

        if not sync_all and not devices:
            raise ValidationError("Select devices to sync or choose 'Sync All'")

        if self.user and not DevicePermission.can_sync_device_data(self.user):
            raise ValidationError("You don't have permission to sync device data")

        return cleaned_data


class LeaveBalanceAdjustmentForm(forms.Form):
    employee = forms.ModelChoiceField(
        queryset=CustomUser.objects.none(),
        widget=forms.Select(attrs={"class": "form-control select2"}),
    )
    leave_type = forms.ModelChoiceField(
        queryset=LeaveType.objects.none(),
        widget=forms.Select(attrs={"class": "form-control select2"}),
    )
    year = forms.IntegerField(
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "2020"}),
        initial=get_current_date().year,
    )
    adjustment_days = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.5"}),
        help_text="Use negative values to deduct days",
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text="Reason for adjustment",
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if self.user:
            accessible_employees = self.get_accessible_employees()
            self.fields["employee"].queryset = accessible_employees

        self.fields["leave_type"].queryset = LeaveType.active.all()

    def get_accessible_employees(self):
        if not self.user:
            return CustomUser.objects.none()

        if self.user.is_superuser:
            return CustomUser.active.all()

        if self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]:
            return CustomUser.active.all()

        if self.user.role and self.user.role.can_manage_employees:
            return CustomUser.active.all()

        return CustomUser.objects.none()

    def clean_adjustment_days(self):
        adjustment_days = self.cleaned_data["adjustment_days"]

        if adjustment_days == 0:
            raise ValidationError("Adjustment days cannot be zero")

        return adjustment_days

    def clean(self):
        cleaned_data = super().clean()

        if self.user and not (
            self.user.role and self.user.role.name in ["HR_ADMIN", "HR_MANAGER"]
        ):
            raise ValidationError("You don't have permission to adjust leave balances")

        return cleaned_data
