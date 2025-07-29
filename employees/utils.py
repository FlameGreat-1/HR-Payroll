from django.db.models import Q, Count, Avg, Sum
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from accounts.models import CustomUser, Department, SystemConfiguration
from .models import EmployeeProfile, Education, Contract
from decimal import Decimal
import csv
import io
import xlsxwriter
from datetime import date, timedelta
import re


class EmployeeUtils:

    @staticmethod
    def generate_employee_id(role_name=None):
        if role_name == "SUPER_ADMIN":
            prefix = "ADMIN"
        elif role_name == "HR_MANAGER":
            prefix = "HR"
        elif role_name == "HR_ADMIN":
            prefix = "HRADMIN"
        elif role_name == "DEPARTMENT_MANAGER":
            prefix = "DEPTMGR"
        elif role_name == "PAYROLL_MANAGER":
            prefix = "PAYMGR"
        elif role_name == "ACCOUNTANT":
            prefix = "ACC"
        elif role_name == "AUDITOR":
            prefix = "AUD"
        else:
            prefix = "EMP"

        existing_ids = EmployeeProfile.objects.filter(
            employee_id__startswith=prefix
        ).values_list("employee_id", flat=True)

        numbers = []
        for emp_id in existing_ids:
            try:
                number = int(emp_id.replace(prefix, ""))
                numbers.append(number)
            except ValueError:
                continue

        next_number = max(numbers) + 1 if numbers else 1
        return f"{prefix}{next_number:04d}"

    @staticmethod
    def calculate_years_of_service(hire_date):
        if not hire_date:
            return 0
        today = timezone.now().date()
        return round((today - hire_date).days / 365.25, 1)

    @staticmethod
    def is_probation_ending_soon(employee_profile, days=7):
        if employee_profile.employment_status != "PROBATION":
            return False
        if not employee_profile.probation_end_date:
            return False

        days_until_end = (
            employee_profile.probation_end_date - timezone.now().date()
        ).days
        return 0 <= days_until_end <= days

    @staticmethod
    def get_employees_by_department(department_id=None):
        if department_id:
            return EmployeeProfile.objects.filter(
                user__department_id=department_id, is_active=True
            ).select_related("user", "user__department")
        return EmployeeProfile.objects.filter(is_active=True).select_related(
            "user", "user__department"
        )

    @staticmethod
    def get_employee_summary_stats():
        total_employees = EmployeeProfile.objects.filter(is_active=True).count()

        by_status = (
            EmployeeProfile.objects.filter(is_active=True)
            .values("employment_status")
            .annotate(count=Count("id"))
        )

        by_department = (
            EmployeeProfile.objects.filter(is_active=True)
            .values("user__department__name")
            .annotate(count=Count("id"))
        )

        by_grade = (
            EmployeeProfile.objects.filter(is_active=True)
            .values("grade_level")
            .annotate(count=Count("id"))
        )

        salary_stats = EmployeeProfile.objects.filter(is_active=True).aggregate(
            avg_salary=Avg("basic_salary"),
            min_salary=Sum("basic_salary"),
            max_salary=Sum("basic_salary"),
        )

        return {
            "total_employees": total_employees,
            "by_employment_status": {
                item["employment_status"]: item["count"] for item in by_status
            },
            "by_department": {
                item["user__department__name"] or "No Department": item["count"]
                for item in by_department
            },
            "by_grade_level": {item["grade_level"]: item["count"] for item in by_grade},
            "salary_stats": salary_stats,
        }

    @staticmethod
    def search_employees(
        query, department=None, employment_status=None, grade_level=None
    ):
        queryset = EmployeeProfile.objects.filter(is_active=True)

        if query:
            queryset = queryset.filter(
                Q(employee_id__icontains=query)
                | Q(user__first_name__icontains=query)
                | Q(user__last_name__icontains=query)
                | Q(user__email__icontains=query)
                | Q(user__employee_code__icontains=query)
            )

        if department:
            queryset = queryset.filter(user__department=department)

        if employment_status:
            queryset = queryset.filter(employment_status=employment_status)

        if grade_level:
            queryset = queryset.filter(grade_level=grade_level)

        return queryset.select_related("user", "user__department")


