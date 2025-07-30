from rest_framework import serializers
from django.contrib.auth import get_user_model
from accounts.models import CustomUser, Department
from employees.models import EmployeeProfile
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
)
from .utils import TimeCalculator, ValidationHelper
from decimal import Decimal

User = get_user_model()


class EmployeeBasicSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "employee_code",
            "full_name",
            "email",
            "department_name",
            "job_title",
        ]
        read_only_fields = ["id", "employee_code", "full_name", "department_name"]


class AttendanceSerializer(serializers.ModelSerializer):
    employee_details = EmployeeBasicSerializer(source="employee", read_only=True)
    department_name = serializers.CharField(
        source="employee.department.name", read_only=True
    )
    formatted_total_time = serializers.CharField(read_only=True)
    formatted_work_time = serializers.CharField(read_only=True)
    formatted_break_time = serializers.CharField(read_only=True)
    formatted_overtime = serializers.CharField(read_only=True)
    attendance_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, read_only=True
    )

    class Meta:
        model = Attendance
        fields = [
            "id",
            "employee",
            "employee_details",
            "department_name",
            "date",
            "status",
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
            "total_time",
            "break_time",
            "work_time",
            "overtime",
            "undertime",
            "formatted_total_time",
            "formatted_work_time",
            "formatted_break_time",
            "formatted_overtime",
            "first_in_time",
            "last_out_time",
            "late_minutes",
            "early_departure_minutes",
            "attendance_percentage",
            "is_manual_entry",
            "notes",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "total_time",
            "break_time",
            "work_time",
            "overtime",
            "undertime",
            "first_in_time",
            "last_out_time",
            "late_minutes",
            "early_departure_minutes",
            "attendance_percentage",
            "created_at",
        ]

    def validate(self, data):
        time_pairs = [
            (data.get("check_in_1"), data.get("check_out_1")),
            (data.get("check_in_2"), data.get("check_out_2")),
            (data.get("check_in_3"), data.get("check_out_3")),
            (data.get("check_in_4"), data.get("check_out_4")),
            (data.get("check_in_5"), data.get("check_out_5")),
            (data.get("check_in_6"), data.get("check_out_6")),
        ]

        is_valid, errors = ValidationHelper.validate_attendance_consistency(time_pairs)
        if not is_valid:
            raise serializers.ValidationError({"time_pairs": errors})

        return data


class AttendanceLogSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )
    device_name = serializers.CharField(source="device.device_name", read_only=True)

    class Meta:
        model = AttendanceLog
        fields = [
            "id",
            "employee_code",
            "employee",
            "employee_name",
            "device",
            "device_name",
            "timestamp",
            "log_type",
            "processing_status",
            "error_message",
            "created_at",
        ]
        read_only_fields = ["id", "employee_name", "device_name", "created_at"]


class DeviceSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    connection_status = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceDevice
        fields = [
            "id",
            "device_id",
            "device_name",
            "device_type",
            "ip_address",
            "port",
            "location",
            "department",
            "department_name",
            "status",
            "last_sync_time",
            "connection_status",
            "is_active",
        ]
        read_only_fields = ["id", "last_sync_time", "connection_status"]

    def get_connection_status(self, obj):
        try:
            is_connected, message = obj.test_connection()
            return {"connected": is_connected, "message": message}
        except Exception as e:
            return {"connected": False, "message": str(e)}


class ShiftSerializer(serializers.ModelSerializer):
    total_duration = serializers.CharField(
        source="total_shift_duration", read_only=True
    )
    working_duration = serializers.CharField(
        source="effective_working_duration", read_only=True
    )

    class Meta:
        model = Shift
        fields = [
            "id",
            "name",
            "code",
            "shift_type",
            "start_time",
            "end_time",
            "break_duration_minutes",
            "grace_period_minutes",
            "working_hours",
            "total_duration",
            "working_duration",
            "is_active",
        ]
        read_only_fields = ["id", "code", "total_duration", "working_duration"]


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = [
            "id",
            "name",
            "code",
            "category",
            "days_allowed_per_year",
            "max_consecutive_days",
            "requires_approval",
            "is_paid",
            "carry_forward_allowed",
            "is_active",
        ]
        read_only_fields = ["id", "code"]


class LeaveBalanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    available_days = serializers.DecimalField(
        max_digits=5, decimal_places=2, read_only=True
    )
    utilization_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, read_only=True
    )

    class Meta:
        model = LeaveBalance
        fields = [
            "id",
            "employee",
            "employee_name",
            "leave_type",
            "leave_type_name",
            "year",
            "allocated_days",
            "used_days",
            "carried_forward_days",
            "available_days",
            "utilization_percentage",
        ]
        read_only_fields = [
            "id",
            "employee_name",
            "leave_type_name",
            "available_days",
            "utilization_percentage",
        ]


class LeaveRequestSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    approved_by_name = serializers.CharField(
        source="approved_by.get_full_name", read_only=True
    )
    can_be_cancelled = serializers.BooleanField(read_only=True)

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee",
            "employee_name",
            "leave_type",
            "leave_type_name",
            "start_date",
            "end_date",
            "total_days",
            "reason",
            "status",
            "is_half_day",
            "half_day_period",
            "applied_at",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "rejection_reason",
            "can_be_cancelled",
        ]
        read_only_fields = [
            "id",
            "employee_name",
            "leave_type_name",
            "total_days",
            "applied_at",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "can_be_cancelled",
        ]


class MonthlyAttendanceSummarySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )
    department_name = serializers.CharField(
        source="employee.department.name", read_only=True
    )
    formatted_total_work_time = serializers.CharField(read_only=True)
    formatted_total_overtime = serializers.CharField(read_only=True)
    efficiency_score = serializers.DecimalField(
        max_digits=5, decimal_places=2, read_only=True
    )

    class Meta:
        model = MonthlyAttendanceSummary
        fields = [
            "id",
            "employee",
            "employee_name",
            "department_name",
            "year",
            "month",
            "total_work_time",
            "total_overtime",
            "formatted_total_work_time",
            "formatted_total_overtime",
            "working_days",
            "attended_days",
            "half_days",
            "late_days",
            "early_days",
            "absent_days",
            "attendance_percentage",
            "punctuality_score",
            "average_work_hours",
            "efficiency_score",
            "earliest_in_time",
            "latest_out_time",
            "generated_at",
        ]
        read_only_fields = [
            "id",
            "employee_name",
            "department_name",
            "formatted_total_work_time",
            "formatted_total_overtime",
            "efficiency_score",
            "generated_at",
        ]


class AttendanceCorrectionSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="attendance.employee.get_full_name", read_only=True
    )
    requested_by_name = serializers.CharField(
        source="requested_by.get_full_name", read_only=True
    )
    approved_by_name = serializers.CharField(
        source="approved_by.get_full_name", read_only=True
    )
    attendance_date = serializers.DateField(source="attendance.date", read_only=True)

    class Meta:
        model = AttendanceCorrection
        fields = [
            "id",
            "attendance",
            "attendance_date",
            "employee_name",
            "correction_type",
            "reason",
            "original_data",
            "corrected_data",
            "status",
            "requested_by",
            "requested_by_name",
            "requested_at",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "rejection_reason",
        ]
        read_only_fields = [
            "id",
            "employee_name",
            "attendance_date",
            "original_data",
            "requested_by_name",
            "requested_at",
            "approved_by",
            "approved_by_name",
            "approved_at",
        ]


class HolidaySerializer(serializers.ModelSerializer):
    applicable_department_names = serializers.SerializerMethodField()
    is_upcoming = serializers.BooleanField(read_only=True)
    days_until = serializers.IntegerField(read_only=True)

    class Meta:
        model = Holiday
        fields = [
            "id",
            "name",
            "date",
            "holiday_type",
            "description",
            "is_optional",
            "applicable_departments",
            "applicable_department_names",
            "applicable_locations",
            "is_paid",
            "is_upcoming",
            "days_until",
            "is_active",
        ]
        read_only_fields = [
            "id",
            "applicable_department_names",
            "is_upcoming",
            "days_until",
        ]

    def get_applicable_department_names(self, obj):
        return [dept.name for dept in obj.applicable_departments.all()]


class AttendanceExportSerializer(serializers.Serializer):
    employee_code = serializers.CharField()
    employee_name = serializers.CharField()
    department = serializers.CharField()
    date = serializers.DateField()
    check_in_1 = serializers.TimeField(allow_null=True)
    check_out_1 = serializers.TimeField(allow_null=True)
    check_in_2 = serializers.TimeField(allow_null=True)
    check_out_2 = serializers.TimeField(allow_null=True)
    check_in_3 = serializers.TimeField(allow_null=True)
    check_out_3 = serializers.TimeField(allow_null=True)
    check_in_4 = serializers.TimeField(allow_null=True)
    check_out_4 = serializers.TimeField(allow_null=True)
    check_in_5 = serializers.TimeField(allow_null=True)
    check_out_5 = serializers.TimeField(allow_null=True)
    check_in_6 = serializers.TimeField(allow_null=True)
    check_out_6 = serializers.TimeField(allow_null=True)
    total_time = serializers.CharField()
    break_time = serializers.CharField()
    work_time = serializers.CharField()
    overtime = serializers.CharField()
    status = serializers.CharField()
    late_minutes = serializers.IntegerField()


class DeviceSyncResultSerializer(serializers.Serializer):
    device_id = serializers.CharField()
    device_name = serializers.CharField()
    sync_status = serializers.CharField()
    records_synced = serializers.IntegerField()
    records_processed = serializers.IntegerField()
    errors = serializers.ListField(child=serializers.CharField(), required=False)
    sync_time = serializers.DateTimeField()


class AttendanceStatsSerializer(serializers.Serializer):
    total_employees = serializers.IntegerField()
    present_today = serializers.IntegerField()
    absent_today = serializers.IntegerField()
    late_today = serializers.IntegerField()
    on_leave_today = serializers.IntegerField()
    attendance_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    average_work_hours = serializers.DecimalField(max_digits=5, decimal_places=2)


class BulkAttendanceUpdateSerializer(serializers.Serializer):
    attendance_records = serializers.ListField(
        child=AttendanceSerializer(), min_length=1, max_length=1000
    )

    def validate_attendance_records(self, value):
        employee_dates = set()
        for record_data in value:
            employee_id = record_data.get("employee")
            date = record_data.get("date")
            key = f"{employee_id}_{date}"

            if key in employee_dates:
                raise serializers.ValidationError(
                    f"Duplicate attendance record for employee {employee_id} on {date}"
                )
            employee_dates.add(key)

        return value
