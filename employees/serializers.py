from rest_framework import serializers
from django.contrib.auth import get_user_model
from accounts.models import CustomUser, Department
from .models import EmployeeProfile, Education, Contract


User = get_user_model()


class EmployeeProfileSerializer(serializers.ModelSerializer):
    user_full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    user_email = serializers.CharField(source="user.email", read_only=True)
    user_employee_code = serializers.CharField(
        source="user.employee_code", read_only=True
    )
    department_name = serializers.CharField(
        source="user.department.name", read_only=True
    )
    years_of_service = serializers.ReadOnlyField()
    is_on_probation = serializers.ReadOnlyField()

    class Meta:
        model = EmployeeProfile
        fields = [
            "id",
            "employee_id",
            "user",
            "user_full_name",
            "user_email",
            "user_employee_code",
            "department_name",
            "employment_status",
            "grade_level",
            "basic_salary",
            "probation_end_date",
            "confirmation_date",
            "bank_name",
            "bank_account_number",
            "bank_branch",
            "tax_identification_number",
            "marital_status",
            "spouse_name",
            "number_of_children",
            "work_location",
            "reporting_time",
            "shift_hours",
            "years_of_service",
            "is_on_probation",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "employee_id",
            "years_of_service",
            "is_on_probation",
            "created_at",
            "updated_at",
        ]

    def validate_basic_salary(self, value):
        if value <= 0:
            raise serializers.ValidationError("Basic salary must be greater than zero.")
        return value

    def validate_tax_identification_number(self, value):
        if value:
            queryset = EmployeeProfile.objects.filter(tax_identification_number=value)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError(
                    "This tax identification number is already in use."
                )
        return value


class EmployeeProfileListSerializer(serializers.ModelSerializer):
    user_full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    department_name = serializers.CharField(
        source="user.department.name", read_only=True
    )
    employment_status_display = serializers.CharField(
        source="get_employment_status_display", read_only=True
    )
    grade_level_display = serializers.CharField(
        source="get_grade_level_display", read_only=True
    )

    class Meta:
        model = EmployeeProfile
        fields = [
            "id",
            "employee_id",
            "user_full_name",
            "department_name",
            "employment_status",
            "employment_status_display",
            "grade_level",
            "grade_level_display",
            "basic_salary",
            "is_active",
        ]


class EducationSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )
    employee_code = serializers.CharField(
        source="employee.employee_code", read_only=True
    )
    education_level_display = serializers.CharField(
        source="get_education_level_display", read_only=True
    )
    verified_by_name = serializers.CharField(
        source="verified_by.get_full_name", read_only=True
    )

    class Meta:
        model = Education
        fields = [
            "id",
            "employee",
            "employee_name",
            "employee_code",
            "education_level",
            "education_level_display",
            "qualification",
            "institution",
            "field_of_study",
            "start_year",
            "completion_year",
            "grade_gpa",
            "certificate_file",
            "is_verified",
            "verified_by",
            "verified_by_name",
            "verified_at",
            "is_active",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "is_verified",
            "verified_by",
            "verified_at",
            "created_at",
        ]

    def validate(self, data):
        start_year = data.get("start_year")
        completion_year = data.get("completion_year")

        if start_year and completion_year:
            if completion_year < start_year:
                raise serializers.ValidationError(
                    {"completion_year": "Completion year cannot be before start year."}
                )

            duration = completion_year - start_year
            if duration > 15:
                raise serializers.ValidationError(
                    {"completion_year": "Education duration seems unusually long."}
                )

        return data


class ContractSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )
    employee_code = serializers.CharField(
        source="employee.employee_code", read_only=True
    )
    department_name = serializers.CharField(source="department.name", read_only=True)
    reporting_manager_name = serializers.CharField(
        source="reporting_manager.get_full_name", read_only=True
    )
    contract_type_display = serializers.CharField(
        source="get_contract_type_display", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    is_expired = serializers.ReadOnlyField()
    days_remaining = serializers.ReadOnlyField()
    contract_duration_days = serializers.ReadOnlyField()

    class Meta:
        model = Contract
        fields = [
            "id",
            "contract_number",
            "employee",
            "employee_name",
            "employee_code",
            "contract_type",
            "contract_type_display",
            "status",
            "status_display",
            "start_date",
            "end_date",
            "signed_date",
            "job_title",
            "department",
            "department_name",
            "reporting_manager",
            "reporting_manager_name",
            "basic_salary",
            "terms_and_conditions",
            "benefits",
            "working_hours",
            "probation_period_months",
            "notice_period_days",
            "contract_file",
            "is_expired",
            "days_remaining",
            "contract_duration_days",
            "is_active",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "contract_number",
            "is_expired",
            "days_remaining",
            "contract_duration_days",
            "created_at",
        ]

    def validate(self, data):
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        employee = data.get("employee")
        contract_type = data.get("contract_type")

        if start_date and end_date:
            if end_date <= start_date:
                raise serializers.ValidationError(
                    {"end_date": "End date must be after start date."}
                )

        if contract_type in ["FIXED_TERM", "INTERNSHIP", "CONSULTANT"] and not end_date:
            raise serializers.ValidationError(
                {"end_date": f"End date is required for {contract_type} contracts."}
            )

        if employee and start_date and end_date:
            overlapping_contracts = Contract.objects.filter(
                employee=employee,
                status="ACTIVE",
                start_date__lte=end_date,
                end_date__gte=start_date,
            )
            if self.instance:
                overlapping_contracts = overlapping_contracts.exclude(
                    pk=self.instance.pk
                )

            if overlapping_contracts.exists():
                raise serializers.ValidationError(
                    "Contract dates overlap with existing active contract."
                )

        return data


class ContractListSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.get_full_name", read_only=True
    )
    contract_type_display = serializers.CharField(
        source="get_contract_type_display", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    days_remaining = serializers.ReadOnlyField()

    class Meta:
        model = Contract
        fields = [
            "id",
            "contract_number",
            "employee_name",
            "contract_type",
            "contract_type_display",
            "status",
            "status_display",
            "start_date",
            "end_date",
            "basic_salary",
            "days_remaining",
            "is_active",
        ]


class EmployeeExportSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.get_full_name")
    email = serializers.CharField(source="user.email")
    phone_number = serializers.CharField(source="user.phone_number")
    department = serializers.CharField(source="user.department.name")
    hire_date = serializers.CharField(source="user.hire_date")
    employment_status_display = serializers.CharField(
        source="get_employment_status_display"
    )
    grade_level_display = serializers.CharField(source="get_grade_level_display")
    marital_status_display = serializers.CharField(source="get_marital_status_display")

    class Meta:
        model = EmployeeProfile
        fields = [
            "employee_id",
            "full_name",
            "email",
            "phone_number",
            "department",
            "employment_status_display",
            "grade_level_display",
            "basic_salary",
            "hire_date",
            "probation_end_date",
            "confirmation_date",
            "marital_status_display",
            "spouse_name",
            "number_of_children",
            "work_location",
            "bank_name",
            "bank_account_number",
            "tax_identification_number",
        ]


class EducationExportSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.get_full_name")
    employee_id = serializers.CharField(source="employee.employee_profile.employee_id")
    education_level_display = serializers.CharField(
        source="get_education_level_display"
    )

    class Meta:
        model = Education
        fields = [
            "employee_name",
            "employee_id",
            "education_level_display",
            "qualification",
            "institution",
            "field_of_study",
            "start_year",
            "completion_year",
            "grade_gpa",
        ]


class ContractExportSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.get_full_name")
    employee_id = serializers.CharField(source="employee.employee_profile.employee_id")
    contract_type_display = serializers.CharField(source="get_contract_type_display")
    status_display = serializers.CharField(source="get_status_display")
    department_name = serializers.CharField(source="department.name")

    class Meta:
        model = Contract
        fields = [
            "contract_number",
            "employee_name",
            "employee_id",
            "contract_type_display",
            "status_display",
            "start_date",
            "end_date",
            "job_title",
            "department_name",
            "basic_salary",
            "working_hours",
            "probation_period_months",
            "notice_period_days",
        ]


class BulkEmployeeImportSerializer(serializers.Serializer):
    employee_id = serializers.CharField(max_length=20, required=False)
    first_name = serializers.CharField(max_length=50)
    last_name = serializers.CharField(max_length=50)
    email = serializers.EmailField()
    phone_number = serializers.CharField(max_length=15, required=False)
    department_code = serializers.CharField(max_length=20)
    employment_status = serializers.ChoiceField(
        choices=EmployeeProfile.EMPLOYMENT_STATUS_CHOICES
    )
    grade_level = serializers.ChoiceField(choices=EmployeeProfile.GRADE_LEVELS)
    basic_salary = serializers.DecimalField(max_digits=12, decimal_places=2)
    hire_date = serializers.DateField()
    probation_end_date = serializers.DateField(required=False)

    def validate_department_code(self, value):
        try:
            Department.objects.get(code=value, is_active=True)
        except Department.DoesNotExist:
            raise serializers.ValidationError(
                f"Department with code '{value}' does not exist."
            )
        return value

    def validate_email(self, value):
        if CustomUser.objects.filter(email=value).exists():
            raise serializers.ValidationError("User with this email already exists.")
        return value


class EmployeeSummarySerializer(serializers.Serializer):
    total_employees = serializers.IntegerField()
    active_employees = serializers.IntegerField()
    on_probation = serializers.IntegerField()
    confirmed_employees = serializers.IntegerField()
    by_department = serializers.DictField()
    by_grade_level = serializers.DictField()
    by_employment_status = serializers.DictField()
    average_salary = serializers.DecimalField(max_digits=12, decimal_places=2)
    salary_range = serializers.DictField()


class ContractSummarySerializer(serializers.Serializer):
    total_contracts = serializers.IntegerField()
    active_contracts = serializers.IntegerField()
    expiring_soon = serializers.IntegerField()
    expired_contracts = serializers.IntegerField()
    by_contract_type = serializers.DictField()
    by_status = serializers.DictField()