class ContractUtils:

    @staticmethod
    def generate_contract_number():
        year = timezone.now().year
        prefix = f"CON{year}"

        existing_numbers = Contract.objects.filter(
            contract_number__startswith=prefix
        ).values_list("contract_number", flat=True)

        numbers = []
        for contract_num in existing_numbers:
            try:
                number = int(contract_num.replace(prefix, ""))
                numbers.append(number)
            except ValueError:
                continue

        next_number = max(numbers) + 1 if numbers else 1
        return f"{prefix}{next_number:04d}"

    @staticmethod
    def check_contract_overlap(
        employee, start_date, end_date, exclude_contract_id=None
    ):
        overlapping_contracts = Contract.objects.filter(
            employee=employee,
            status="ACTIVE",
            start_date__lte=end_date or date(2099, 12, 31),
            end_date__gte=start_date,
        )

        if exclude_contract_id:
            overlapping_contracts = overlapping_contracts.exclude(
                pk=exclude_contract_id
            )

        return overlapping_contracts.exists()

    @staticmethod
    def get_expiring_contracts(days=30):
        expiry_date = timezone.now().date() + timedelta(days=days)
        return Contract.objects.filter(
            status="ACTIVE",
            end_date__lte=expiry_date,
            end_date__gte=timezone.now().date(),
            is_active=True,
        ).select_related("employee", "department")

    @staticmethod
    def get_contract_summary_stats():
        total_contracts = Contract.objects.filter(is_active=True).count()

        by_type = (
            Contract.objects.filter(is_active=True)
            .values("contract_type")
            .annotate(count=Count("id"))
        )

        by_status = (
            Contract.objects.filter(is_active=True)
            .values("status")
            .annotate(count=Count("id"))
        )

        expiring_soon = ContractUtils.get_expiring_contracts(30).count()
        expired = Contract.objects.filter(
            end_date__lt=timezone.now().date(), status="ACTIVE"
        ).count()

        return {
            "total_contracts": total_contracts,
            "by_contract_type": {
                item["contract_type"]: item["count"] for item in by_type
            },
            "by_status": {item["status"]: item["count"] for item in by_status},
            "expiring_soon": expiring_soon,
            "expired": expired,
        }


class ValidationUtils:

    @staticmethod
    def validate_phone_number(phone_number):
        pattern = r"^\+?[1-9]\d{1,14}$"
        return re.match(pattern, phone_number) is not None

    @staticmethod
    def validate_employee_code(employee_code):
        pattern = r"^[A-Z0-9]{3,20}$"
        return re.match(pattern, employee_code) is not None

    @staticmethod
    def validate_bank_account(account_number):
        pattern = r"^[0-9]{8,20}$"
        return re.match(pattern, account_number) is not None

    @staticmethod
    def validate_salary_amount(amount):
        try:
            decimal_amount = Decimal(str(amount))
            return decimal_amount > 0 and decimal_amount <= Decimal("10000000.00")
        except:
            return False

    @staticmethod
    def validate_date_range(start_date, end_date):
        if not start_date or not end_date:
            return True
        return end_date > start_date


class ExportUtils:

    @staticmethod
    def export_employees_to_csv(queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="employees.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "Employee ID",
                "Full Name",
                "Email",
                "Phone",
                "Department",
                "Employment Status",
                "Grade Level",
                "Basic Salary",
                "Hire Date",
                "Probation End Date",
                "Confirmation Date",
                "Years of Service",
            ]
        )

        for emp in queryset:
            writer.writerow(
                [
                    emp.employee_id,
                    emp.user.get_full_name(),
                    emp.user.email,
                    emp.user.phone_number or "",
                    emp.user.department.name if emp.user.department else "",
                    emp.get_employment_status_display(),
                    emp.get_grade_level_display(),
                    emp.basic_salary,
                    emp.user.hire_date,
                    emp.probation_end_date,
                    emp.confirmation_date,
                    emp.years_of_service,
                ]
            )

        return response

    @staticmethod
    def export_employees_to_excel(queryset):
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output)
        worksheet = workbook.add_worksheet("Employees")

        header_format = workbook.add_format(
            {"bold": True, "bg_color": "#D7E4BC", "border": 1}
        )

        headers = [
            "Employee ID",
            "Full Name",
            "Email",
            "Phone",
            "Department",
            "Employment Status",
            "Grade Level",
            "Basic Salary",
            "Hire Date",
            "Probation End Date",
            "Confirmation Date",
            "Years of Service",
        ]

        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)

        for row, emp in enumerate(queryset, 1):
            worksheet.write(row, 0, emp.employee_id)
            worksheet.write(row, 1, emp.user.get_full_name())
            worksheet.write(row, 2, emp.user.email)
            worksheet.write(row, 3, emp.user.phone_number or "")
            worksheet.write(
                row, 4, emp.user.department.name if emp.user.department else ""
            )
            worksheet.write(row, 5, emp.get_employment_status_display())
            worksheet.write(row, 6, emp.get_grade_level_display())
            worksheet.write(row, 7, float(emp.basic_salary))
            worksheet.write(
                row, 8, str(emp.user.hire_date) if emp.user.hire_date else ""
            )
            worksheet.write(
                row, 9, str(emp.probation_end_date) if emp.probation_end_date else ""
            )
            worksheet.write(
                row, 10, str(emp.confirmation_date) if emp.confirmation_date else ""
            )
            worksheet.write(row, 11, emp.years_of_service)

        workbook.close()
        output.seek(0)

        response = HttpResponse(
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="employees.xlsx"'
        return response

    @staticmethod
    def export_contracts_to_csv(queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="contracts.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "Contract Number",
                "Employee Name",
                "Contract Type",
                "Status",
                "Start Date",
                "End Date",
                "Basic Salary",
                "Job Title",
                "Department",
                "Days Remaining",
            ]
        )

        for contract in queryset:
            writer.writerow(
                [
                    contract.contract_number,
                    contract.employee.get_full_name(),
                    contract.get_contract_type_display(),
                    contract.get_status_display(),
                    contract.start_date,
                    contract.end_date,
                    contract.basic_salary,
                    contract.job_title,
                    contract.department.name if contract.department else "",
                    contract.days_remaining if contract.days_remaining else "Permanent",
                ]
            )

        return response


class ImportUtils:

    @staticmethod
    def validate_csv_headers(headers, required_headers):
        missing_headers = []
        for header in required_headers:
            if header not in headers:
                missing_headers.append(header)
        return missing_headers

    @staticmethod
    def process_employee_csv(csv_file, update_existing=False):
        required_headers = [
            "first_name",
            "last_name",
            "email",
            "department_code",
            "employment_status",
            "grade_level",
            "basic_salary",
            "hire_date",
        ]

        results = {"success": 0, "errors": 0, "error_details": []}

        try:
            decoded_file = csv_file.read().decode("utf-8")
            csv_data = csv.DictReader(io.StringIO(decoded_file))

            headers = csv_data.fieldnames
            missing_headers = ImportUtils.validate_csv_headers(
                headers, required_headers
            )

            if missing_headers:
                results["error_details"].append(
                    f"Missing required headers: {', '.join(missing_headers)}"
                )
                return results

            for row_num, row in enumerate(csv_data, 2):
                try:
                    department = Department.objects.get(
                        code=row["department_code"], is_active=True
                    )

                    user_data = {
                        "first_name": row["first_name"],
                        "last_name": row["last_name"],
                        "email": row["email"],
                        "department": department,
                        "hire_date": row["hire_date"],
                    }

                    if update_existing and "employee_id" in row:
                        try:
                            profile = EmployeeProfile.objects.get(
                                employee_id=row["employee_id"]
                            )
                            for key, value in user_data.items():
                                setattr(profile.user, key, value)
                            profile.user.save()

                            profile.employment_status = row["employment_status"]
                            profile.grade_level = row["grade_level"]
                            profile.basic_salary = Decimal(row["basic_salary"])
                            profile.save()

                            results["success"] += 1
                            continue
                        except EmployeeProfile.DoesNotExist:
                            pass

                    user = CustomUser.objects.create_user(
                        employee_code=EmployeeUtils.generate_employee_id(), **user_data
                    )

                    EmployeeProfile.objects.create(
                        user=user,
                        employment_status=row["employment_status"],
                        grade_level=row["grade_level"],
                        basic_salary=Decimal(row["basic_salary"]),
                    )

                    results["success"] += 1

                except Exception as e:
                    results["errors"] += 1
                    results["error_details"].append(f"Row {row_num}: {str(e)}")

        except Exception as e:
            results["error_details"].append(f"File processing error: {str(e)}")

        return results


class ReportUtils:

    @staticmethod
    def generate_employee_report(filters=None):
        queryset = EmployeeProfile.objects.filter(is_active=True)

        if filters:
            if filters.get("department"):
                queryset = queryset.filter(user__department=filters["department"])
            if filters.get("employment_status"):
                queryset = queryset.filter(
                    employment_status=filters["employment_status"]
                )
            if filters.get("grade_level"):
                queryset = queryset.filter(grade_level=filters["grade_level"])

        return queryset.select_related("user", "user__department")

    @staticmethod
    def generate_probation_report():
        today = timezone.now().date()
        next_month = today + timedelta(days=30)

        return EmployeeProfile.objects.filter(
            employment_status="PROBATION",
            probation_end_date__lte=next_month,
            probation_end_date__gte=today,
            is_active=True,
        ).select_related("user", "user__department")

    @staticmethod
    def generate_contract_expiry_report(days=30):
        return ContractUtils.get_expiring_contracts(days)

    @staticmethod
    def generate_salary_analysis_report():
        profiles = EmployeeProfile.objects.filter(is_active=True)

        by_department = profiles.values("user__department__name").annotate(
            avg_salary=Avg("basic_salary"),
            min_salary=Sum("basic_salary"),
            max_salary=Sum("basic_salary"),
            count=Count("id"),
        )

        by_grade = profiles.values("grade_level").annotate(
            avg_salary=Avg("basic_salary"),
            min_salary=Sum("basic_salary"),
            max_salary=Sum("basic_salary"),
            count=Count("id"),
        )

        return {
            "by_department": list(by_department),
            "by_grade_level": list(by_grade),
            "overall_stats": profiles.aggregate(
                total_employees=Count("id"),
                avg_salary=Avg("basic_salary"),
                total_salary_cost=Sum("basic_salary"),
            ),
        }


class NotificationUtils:

    @staticmethod
    def get_probation_notifications():
        today = timezone.now().date()
        reminder_days = SystemConfiguration.get_int_setting(
            "PROBATION_REMINDER_DAYS", 7
        )
        reminder_date = today + timedelta(days=reminder_days)

        return EmployeeProfile.objects.filter(
            employment_status="PROBATION",
            probation_end_date=reminder_date,
            is_active=True,
        ).select_related("user", "user__manager")

    @staticmethod
    def get_contract_expiry_notifications():
        today = timezone.now().date()
        reminder_days = SystemConfiguration.get_int_setting(
            "CONTRACT_EXPIRY_REMINDER_DAYS", 30
        )
        reminder_date = today + timedelta(days=reminder_days)

        return Contract.objects.filter(
            status="ACTIVE", end_date=reminder_date, is_active=True
        ).select_related("employee", "department")

    @staticmethod
    def get_birthday_notifications():
        today = timezone.now().date()
        return CustomUser.objects.filter(
            date_of_birth__month=today.month,
            date_of_birth__day=today.day,
            is_active=True,
        ).select_related("employee_profile")


class BulkOperationUtils:

    @staticmethod
    def bulk_update_salaries(employee_ids, percentage_increase):
        updated_count = 0
        for emp_id in employee_ids:
            try:
                profile = EmployeeProfile.objects.get(id=emp_id, is_active=True)
                new_salary = profile.basic_salary * (1 + percentage_increase / 100)
                profile.basic_salary = new_salary
                profile.save()
                updated_count += 1
            except EmployeeProfile.DoesNotExist:
                continue
        return updated_count

    @staticmethod
    def bulk_confirm_employees(employee_ids):
        updated_count = EmployeeProfile.objects.filter(
            id__in=employee_ids, employment_status="PROBATION"
        ).update(employment_status="CONFIRMED", confirmation_date=timezone.now().date())
        return updated_count

    @staticmethod
    def bulk_deactivate_employees(employee_ids):
        updated_count = 0
        for emp_id in employee_ids:
            try:
                profile = EmployeeProfile.objects.get(id=emp_id)
                profile.soft_delete()
                updated_count += 1
            except EmployeeProfile.DoesNotExist:
                continue
        return updated_count


class CalculationUtils:

    @staticmethod
    def calculate_probation_days_remaining(probation_end_date):
        if not probation_end_date:
            return None
        today = timezone.now().date()
        if probation_end_date <= today:
            return 0
        return (probation_end_date - today).days

    @staticmethod
    def calculate_contract_duration(start_date, end_date):
        if not start_date:
            return None
        if not end_date:
            return None
        return (end_date - start_date).days

    @staticmethod
    def calculate_age(birth_date):
        if not birth_date:
            return None
        today = timezone.now().date()
        return (
            today.year
            - birth_date.year
            - ((today.month, today.day) < (birth_date.month, birth_date.day))
        )

    @staticmethod
    def calculate_service_years_months(hire_date):
        if not hire_date:
            return {"years": 0, "months": 0}

        today = timezone.now().date()
        years = today.year - hire_date.year
        months = today.month - hire_date.month

        if months < 0:
            years -= 1
            months += 12

        return {"years": years, "months": months}
