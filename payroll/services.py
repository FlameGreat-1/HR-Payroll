from django.db import transaction, models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import Q, Sum, Count, Avg, F
from accounts.models import CustomUser, Role, Department, SystemConfiguration
from employees.models import EmployeeProfile, Contract
from attendance.models import MonthlyAttendanceSummary, Attendance, LeaveRequest
from attendance.utils import EmployeeDataManager, MonthlyCalculator, TimeCalculator
from .models import (
    PayrollPeriod,
    Payslip,
    SalaryAdvance,
    PayrollDepartmentSummary,
    PayrollConfiguration,
    PayrollBankTransfer,
    PayrollAuditLog,
)
from .utils import (
    PayrollCalculator,
    PayrollDeductionCalculator,
    PayrollTaxCalculator,
    PayrollAdvanceCalculator,
    PayrollDataProcessor,
    PayrollValidationHelper,
    PayrollUtilityHelper,
    PayrollCacheManager,
    log_payroll_activity,
)
from .permissions import PayrollAccessControl
from decimal import Decimal
from datetime import date, datetime
from typing import Dict, List, Tuple, Optional, Any
import logging

logger = logging.getLogger(__name__)


class PayrollPeriodService:
    @staticmethod
    def create_payroll_period(year: int, month: int, user: CustomUser) -> PayrollPeriod:
        if not PayrollAccessControl.can_process_payroll(user):
            raise ValidationError("You don't have permission to create payroll periods")

        is_valid, message = PayrollDataProcessor.validate_payroll_period(year, month)
        if not is_valid:
            raise ValidationError(message)

        if PayrollPeriod.objects.filter(year=year, month=month).exists():
            raise ValidationError(
                f"Payroll period for {year}-{month:02d} already exists"
            )

        try:
            with transaction.atomic():
                period = PayrollPeriod.objects.create(
                    year=year, month=month, created_by=user
                )
                eligible_employees = PayrollPeriodService._get_eligible_employees(
                    year, month
                )

                created_payslips = []
                for employee in eligible_employees:
                    payslip = Payslip.objects.create(
                        payroll_period=period, employee=employee
                    )
                    created_payslips.append(payslip)

                log_payroll_activity(
                    user,
                    "PAYROLL_PERIOD_CREATED",
                    {
                        "period_id": str(period.id),
                        "year": year,
                        "month": month,
                        "eligible_employees": len(created_payslips),
                    },
                )

                return period

        except Exception as e:
            logger.error(f"Error creating payroll period: {str(e)}")
            raise ValidationError(f"Failed to create payroll period: {str(e)}")

    @staticmethod
    def _get_eligible_employees(year: int, month: int) -> List[CustomUser]:
        period_start = date(year, month, 1)
        period_end = PayrollDataProcessor.get_payroll_month_dates(year, month)[
            "month_end"
        ]

        return (
            CustomUser.active.filter(status="ACTIVE", hire_date__lte=period_end)
            .exclude(
                Q(termination_date__lt=period_start) & Q(termination_date__isnull=False)
            )
            .select_related("role", "department", "employee_profile")
        )

    @staticmethod
    def start_processing(period_id: str, user: CustomUser) -> PayrollPeriod:
        if not PayrollAccessControl.can_process_payroll(user):
            raise ValidationError("You don't have permission to process payroll")

        try:
            with transaction.atomic():
                period = PayrollPeriod.objects.select_for_update().get(id=period_id)
                if not period.can_be_processed():
                    raise ValidationError(
                        f"Cannot process payroll in {period.status} status"
                    )

                period.mark_as_processing(user)
                return period

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error starting payroll processing: {str(e)}")
            raise ValidationError(f"Failed to start processing: {str(e)}")

    @staticmethod
    def complete_processing(period_id: str, user: CustomUser) -> PayrollPeriod:
        if not PayrollAccessControl.can_process_payroll(user):
            raise ValidationError(
                "You don't have permission to complete payroll processing"
            )

        try:
            with transaction.atomic():
                period = PayrollPeriod.objects.select_for_update().get(id=period_id)
                if period.status != "PROCESSING":
                    raise ValidationError(
                        "Can only complete payroll that is being processed"
                    )

                incomplete_payslips = period.payslips.exclude(
                    status__in=["CALCULATED", "APPROVED"]
                )
                if incomplete_payslips.exists():
                    raise ValidationError(
                        f"{incomplete_payslips.count()} payslips are not calculated yet"
                    )

                period.mark_as_completed(user)
                PayrollPeriodService._generate_department_summaries(period)
                return period

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error completing payroll processing: {str(e)}")
            raise ValidationError(f"Failed to complete processing: {str(e)}")

    @staticmethod
    def _generate_department_summaries(period: PayrollPeriod):
        departments = Department.objects.filter(
            employees__payslips__payroll_period=period
        ).distinct()
        for department in departments:
            summary, created = PayrollDepartmentSummary.objects.get_or_create(
                payroll_period=period, department=department
            )
            summary.calculate_summary()

    @staticmethod
    def approve_payroll(period_id: str, user: CustomUser) -> PayrollPeriod:
        if not PayrollAccessControl.can_approve_payroll(user):
            raise ValidationError("You don't have permission to approve payroll")

        try:
            with transaction.atomic():
                period = PayrollPeriod.objects.select_for_update().get(id=period_id)
                if not period.can_be_approved():
                    raise ValidationError(
                        f"Cannot approve payroll in {period.status} status"
                    )

                unapproved_payslips = period.payslips.exclude(status="APPROVED")
                if unapproved_payslips.exists():
                    raise ValidationError(
                        f"{unapproved_payslips.count()} payslips are not approved yet"
                    )

                period.approve(user)
                return period

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error approving payroll: {str(e)}")
            raise ValidationError(f"Failed to approve payroll: {str(e)}")


class PayslipCalculationService:
    @staticmethod
    def calculate_single_payslip(payslip_id: str, user: CustomUser) -> Payslip:
        if not PayrollAccessControl.can_process_payroll(user):
            raise ValidationError("You don't have permission to calculate payslips")

        try:
            with transaction.atomic():
                payslip = Payslip.objects.select_for_update().get(id=payslip_id)
                if payslip.status not in ["DRAFT"]:
                    raise ValidationError(
                        f"Cannot calculate payslip in {payslip.status} status"
                    )

                PayslipCalculationService._perform_calculation(payslip, user)
                return payslip

        except Payslip.DoesNotExist:
            raise ValidationError("Payslip not found")
        except Exception as e:
            logger.error(f"Error calculating payslip: {str(e)}")
            raise ValidationError(f"Failed to calculate payslip: {str(e)}")

    @staticmethod
    def _perform_calculation(payslip: Payslip, user: CustomUser):
        monthly_summary = PayrollDataProcessor.get_employee_monthly_summary(
            payslip.employee, payslip.payroll_period.year, payslip.payroll_period.month
        )

        if not monthly_summary:
            raise ValidationError(
                f"Monthly attendance summary not found for {payslip.employee.employee_code}"
            )

        payslip.monthly_summary = monthly_summary
        payslip.calculate_payroll()
        payslip.calculated_by = user

        log_payroll_activity(
            user,
            "PAYSLIP_CALCULATED",
            {
                "payslip_id": str(payslip.id),
                "employee_code": payslip.employee.employee_code,
                "gross_salary": float(payslip.gross_salary),
                "net_salary": float(payslip.net_salary),
            },
        )

    @staticmethod
    def bulk_calculate_payslips(
        period_id: str, user: CustomUser, employee_ids: List[str] = None
    ) -> Dict[str, Any]:
        if not PayrollAccessControl.can_process_payroll(user):
            raise ValidationError("You don't have permission to calculate payslips")

        try:
            period = PayrollPeriod.objects.get(id=period_id)
            if period.status not in ["DRAFT", "PROCESSING"]:
                raise ValidationError(
                    "Cannot calculate payslips for completed payroll period"
                )

            payslips_query = period.payslips.filter(status="DRAFT")
            if employee_ids:
                payslips_query = payslips_query.filter(employee__id__in=employee_ids)

            results = {"successful": [], "failed": []}

            for payslip in payslips_query:
                try:
                    PayslipCalculationService._perform_calculation(payslip, user)
                    results["successful"].append(payslip.employee.employee_code)
                except Exception as e:
                    results["failed"].append(
                        {
                            "employee_code": payslip.employee.employee_code,
                            "error": str(e),
                        }
                    )

            return results

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error in bulk calculation: {str(e)}")
            raise ValidationError(f"Failed to calculate payslips: {str(e)}")

class SalaryAdvanceService:
    @staticmethod
    def request_advance(employee_id: str, amount: Decimal, advance_type: str, reason: str, 
                       installments: int, user: CustomUser) -> SalaryAdvance:
        if not PayrollAccessControl.can_manage_salary_advance(user):
            raise ValidationError("You don't have permission to manage salary advances")
        
        try:
            employee = CustomUser.objects.get(id=employee_id)
            
            advance_data = PayrollAdvanceCalculator.calculate_available_advance_amount(employee)
            if amount > advance_data['available_amount']:
                raise ValidationError(f"Requested amount exceeds available limit of LKR {advance_data['available_amount']}")
            
            max_advances_per_year = SystemConfiguration.get_int_setting("MAX_ADVANCES_PER_YEAR", 10)
            if advance_data['advance_count_this_year'] >= max_advances_per_year:
                raise ValidationError(f"Maximum {max_advances_per_year} advances per year exceeded")
            
            with transaction.atomic():
                advance = SalaryAdvance.objects.create(
                    employee=employee,
                    amount=amount,
                    advance_type=advance_type,
                    reason=reason,
                    installments=installments,
                    requested_by=user,
                    employee_basic_salary=advance_data['basic_salary']
                )
                
                log_payroll_activity(user, 'SALARY_ADVANCE_REQUESTED', {
                    'advance_id': str(advance.id),
                    'employee_code': employee.employee_code,
                    'amount': float(amount),
                    'advance_type': advance_type
                })
                
                return advance
                
        except CustomUser.DoesNotExist:
            raise ValidationError("Employee not found")
        except Exception as e:
            logger.error(f"Error requesting salary advance: {str(e)}")
            raise ValidationError(f"Failed to request advance: {str(e)}")
    
    @staticmethod
    def approve_advance(advance_id: str, user: CustomUser) -> SalaryAdvance:
        if not PayrollAccessControl.can_manage_salary_advance(user):
            raise ValidationError("You don't have permission to approve salary advances")
        
        try:
            with transaction.atomic():
                advance = SalaryAdvance.objects.select_for_update().get(id=advance_id)
                if advance.status != 'PENDING':
                    raise ValidationError("Can only approve pending advances")
                
                advance.approve(user)
                
                SalaryAdvanceService._invalidate_employee_payroll_cache(advance.employee)
                
                return advance
                
        except SalaryAdvance.DoesNotExist:
            raise ValidationError("Salary advance not found")
        except Exception as e:
            logger.error(f"Error approving salary advance: {str(e)}")
            raise ValidationError(f"Failed to approve advance: {str(e)}")
    
    @staticmethod
    def activate_advance(advance_id: str, user: CustomUser) -> SalaryAdvance:
        if not PayrollAccessControl.can_manage_salary_advance(user):
            raise ValidationError("You don't have permission to activate salary advances")
        
        try:
            with transaction.atomic():
                advance = SalaryAdvance.objects.select_for_update().get(id=advance_id)
                if advance.status != 'APPROVED':
                    raise ValidationError("Can only activate approved advances")
                
                advance.activate(user)
                
                SalaryAdvanceService._invalidate_employee_payroll_cache(advance.employee)
                
                return advance
                
        except SalaryAdvance.DoesNotExist:
            raise ValidationError("Salary advance not found")
        except Exception as e:
            logger.error(f"Error activating salary advance: {str(e)}")
            raise ValidationError(f"Failed to activate advance: {str(e)}")
    
    @staticmethod
    def _invalidate_employee_payroll_cache(employee: CustomUser):
        current_year, current_month = PayrollUtilityHelper.get_next_payroll_period()
        PayrollCacheManager.invalidate_payroll_cache(employee.id, current_year, current_month)
        
        current_payslips = Payslip.objects.filter(
            employee=employee,
            status__in=['DRAFT', 'CALCULATED'],
            payroll_period__status__in=['DRAFT', 'PROCESSING']
        )
        
        for payslip in current_payslips:
            if payslip.status == 'CALCULATED':
                payslip.status = 'DRAFT'
                payslip.save(update_fields=['status'])
    
    @staticmethod
    def get_employee_advance_summary(employee_id: str, user: CustomUser) -> Dict[str, Any]:
        if not PayrollAccessControl.can_view_payroll(user):
            raise ValidationError("You don't have permission to view advance information")
        
        try:
            employee = CustomUser.objects.get(id=employee_id)
            
            if not PayrollAccessControl.can_view_payroll(user, employee):
                raise ValidationError("You don't have permission to view this employee's advance information")
            
            advance_data = PayrollAdvanceCalculator.calculate_available_advance_amount(employee)
            active_advances = PayrollAdvanceCalculator.get_current_advances(employee, timezone.now().year)
            
            return {
                'employee_info': {
                    'employee_code': employee.employee_code,
                    'name': employee.get_full_name(),
                    'basic_salary': float(advance_data['basic_salary'])
                },
                'advance_limits': {
                    'max_percentage': float(advance_data['max_percentage']),
                    'max_advance_amount': float(advance_data['max_advance_amount']),
                    'available_amount': float(advance_data['available_amount']),
                    'current_outstanding': float(advance_data['current_outstanding'])
                },
                'active_advances': active_advances,
                'advance_history': SalaryAdvanceService._get_advance_history(employee)
            }
            
        except CustomUser.DoesNotExist:
            raise ValidationError("Employee not found")
        except Exception as e:
            logger.error(f"Error getting advance summary: {str(e)}")
            raise ValidationError(f"Failed to get advance summary: {str(e)}")
    
    @staticmethod
    def _get_advance_history(employee: CustomUser) -> List[Dict[str, Any]]:
        advances = SalaryAdvance.objects.filter(employee=employee).order_by('-created_at')[:10]
        
        return [{
            'id': str(advance.id),
            'amount': float(advance.amount),
            'advance_type': advance.advance_type,
            'status': advance.status,
            'requested_date': advance.requested_date.isoformat(),
            'outstanding_amount': float(advance.outstanding_amount)
        } for advance in advances]


class EmployeePayrollService:
    @staticmethod
    def get_employee_payroll_history(employee_id: str, user: CustomUser, year: int = None) -> Dict[str, Any]:
        if not PayrollAccessControl.can_view_payroll(user):
            raise ValidationError("You don't have permission to view payroll information")

        try:
            employee = CustomUser.objects.get(id=employee_id)

            if not PayrollAccessControl.can_view_payroll(user, employee):
                raise ValidationError("You don't have permission to view this employee's payroll")

            payslips_query = Payslip.objects.filter(employee=employee)
            if year:
                payslips_query = payslips_query.filter(payroll_period__year=year)

            payslips = payslips_query.select_related('payroll_period').order_by('-payroll_period__year', '-payroll_period__month')

            return {
                'employee_info': {
                    'employee_code': employee.employee_code,
                    'name': employee.get_full_name(),
                    'department': employee.department.name if employee.department else '',
                    'role': employee.role.name if employee.role else ''
                },
                'payroll_summary': EmployeePayrollService._calculate_payroll_summary(payslips),
                'monthly_payslips': EmployeePayrollService._format_payslips(payslips)
            }

        except CustomUser.DoesNotExist:
            raise ValidationError("Employee not found")
        except Exception as e:
            logger.error(f"Error getting employee payroll history: {str(e)}")
            raise ValidationError(f"Failed to get payroll history: {str(e)}")

    @staticmethod
    def _calculate_payroll_summary(payslips) -> Dict[str, Any]:
        if not payslips:
            return {'total_months': 0, 'total_gross': 0, 'total_net': 0, 'average_gross': 0, 'average_net': 0}

        total_gross = sum(p.gross_salary for p in payslips)
        total_net = sum(p.net_salary for p in payslips)
        count = len(payslips)

        return {
            'total_months': count,
            'total_gross': float(total_gross),
            'total_net': float(total_net),
            'average_gross': float(total_gross / count),
            'average_net': float(total_net / count)
        }

    @staticmethod
    def _format_payslips(payslips) -> List[Dict[str, Any]]:
        return [{
            'id': str(payslip.id),
            'year': payslip.payroll_period.year,
            'month': payslip.payroll_period.month,
            'status': payslip.status,
            'basic_salary': float(payslip.basic_salary),
            'gross_salary': float(payslip.gross_salary),
            'total_deductions': float(payslip.total_deductions),
            'net_salary': float(payslip.net_salary),
            'working_days': payslip.working_days,
            'attended_days': payslip.attended_days
        } for payslip in payslips]

    @staticmethod
    def get_current_month_payslip(employee_id: str, user: CustomUser) -> Optional[Dict[str, Any]]:
        if not PayrollAccessControl.can_view_payroll(user):
            raise ValidationError("You don't have permission to view payroll information")

        try:
            employee = CustomUser.objects.get(id=employee_id)

            if not PayrollAccessControl.can_view_payroll(user, employee):
                raise ValidationError("You don't have permission to view this employee's payroll")

            current_year, current_month = PayrollUtilityHelper.get_next_payroll_period()

            try:
                payslip = Payslip.objects.get(
                    employee=employee,
                    payroll_period__year=current_year,
                    payroll_period__month=current_month
                )

                return {
                    'payslip_id': str(payslip.id),
                    'status': payslip.status,
                    'basic_salary': float(payslip.basic_salary),
                    'gross_salary': float(payslip.gross_salary),
                    'net_salary': float(payslip.net_salary),
                    'calculation_progress': EmployeePayrollService._get_calculation_progress(payslip)
                }

            except Payslip.DoesNotExist:
                return None

        except CustomUser.DoesNotExist:
            raise ValidationError("Employee not found")
        except Exception as e:
            logger.error(f"Error getting current month payslip: {str(e)}")
            raise ValidationError(f"Failed to get current payslip: {str(e)}")

    @staticmethod
    def _get_calculation_progress(payslip: Payslip) -> Dict[str, Any]:
        return {
            'has_monthly_summary': payslip.monthly_summary is not None,
            'is_calculated': payslip.status in ['CALCULATED', 'APPROVED'],
            'is_approved': payslip.status == 'APPROVED',
            'calculation_date': payslip.updated_at.isoformat() if payslip.status == 'CALCULATED' else None
        }

    @staticmethod
    def update_employee_payroll_settings(employee_id: str, settings: Dict[str, Any], user: CustomUser) -> CustomUser:
        if not PayrollAccessControl.can_process_payroll(user):
            raise ValidationError("You don't have permission to update payroll settings")

        try:
            with transaction.atomic():
                employee = CustomUser.objects.select_for_update().get(id=employee_id)

                profile = EmployeeDataManager.get_employee_profile(employee)
                if not profile:
                    raise ValidationError("Employee profile not found")

                old_values = {
                    'basic_salary': float(profile.basic_salary),
                    'bank_account': profile.bank_account_number
                }

                if 'basic_salary' in settings:
                    profile.basic_salary = Decimal(str(settings['basic_salary']))

                if 'bank_account_number' in settings:
                    profile.bank_account_number = settings['bank_account_number']

                if 'bank_code' in settings:
                    profile.bank_code = settings['bank_code']

                profile.save()

                EmployeePayrollService._invalidate_employee_caches(employee)

                log_payroll_activity(user, 'EMPLOYEE_PAYROLL_SETTINGS_UPDATED', {
                    'employee_code': employee.employee_code,
                    'old_values': old_values,
                    'new_values': settings
                })

                return employee

        except CustomUser.DoesNotExist:
            raise ValidationError("Employee not found")
        except Exception as e:
            logger.error(f"Error updating employee payroll settings: {str(e)}")
            raise ValidationError(f"Failed to update settings: {str(e)}")

    @staticmethod
    def _invalidate_employee_caches(employee: CustomUser):
        current_year, current_month = PayrollUtilityHelper.get_next_payroll_period()
        PayrollCacheManager.invalidate_payroll_cache(employee.id, current_year, current_month)

        draft_payslips = Payslip.objects.filter(
            employee=employee,
            status__in=['DRAFT', 'CALCULATED'],
            payroll_period__status__in=['DRAFT', 'PROCESSING']
        )

        for payslip in draft_payslips:
            if payslip.status == 'CALCULATED':
                payslip.status = 'DRAFT'
                payslip.save(update_fields=['status'])


class PayrollReportingService:
    @staticmethod
    def generate_monthly_payroll_report(
        period_id: str, user: CustomUser, format_type: str = "EXCEL"
    ) -> Dict[str, Any]:
        if not PayrollAccessControl.can_export_payroll(user):
            raise ValidationError(
                "You don't have permission to generate payroll reports"
            )

        try:
            period = PayrollPeriod.objects.get(id=period_id)
            if period.status not in ["COMPLETED", "APPROVED", "PAID"]:
                raise ValidationError(
                    "Can only generate reports for completed payroll periods"
                )

            payslips = period.payslips.filter(
                status__in=["CALCULATED", "APPROVED"]
            ).select_related(
                "employee",
                "employee__department",
                "employee__role",
                "employee__employee_profile",
            )

            report_data = PayrollReportingService._prepare_report_data(payslips)
            file_content = PayrollReportingService._generate_file_content(
                report_data, period, format_type
            )
            report_record = PayrollReportingService._save_report_file(
                period, format_type, user, file_content
            )

            return {
                "report_id": str(report_record.id),
                "file_path": report_record.file_path,
                "file_size": report_record.file_size,
                "generation_time": report_record.completed_at.isoformat(),
            }

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error generating payroll report: {str(e)}")
            raise ValidationError(f"Failed to generate report: {str(e)}")

    @staticmethod
    def _prepare_report_data(payslips) -> List[Dict[str, Any]]:
        from .utils import PayrollReportDataProcessor

        report_data = []
        for sr_no, payslip in enumerate(payslips, 1):
            payslip_data = PayrollReportDataProcessor.prepare_individual_payslip_data(
                payslip.employee,
                {
                    "sr_no": sr_no,
                    "basic_salary": payslip.basic_salary,
                    "working_days": payslip.working_days,
                    "bonus_1": payslip.bonus_1,
                    "bonus_2": payslip.bonus_2,
                    "epf_salary_base": payslip.epf_salary_base,
                    "transport_allowance": payslip.transport_allowance,
                    "telephone_allowance": payslip.telephone_allowance,
                    "attendance_bonus": payslip.attendance_bonus,
                    "performance_bonus": payslip.performance_bonus,
                    "fuel_allowance": payslip.fuel_allowance,
                    "meal_allowance": payslip.meal_allowance,
                    "regular_overtime": payslip.regular_overtime,
                    "gross_salary": payslip.gross_salary,
                    "leave_deduction": payslip.leave_deduction,
                    "late_penalty": payslip.late_penalty,
                    "epf_deduction": payslip.employee_epf_contribution,
                    "total_deductions": payslip.total_deductions,
                    "net_salary": payslip.net_salary,
                    "fuel_per_day": payslip.fuel_per_day,
                    "meal_per_day": payslip.meal_per_day,
                },
            )
            report_data.append(payslip_data)

        return report_data

    @staticmethod
    def _generate_file_content(
        report_data: List[Dict[str, Any]], period: PayrollPeriod, format_type: str
    ) -> bytes:
        if format_type == "EXCEL":
            from .utils import PayrollExcelProcessor

            return PayrollExcelProcessor.create_payroll_excel(
                report_data, period.year, period.month
            )
        elif format_type == "PDF":
            from .utils import PayrollPDFProcessor

            return PayrollPDFProcessor.create_payroll_summary_pdf(
                report_data, period.year, period.month
            )
        else:
            raise ValidationError("Unsupported format type")

    @staticmethod
    def _save_report_file(
        period: PayrollPeriod, format_type: str, user: CustomUser, file_content: bytes
    ):
        import os
        from django.conf import settings
        from .models import PayrollReport

        file_name = (
            f"payroll_{period.year}_{period.month:02d}_summary.{format_type.lower()}"
        )
        file_path = os.path.join(settings.MEDIA_ROOT, "payroll", "reports", file_name)

        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        with open(file_path, "wb") as f:
            f.write(file_content)

        return PayrollReport.objects.create(
            report_type="MONTHLY_SUMMARY",
            report_format=format_type,
            report_name=f"Payroll Report - {period.period_name}",
            payroll_period=period,
            file_path=file_path,
            file_size=len(file_content),
            generation_status="COMPLETED",
            completed_at=timezone.now(),
            generated_by=user,
        )

    @staticmethod
    def generate_department_summary(
        period_id: str, department_id: str, user: CustomUser
    ) -> Dict[str, Any]:
        if not PayrollAccessControl.can_view_payroll_reports(user):
            raise ValidationError(
                "You don't have permission to generate department reports"
            )

        try:
            period = PayrollPeriod.objects.get(id=period_id)
            department = Department.objects.get(id=department_id)
            summary = PayrollDepartmentSummary.objects.get(
                payroll_period=period, department=department
            )

            department_payslips = period.payslips.filter(
                employee__department=department, status__in=["CALCULATED", "APPROVED"]
            ).select_related("employee", "employee__role")

            return {
                "department_info": {
                    "name": department.name,
                    "employee_count": summary.employee_count,
                    "total_gross": float(summary.total_gross_salary),
                    "total_net": float(summary.total_net_salary),
                    "average_salary": float(summary.average_salary),
                    "budget_utilization": float(summary.budget_utilization_percentage),
                },
                "role_breakdown": summary.role_breakdown,
                "performance_metrics": summary.performance_metrics,
                "employee_details": PayrollReportingService._format_department_employees(
                    department_payslips
                ),
            }

        except (
            PayrollPeriod.DoesNotExist,
            Department.DoesNotExist,
            PayrollDepartmentSummary.DoesNotExist,
        ):
            raise ValidationError("Required data not found")
        except Exception as e:
            logger.error(f"Error generating department summary: {str(e)}")
            raise ValidationError(f"Failed to generate department summary: {str(e)}")

    @staticmethod
    def _format_department_employees(payslips) -> List[Dict[str, Any]]:
        return [
            {
                "employee_code": payslip.employee.employee_code,
                "name": payslip.employee.get_full_name(),
                "role": payslip.employee.role.name if payslip.employee.role else "",
                "basic_salary": float(payslip.basic_salary),
                "gross_salary": float(payslip.gross_salary),
                "net_salary": float(payslip.net_salary),
                "attendance_percentage": (
                    float(payslip.monthly_summary.attendance_percentage)
                    if payslip.monthly_summary
                    else 0
                ),
            }
            for payslip in payslips
        ]

    @staticmethod
    def generate_individual_payslip_pdf(payslip_id: str, user: CustomUser) -> bytes:
        if not PayrollAccessControl.can_view_payroll(user):
            raise ValidationError("You don't have permission to generate payslips")

        try:
            payslip = Payslip.objects.select_related(
                "employee", "employee__employee_profile", "payroll_period"
            ).get(id=payslip_id)

            if not PayrollAccessControl.can_view_payroll(user, payslip.employee):
                raise ValidationError(
                    "You don't have permission to view this employee's payslip"
                )

            if payslip.status not in ["CALCULATED", "APPROVED"]:
                raise ValidationError(
                    "Can only generate PDF for calculated or approved payslips"
                )

            from .utils import PayrollPDFProcessor

            employee_data = PayrollReportingService._prepare_report_data([payslip])[0]

            return PayrollPDFProcessor.create_individual_payslip_pdf(
                employee_data, payslip.payroll_period.year, payslip.payroll_period.month
            )

        except Payslip.DoesNotExist:
            raise ValidationError("Payslip not found")
        except Exception as e:
            logger.error(f"Error generating individual payslip PDF: {str(e)}")
            raise ValidationError(f"Failed to generate payslip PDF: {str(e)}")


class BankTransferService:
    @staticmethod
    def generate_bank_transfer_file(
        period_id: str, user: CustomUser
    ) -> PayrollBankTransfer:
        if not PayrollAccessControl.can_export_payroll(user):
            raise ValidationError(
                "You don't have permission to generate bank transfer files"
            )

        try:
            with transaction.atomic():
                period = PayrollPeriod.objects.select_for_update().get(id=period_id)

                if period.status != "APPROVED":
                    raise ValidationError(
                        "Can only generate bank transfer for approved payroll"
                    )

                if PayrollBankTransfer.objects.filter(
                    payroll_period=period,
                    status__in=["GENERATED", "SENT", "PROCESSED", "COMPLETED"],
                ).exists():
                    raise ValidationError(
                        "Bank transfer file already exists for this period"
                    )

                approved_payslips = period.payslips.filter(
                    status="APPROVED", net_salary__gt=0
                )
                if not approved_payslips.exists():
                    raise ValidationError(
                        "No approved payslips with positive net salary found"
                    )

                bank_transfer = PayrollBankTransfer.objects.create(
                    payroll_period=period, created_by=user
                )
                file_path = bank_transfer.generate_bank_file()

                log_payroll_activity(
                    user,
                    "BANK_TRANSFER_GENERATED",
                    {
                        "transfer_id": str(bank_transfer.id),
                        "period_id": str(period.id),
                        "total_employees": bank_transfer.total_employees,
                        "total_amount": float(bank_transfer.total_amount),
                    },
                )

                return bank_transfer

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error generating bank transfer file: {str(e)}")
            raise ValidationError(f"Failed to generate bank transfer file: {str(e)}")

    @staticmethod
    def get_transfer_status(transfer_id: str, user: CustomUser) -> Dict[str, Any]:
        if not PayrollAccessControl.can_view_payroll_reports(user):
            raise ValidationError(
                "You don't have permission to view bank transfer status"
            )

        try:
            transfer = PayrollBankTransfer.objects.select_related("payroll_period").get(
                id=transfer_id
            )

            return {
                "transfer_id": str(transfer.id),
                "batch_reference": transfer.batch_reference,
                "status": transfer.status,
                "period": {
                    "year": transfer.payroll_period.year,
                    "month": transfer.payroll_period.month,
                    "period_name": transfer.payroll_period.period_name,
                },
                "financial_summary": {
                    "total_employees": transfer.total_employees,
                    "total_amount": float(transfer.total_amount),
                },
                "timestamps": {
                    "created_at": transfer.created_at.isoformat(),
                    "generated_at": (
                        transfer.generated_at.isoformat()
                        if transfer.generated_at
                        else None
                    ),
                    "sent_at": (
                        transfer.sent_at.isoformat() if transfer.sent_at else None
                    ),
                    "processed_at": (
                        transfer.processed_at.isoformat()
                        if transfer.processed_at
                        else None
                    ),
                },
                "file_info": {
                    "file_path": transfer.bank_file_path,
                    "file_format": transfer.bank_file_format,
                },
            }

        except PayrollBankTransfer.DoesNotExist:
            raise ValidationError("Bank transfer not found")
        except Exception as e:
            logger.error(f"Error getting bank transfer status: {str(e)}")
            raise ValidationError(f"Failed to get transfer status: {str(e)}")

    @staticmethod
    def update_transfer_status(
        transfer_id: str,
        new_status: str,
        user: CustomUser,
        bank_response: Dict[str, Any] = None,
    ) -> PayrollBankTransfer:
        if not PayrollAccessControl.can_process_payroll(user):
            raise ValidationError(
                "You don't have permission to update bank transfer status"
            )

        try:
            with transaction.atomic():
                transfer = PayrollBankTransfer.objects.select_for_update().get(
                    id=transfer_id
                )

                valid_transitions = {
                    "GENERATED": ["SENT", "FAILED"],
                    "SENT": ["PROCESSED", "FAILED"],
                    "PROCESSED": ["COMPLETED", "FAILED"],
                }

                if (
                    transfer.status not in valid_transitions
                    or new_status not in valid_transitions[transfer.status]
                ):
                    raise ValidationError(
                        f"Invalid status transition from {transfer.status} to {new_status}"
                    )

                old_status = transfer.status
                transfer.status = new_status

                if new_status == "SENT":
                    transfer.sent_at = timezone.now()
                elif new_status == "PROCESSED":
                    transfer.processed_at = timezone.now()

                if bank_response:
                    transfer.bank_response = bank_response

                transfer.save()

                log_payroll_activity(
                    user,
                    "BANK_TRANSFER_STATUS_UPDATED",
                    {
                        "transfer_id": str(transfer.id),
                        "old_status": old_status,
                        "new_status": new_status,
                        "bank_response": bank_response,
                    },
                )

                return transfer

        except PayrollBankTransfer.DoesNotExist:
            raise ValidationError("Bank transfer not found")
        except Exception as e:
            logger.error(f"Error updating bank transfer status: {str(e)}")
            raise ValidationError(f"Failed to update transfer status: {str(e)}")


class PayrollConfigurationService:
    @staticmethod
    def create_role_configuration(
        role_id: str,
        config_key: str,
        config_value: str,
        value_type: str,
        user: CustomUser,
        description: str = "",
    ) -> PayrollConfiguration:
        if not PayrollAccessControl.can_configure_payroll(user):
            raise ValidationError(
                "You don't have permission to configure payroll settings"
            )

        try:
            role = Role.objects.get(id=role_id)

            if PayrollConfiguration.objects.filter(
                role=role, configuration_key=config_key, is_active=True
            ).exists():
                raise ValidationError(
                    f"Configuration {config_key} already exists for role {role.name}"
                )

            with transaction.atomic():
                config = PayrollConfiguration.objects.create(
                    configuration_type="ALLOWANCE",
                    role=role,
                    configuration_key=config_key,
                    configuration_value=config_value,
                    value_type=value_type,
                    description=description,
                    created_by=user,
                )

                PayrollConfigurationService._invalidate_role_payroll_cache(role)

                log_payroll_activity(
                    user,
                    "PAYROLL_CONFIGURATION_CREATED",
                    {
                        "config_id": str(config.id),
                        "role": role.name,
                        "config_key": config_key,
                        "config_value": config_value,
                    },
                )

                return config

        except Role.DoesNotExist:
            raise ValidationError("Role not found")
        except Exception as e:
            logger.error(f"Error creating role configuration: {str(e)}")
            raise ValidationError(f"Failed to create configuration: {str(e)}")

    @staticmethod
    def update_configuration(
        config_id: str, new_value: str, user: CustomUser
    ) -> PayrollConfiguration:
        if not PayrollAccessControl.can_configure_payroll(user):
            raise ValidationError(
                "You don't have permission to update payroll configurations"
            )

        try:
            with transaction.atomic():
                config = PayrollConfiguration.objects.select_for_update().get(
                    id=config_id
                )
                old_value = config.configuration_value

                config.configuration_value = new_value
                config.updated_by = user
                config.save()

                if config.role:
                    PayrollConfigurationService._invalidate_role_payroll_cache(
                        config.role
                    )
                elif config.department:
                    PayrollConfigurationService._invalidate_department_payroll_cache(
                        config.department
                    )

                log_payroll_activity(
                    user,
                    "PAYROLL_CONFIGURATION_UPDATED",
                    {
                        "config_id": str(config.id),
                        "config_key": config.configuration_key,
                        "old_value": old_value,
                        "new_value": new_value,
                    },
                )

                return config

        except PayrollConfiguration.DoesNotExist:
            raise ValidationError("Configuration not found")
        except Exception as e:
            logger.error(f"Error updating configuration: {str(e)}")
            raise ValidationError(f"Failed to update configuration: {str(e)}")

    @staticmethod
    def _invalidate_role_payroll_cache(role: Role):
        draft_payslips = Payslip.objects.filter(
            employee__role=role,
            status__in=["DRAFT", "CALCULATED"],
            payroll_period__status__in=["DRAFT", "PROCESSING"],
        )

        for payslip in draft_payslips:
            PayrollCacheManager.invalidate_payroll_cache(
                payslip.employee.id,
                payslip.payroll_period.year,
                payslip.payroll_period.month,
            )
            if payslip.status == "CALCULATED":
                payslip.status = "DRAFT"
                payslip.save(update_fields=["status"])

    @staticmethod
    def _invalidate_department_payroll_cache(department: Department):
        draft_payslips = Payslip.objects.filter(
            employee__department=department,
            status__in=["DRAFT", "CALCULATED"],
            payroll_period__status__in=["DRAFT", "PROCESSING"],
        )

        for payslip in draft_payslips:
            PayrollCacheManager.invalidate_payroll_cache(
                payslip.employee.id,
                payslip.payroll_period.year,
                payslip.payroll_period.month,
            )
            if payslip.status == "CALCULATED":
                payslip.status = "DRAFT"
                payslip.save(update_fields=["status"])

    @staticmethod
    def get_role_configurations(role_id: str, user: CustomUser) -> List[Dict[str, Any]]:
        if not PayrollAccessControl.can_view_payroll_reports(user):
            raise ValidationError("You don't have permission to view configurations")

        try:
            role = Role.objects.get(id=role_id)
            configs = PayrollConfiguration.objects.filter(role=role, is_active=True)

            return [
                {
                    "id": str(config.id),
                    "configuration_key": config.configuration_key,
                    "configuration_value": config.configuration_value,
                    "value_type": config.value_type,
                    "description": config.description,
                    "effective_from": config.effective_from.isoformat(),
                    "created_at": config.created_at.isoformat(),
                }
                for config in configs
            ]

        except Role.DoesNotExist:
            raise ValidationError("Role not found")
        except Exception as e:
            logger.error(f"Error getting role configurations: {str(e)}")
            raise ValidationError(f"Failed to get configurations: {str(e)}")

    @staticmethod
    def bulk_update_allowances(
        updates: List[Dict[str, Any]], user: CustomUser
    ) -> Dict[str, Any]:
        if not PayrollAccessControl.can_configure_payroll(user):
            raise ValidationError(
                "You don't have permission to update payroll configurations"
            )

        results = {"successful": [], "failed": []}

        try:
            with transaction.atomic():
                for update in updates:
                    try:
                        role = Role.objects.get(name=update["role_name"])
                        config_key = f"{role.name}_TRANSPORT_ALLOWANCE"

                        config, created = PayrollConfiguration.objects.get_or_create(
                            role=role,
                            configuration_key=config_key,
                            defaults={
                                "configuration_type": "ALLOWANCE",
                                "configuration_value": str(update["amount"]),
                                "value_type": "DECIMAL",
                                "description": f"Transport allowance for {role.name}",
                                "created_by": user,
                            },
                        )

                        if not created:
                            config.configuration_value = str(update["amount"])
                            config.updated_by = user
                            config.save()

                        PayrollConfigurationService._invalidate_role_payroll_cache(role)
                        results["successful"].append(update["role_name"])

                    except Exception as e:
                        results["failed"].append(
                            {
                                "role_name": update.get("role_name", "Unknown"),
                                "error": str(e),
                            }
                        )

                log_payroll_activity(
                    user,
                    "BULK_ALLOWANCE_UPDATE",
                    {
                        "successful_count": len(results["successful"]),
                        "failed_count": len(results["failed"]),
                    },
                )

                return results

        except Exception as e:
            logger.error(f"Error in bulk allowance update: {str(e)}")
            raise ValidationError(f"Failed to update allowances: {str(e)}")


class PayrollAuditService:
    @staticmethod
    def get_payroll_audit_trail(
        period_id: str, user: CustomUser, limit: int = 100
    ) -> List[Dict[str, Any]]:
        if not PayrollAccessControl.can_view_payroll_reports(user):
            raise ValidationError("You don't have permission to view audit trails")

        try:
            period = PayrollPeriod.objects.get(id=period_id)

            audit_logs = (
                PayrollAuditLog.objects.filter(payroll_period=period)
                .select_related("user", "employee")
                .order_by("-created_at")[:limit]
            )

            return [
                {
                    "id": str(log.id),
                    "action_type": log.action_type,
                    "user": log.user.get_full_name() if log.user else "System",
                    "employee": log.employee.get_full_name() if log.employee else None,
                    "description": log.description,
                    "old_values": log.old_values,
                    "new_values": log.new_values,
                    "additional_data": log.additional_data,
                    "created_at": log.created_at.isoformat(),
                }
                for log in audit_logs
            ]

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error getting audit trail: {str(e)}")
            raise ValidationError(f"Failed to get audit trail: {str(e)}")

    @staticmethod
    def get_employee_payroll_changes(
        employee_id: str, user: CustomUser, days: int = 30
    ) -> List[Dict[str, Any]]:
        if not PayrollAccessControl.can_view_payroll(user):
            raise ValidationError("You don't have permission to view payroll changes")

        try:
            employee = CustomUser.objects.get(id=employee_id)

            if not PayrollAccessControl.can_view_payroll(user, employee):
                raise ValidationError(
                    "You don't have permission to view this employee's payroll changes"
                )

            from datetime import timedelta

            cutoff_date = timezone.now() - timedelta(days=days)

            audit_logs = (
                PayrollAuditLog.objects.filter(
                    employee=employee, created_at__gte=cutoff_date
                )
                .select_related("user")
                .order_by("-created_at")
            )

            return [
                {
                    "action_type": log.action_type,
                    "user": log.user.get_full_name() if log.user else "System",
                    "description": log.description,
                    "old_values": log.old_values,
                    "new_values": log.new_values,
                    "created_at": log.created_at.isoformat(),
                }
                for log in audit_logs
            ]

        except CustomUser.DoesNotExist:
            raise ValidationError("Employee not found")
        except Exception as e:
            logger.error(f"Error getting employee payroll changes: {str(e)}")
            raise ValidationError(f"Failed to get payroll changes: {str(e)}")

    @staticmethod
    def generate_audit_report(period_id: str, user: CustomUser) -> Dict[str, Any]:
        if not PayrollAccessControl.can_view_payroll_reports(user):
            raise ValidationError("You don't have permission to generate audit reports")

        try:
            period = PayrollPeriod.objects.get(id=period_id)

            audit_summary = PayrollAuditService._calculate_audit_summary(period)
            critical_actions = PayrollAuditService._get_critical_actions(period)
            user_activity = PayrollAuditService._get_user_activity_summary(period)

            return {
                "period_info": {
                    "year": period.year,
                    "month": period.month,
                    "status": period.status,
                },
                "audit_summary": audit_summary,
                "critical_actions": critical_actions,
                "user_activity": user_activity,
                "generated_at": timezone.now().isoformat(),
                "generated_by": user.get_full_name(),
            }

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error generating audit report: {str(e)}")
            raise ValidationError(f"Failed to generate audit report: {str(e)}")

    @staticmethod
    def _calculate_audit_summary(period: PayrollPeriod) -> Dict[str, Any]:
        audit_logs = PayrollAuditLog.objects.filter(payroll_period=period)

        action_counts = {}
        for action_type, _ in PayrollAuditLog.ACTION_TYPES:
            action_counts[action_type] = audit_logs.filter(
                action_type=action_type
            ).count()

        return {
            "total_actions": audit_logs.count(),
            "action_breakdown": action_counts,
            "unique_users": audit_logs.values("user").distinct().count(),
            "date_range": {
                "first_action": (
                    audit_logs.first().created_at.isoformat()
                    if audit_logs.exists()
                    else None
                ),
                "last_action": (
                    audit_logs.last().created_at.isoformat()
                    if audit_logs.exists()
                    else None
                ),
            },
        }

    @staticmethod
    def _get_critical_actions(period: PayrollPeriod) -> List[Dict[str, Any]]:
        critical_action_types = [
            "PERIOD_APPROVED",
            "PAYSLIP_APPROVED",
            "SALARY_ADJUSTMENT",
            "CONFIGURATION_CHANGED",
        ]

        critical_logs = (
            PayrollAuditLog.objects.filter(
                payroll_period=period, action_type__in=critical_action_types
            )
            .select_related("user", "employee")
            .order_by("-created_at")
        )

        return [
            {
                "action_type": log.action_type,
                "user": log.user.get_full_name() if log.user else "System",
                "employee": log.employee.get_full_name() if log.employee else None,
                "description": log.description,
                "created_at": log.created_at.isoformat(),
            }
            for log in critical_logs
        ]

    @staticmethod
    def _get_user_activity_summary(period: PayrollPeriod) -> List[Dict[str, Any]]:
        from django.db.models import Count

        user_activity = (
            PayrollAuditLog.objects.filter(payroll_period=period)
            .values("user__first_name", "user__last_name")
            .annotate(action_count=Count("id"))
            .order_by("-action_count")
        )

        return [
            {
                "user_name": (
                    f"{activity['user__first_name']} {activity['user__last_name']}"
                    if activity["user__first_name"]
                    else "System"
                ),
                "action_count": activity["action_count"],
            }
            for activity in user_activity
        ]


class PayrollDashboardService:
    @staticmethod
    def get_dashboard_overview(user: CustomUser) -> Dict[str, Any]:
        if not PayrollAccessControl.can_view_payroll_dashboard(user):
            raise ValidationError("You don't have permission to view payroll dashboard")

        try:
            current_year, current_month = PayrollUtilityHelper.get_next_payroll_period()
            current_period = PayrollPeriod.objects.filter(
                year=current_year, month=current_month
            ).first()
            recent_periods = PayrollPeriod.objects.order_by("-year", "-month")[:6]

            return {
                "current_period": (
                    PayrollDashboardService._format_period_summary(current_period)
                    if current_period
                    else None
                ),
                "recent_periods": [
                    PayrollDashboardService._format_period_summary(p)
                    for p in recent_periods
                ],
                "system_statistics": PayrollDashboardService._get_system_statistics(),
                "pending_actions": PayrollDashboardService._get_pending_actions(user),
                "financial_overview": PayrollDashboardService._get_financial_overview(),
            }

        except Exception as e:
            logger.error(f"Error getting dashboard overview: {str(e)}")
            raise ValidationError(f"Failed to get dashboard data: {str(e)}")

    @staticmethod
    def _format_period_summary(period: PayrollPeriod) -> Dict[str, Any]:
        total_payslips = period.payslips.count()
        calculated_payslips = period.payslips.filter(
            status__in=["CALCULATED", "APPROVED"]
        ).count()

        return {
            "id": str(period.id),
            "year": period.year,
            "month": period.month,
            "period_name": period.period_name,
            "status": period.status,
            "total_employees": period.total_employees,
            "total_gross_salary": float(period.total_gross_salary),
            "total_net_salary": float(period.total_net_salary),
            "calculation_progress": (
                (calculated_payslips / total_payslips * 100)
                if total_payslips > 0
                else 0
            ),
        }

    @staticmethod
    def _get_system_statistics() -> Dict[str, Any]:
        return {
            "total_active_employees": CustomUser.active.filter(status="ACTIVE").count(),
            "total_departments": Department.objects.filter(is_active=True).count(),
            "pending_advances": SalaryAdvance.objects.filter(status="PENDING").count(),
            "active_advances": SalaryAdvance.objects.filter(status="ACTIVE").count(),
        }

    @staticmethod
    def _get_pending_actions(user: CustomUser) -> Dict[str, Any]:
        pending = {
            "periods_to_process": 0,
            "periods_to_approve": 0,
            "advances_to_approve": 0,
            "payslips_to_calculate": 0,
        }

        if PayrollAccessControl.can_process_payroll(user):
            pending["periods_to_process"] = PayrollPeriod.objects.filter(
                status="DRAFT"
            ).count()
            pending["payslips_to_calculate"] = Payslip.objects.filter(
                status="DRAFT", payroll_period__status__in=["DRAFT", "PROCESSING"]
            ).count()

        if PayrollAccessControl.can_approve_payroll(user):
            pending["periods_to_approve"] = PayrollPeriod.objects.filter(
                status="COMPLETED"
            ).count()

        if PayrollAccessControl.can_manage_salary_advance(user):
            pending["advances_to_approve"] = SalaryAdvance.objects.filter(
                status="PENDING"
            ).count()

        return pending

    @staticmethod
    def _get_financial_overview() -> Dict[str, Any]:
        current_year = timezone.now().year
        year_periods = PayrollPeriod.objects.filter(
            year=current_year, status__in=["COMPLETED", "APPROVED", "PAID"]
        )

        total_gross = float(
            year_periods.aggregate(Sum("total_gross_salary"))["total_gross_salary__sum"]
            or 0
        )
        total_net = float(
            year_periods.aggregate(Sum("total_net_salary"))["total_net_salary__sum"]
            or 0
        )
        period_count = year_periods.count()

        return {
            "year_to_date_gross": total_gross,
            "year_to_date_net": total_net,
            "monthly_average_gross": (
                total_gross / period_count if period_count > 0 else 0
            ),
            "monthly_average_net": total_net / period_count if period_count > 0 else 0,
        }


class PayrollAnalyticsService:
    @staticmethod
    def get_payroll_trends(user: CustomUser, months: int = 12) -> Dict[str, Any]:
        if not PayrollAccessControl.can_view_payroll_reports(user):
            raise ValidationError("You don't have permission to view payroll analytics")

        try:
            from dateutil.relativedelta import relativedelta

            end_date = timezone.now().date()
            start_date = end_date - relativedelta(months=months)

            periods = PayrollPeriod.objects.filter(
                year__gte=start_date.year,
                year__lte=end_date.year,
                status__in=["COMPLETED", "APPROVED", "PAID"],
            ).order_by("year", "month")

            return {
                "monthly_trends": PayrollAnalyticsService._calculate_monthly_trends(
                    periods
                ),
                "role_analysis": PayrollAnalyticsService._analyze_role_trends(periods),
                "cost_analysis": PayrollAnalyticsService._analyze_cost_trends(periods),
            }

        except Exception as e:
            logger.error(f"Error getting payroll trends: {str(e)}")
            raise ValidationError(f"Failed to get payroll trends: {str(e)}")

    @staticmethod
    def _calculate_monthly_trends(periods) -> List[Dict[str, Any]]:
        return [
            {
                "year": period.year,
                "month": period.month,
                "total_employees": period.total_employees,
                "total_gross": float(period.total_gross_salary),
                "total_net": float(period.total_net_salary),
                "avg_gross_per_employee": (
                    float(period.total_gross_salary / period.total_employees)
                    if period.total_employees > 0
                    else 0
                ),
            }
            for period in periods
        ]

    @staticmethod
    def _analyze_role_trends(periods) -> Dict[str, List[Dict[str, Any]]]:
        role_trends = {}

        for role in Role.objects.filter(is_active=True):
            role_data = []
            for period in periods:
                role_payslips = period.payslips.filter(
                    employee__role=role, status__in=["CALCULATED", "APPROVED"]
                )
                if role_payslips.exists():
                    role_data.append(
                        {
                            "year": period.year,
                            "month": period.month,
                            "employee_count": role_payslips.count(),
                            "total_gross": float(
                                role_payslips.aggregate(Sum("gross_salary"))[
                                    "gross_salary__sum"
                                ]
                                or 0
                            ),
                            "avg_gross": float(
                                role_payslips.aggregate(Avg("gross_salary"))[
                                    "gross_salary__avg"
                                ]
                                or 0
                            ),
                        }
                    )

            if role_data:
                role_trends[role.name] = role_data

        return role_trends

    @staticmethod
    def _analyze_cost_trends(periods) -> Dict[str, List[Dict[str, Any]]]:
        cost_trends = {
            "basic_salary": [],
            "allowances": [],
            "overtime": [],
            "deductions": [],
        }

        for period in periods:
            payslips = period.payslips.filter(status__in=["CALCULATED", "APPROVED"])

            cost_trends["basic_salary"].append(
                {
                    "year": period.year,
                    "month": period.month,
                    "total": float(
                        payslips.aggregate(Sum("basic_salary"))["basic_salary__sum"]
                        or 0
                    ),
                }
            )

            allowances_total = (
                payslips.aggregate(
                    total=Sum(
                        F("transport_allowance")
                        + F("telephone_allowance")
                        + F("fuel_allowance")
                        + F("meal_allowance")
                    )
                )["total"]
                or 0
            )

            cost_trends["allowances"].append(
                {
                    "year": period.year,
                    "month": period.month,
                    "total": float(allowances_total),
                }
            )

            cost_trends["overtime"].append(
                {
                    "year": period.year,
                    "month": period.month,
                    "total": float(
                        payslips.aggregate(Sum("regular_overtime"))[
                            "regular_overtime__sum"
                        ]
                        or 0
                    ),
                }
            )

            cost_trends["deductions"].append(
                {
                    "year": period.year,
                    "month": period.month,
                    "total": float(
                        payslips.aggregate(Sum("total_deductions"))[
                            "total_deductions__sum"
                        ]
                        or 0
                    ),
                }
            )

        return cost_trends

    @staticmethod
    def generate_performance_metrics(
        period_id: str, user: CustomUser
    ) -> Dict[str, Any]:
        if not PayrollAccessControl.can_view_payroll_reports(user):
            raise ValidationError(
                "You don't have permission to view performance metrics"
            )

        try:
            period = PayrollPeriod.objects.get(id=period_id)
            payslips = period.payslips.filter(status__in=["CALCULATED", "APPROVED"])

            return {
                "processing_efficiency": PayrollAnalyticsService._calculate_processing_efficiency(
                    period
                ),
                "attendance_metrics": PayrollAnalyticsService._calculate_attendance_metrics(
                    payslips
                ),
                "cost_efficiency": PayrollAnalyticsService._calculate_cost_efficiency(
                    payslips
                ),
            }

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error generating performance metrics: {str(e)}")
            raise ValidationError(f"Failed to generate performance metrics: {str(e)}")

    @staticmethod
    def _calculate_processing_efficiency(period: PayrollPeriod) -> Dict[str, Any]:
        total_payslips = period.payslips.count()
        calculated_payslips = period.payslips.filter(
            status__in=["CALCULATED", "APPROVED"]
        ).count()

        return {
            "completion_rate": (
                (calculated_payslips / total_payslips * 100)
                if total_payslips > 0
                else 0
            ),
            "processing_days": (
                period.updated_at.date() - period.created_at.date()
            ).days,
            "error_rate": (
                ((total_payslips - calculated_payslips) / total_payslips * 100)
                if total_payslips > 0
                else 0
            ),
        }

    @staticmethod
    def _calculate_attendance_metrics(payslips) -> Dict[str, Any]:
        payslips_with_summary = payslips.filter(monthly_summary__isnull=False)

        if not payslips_with_summary.exists():
            return {
                "avg_attendance": 0,
                "avg_punctuality": 0,
                "high_performers_percentage": 0,
            }

        avg_attendance = (
            payslips_with_summary.aggregate(
                Avg("monthly_summary__attendance_percentage")
            )["monthly_summary__attendance_percentage__avg"]
            or 0
        )
        avg_punctuality = (
            payslips_with_summary.aggregate(Avg("monthly_summary__punctuality_score"))[
                "monthly_summary__punctuality_score__avg"
            ]
            or 0
        )
        high_performers = payslips_with_summary.filter(
            monthly_summary__attendance_percentage__gte=95,
            monthly_summary__punctuality_score__gte=95,
        ).count()

        return {
            "avg_attendance": float(avg_attendance),
            "avg_punctuality": float(avg_punctuality),
            "high_performers_percentage": (
                high_performers / payslips_with_summary.count() * 100
            ),
        }

    @staticmethod
    def _calculate_cost_efficiency(payslips) -> Dict[str, Any]:
        total_gross = payslips.aggregate(Sum("gross_salary"))["gross_salary__sum"] or 0
        total_basic = payslips.aggregate(Sum("basic_salary"))["basic_salary__sum"] or 0
        total_overtime = (
            payslips.aggregate(Sum("regular_overtime"))["regular_overtime__sum"] or 0
        )

        return {
            "overtime_percentage": (
                (float(total_overtime) / float(total_gross) * 100)
                if total_gross > 0
                else 0
            ),
            "basic_to_gross_ratio": (
                (float(total_basic) / float(total_gross) * 100)
                if total_gross > 0
                else 0
            ),
            "cost_per_employee": (
                float(total_gross) / payslips.count() if payslips.count() > 0 else 0
            ),
        }


class PayrollValidationService:
    @staticmethod
    def validate_payroll_period_creation(
        year: int, month: int, user: CustomUser
    ) -> Tuple[bool, str]:
        if not PayrollAccessControl.can_process_payroll(user):
            return False, "You don't have permission to create payroll periods"

        if PayrollPeriod.objects.filter(year=year, month=month).exists():
            return False, f"Payroll period for {year}-{month:02d} already exists"

        current_date = timezone.now().date()
        if year > current_date.year or (
            year == current_date.year and month > current_date.month
        ):
            return False, "Cannot create payroll period for future months"

        if year < current_date.year - 2:
            return False, "Cannot create payroll period older than 2 years"

        return True, "Valid payroll period"

    @staticmethod
    def validate_payslip_calculation(
        payslip: Payslip, user: CustomUser
    ) -> Tuple[bool, str]:
        if not PayrollAccessControl.can_process_payroll(user):
            return False, "You don't have permission to calculate payslips"

        if payslip.status not in ["DRAFT"]:
            return False, f"Cannot calculate payslip in {payslip.status} status"

        if payslip.payroll_period.status not in ["DRAFT", "PROCESSING"]:
            return False, "Cannot calculate payslip for completed payroll period"

        if not payslip.employee.is_active or payslip.employee.status != "ACTIVE":
            return False, "Employee is not active"

        monthly_summary = PayrollDataProcessor.get_employee_monthly_summary(
            payslip.employee, payslip.payroll_period.year, payslip.payroll_period.month
        )

        if not monthly_summary:
            return (
                False,
                f"Monthly attendance summary not found for {payslip.employee.employee_code}",
            )

        return True, "Valid for calculation"

    @staticmethod
    def validate_salary_advance_request(
        employee: CustomUser, amount: Decimal, user: CustomUser
    ) -> Tuple[bool, str]:
        if not PayrollAccessControl.can_manage_salary_advance(user):
            return False, "You don't have permission to manage salary advances"

        if not employee.is_active or employee.status != "ACTIVE":
            return False, "Employee is not active"

        advance_data = PayrollAdvanceCalculator.calculate_available_advance_amount(
            employee
        )

        if amount > advance_data["available_amount"]:
            return (
                False,
                f"Requested amount exceeds available limit of LKR {advance_data['available_amount']}",
            )

        max_advances_per_year = SystemConfiguration.get_int_setting(
            "MAX_ADVANCES_PER_YEAR", 10
        )
        if advance_data["advance_count_this_year"] >= max_advances_per_year:
            return False, f"Maximum {max_advances_per_year} advances per year exceeded"

        return True, "Valid advance request"

    @staticmethod
    def validate_bank_transfer_generation(
        period: PayrollPeriod, user: CustomUser
    ) -> Tuple[bool, str]:
        if not PayrollAccessControl.can_export_payroll(user):
            return False, "You don't have permission to generate bank transfer files"

        if period.status != "APPROVED":
            return False, "Can only generate bank transfer for approved payroll"

        if PayrollBankTransfer.objects.filter(
            payroll_period=period,
            status__in=["GENERATED", "SENT", "PROCESSED", "COMPLETED"],
        ).exists():
            return False, "Bank transfer file already exists for this period"

        approved_payslips = period.payslips.filter(status="APPROVED", net_salary__gt=0)
        if not approved_payslips.exists():
            return False, "No approved payslips with positive net salary found"

        employees_without_bank_details = approved_payslips.filter(
            Q(employee__employee_profile__bank_account_number__isnull=True)
            | Q(employee__employee_profile__bank_account_number="")
        ).count()

        if employees_without_bank_details > 0:
            return (
                False,
                f"{employees_without_bank_details} employees missing bank account details",
            )

        return True, "Valid for bank transfer generation"


class PayrollMaintenanceService:
    @staticmethod
    def cleanup_expired_data(user: CustomUser, days: int = 365) -> Dict[str, Any]:
        if not PayrollAccessControl.can_configure_payroll(user):
            raise ValidationError(
                "You don't have permission to perform maintenance operations"
            )

        try:
            from datetime import timedelta

            cutoff_date = timezone.now().date() - timedelta(days=days)
            cleanup_results = {
                "audit_logs": 0,
                "cancelled_periods": 0,
                "completed_advances": 0,
            }

            with transaction.atomic():
                expired_audit_logs = PayrollAuditLog.objects.filter(
                    created_at__date__lt=cutoff_date
                )
                cleanup_results["audit_logs"] = expired_audit_logs.count()
                expired_audit_logs.delete()

                cancelled_periods = PayrollPeriod.objects.filter(
                    status="CANCELLED", created_at__date__lt=cutoff_date
                )
                for period in cancelled_periods:
                    period.payslips.all().delete()
                    period.delete()
                cleanup_results["cancelled_periods"] = cancelled_periods.count()

                completed_advances = SalaryAdvance.objects.filter(
                    status="COMPLETED", completion_date__lt=cutoff_date
                )
                cleanup_results["completed_advances"] = completed_advances.count()

                log_payroll_activity(
                    user,
                    "MAINTENANCE_CLEANUP_PERFORMED",
                    {
                        "cutoff_date": cutoff_date.isoformat(),
                        "cleanup_results": cleanup_results,
                    },
                )

                return cleanup_results

        except Exception as e:
            logger.error(f"Error in maintenance cleanup: {str(e)}")
            raise ValidationError(f"Failed to perform cleanup: {str(e)}")

    @staticmethod
    def validate_data_integrity(user: CustomUser) -> Dict[str, Any]:
        if not PayrollAccessControl.can_view_payroll_reports(user):
            raise ValidationError(
                "You don't have permission to validate data integrity"
            )

        try:
            integrity_issues = []

            payslips_without_summary = Payslip.objects.filter(
                monthly_summary__isnull=True, status__in=["CALCULATED", "APPROVED"]
            )
            if payslips_without_summary.exists():
                integrity_issues.append(
                    f"{payslips_without_summary.count()} payslips without monthly summary"
                )

            negative_salaries = Payslip.objects.filter(net_salary__lt=0)
            if negative_salaries.exists():
                integrity_issues.append(
                    f"{negative_salaries.count()} payslips with negative net salary"
                )

            mismatched_totals = []
            for period in PayrollPeriod.objects.filter(
                status__in=["COMPLETED", "APPROVED"]
            ):
                calculated_total = (
                    period.payslips.aggregate(total=Sum("net_salary"))["total"] or 0
                )
                if abs(calculated_total - period.total_net_salary) > 0.01:
                    mismatched_totals.append(str(period.id))

            if mismatched_totals:
                integrity_issues.append(
                    f"{len(mismatched_totals)} periods with mismatched totals"
                )

            active_advances_without_deduction = SalaryAdvance.objects.filter(
                status="ACTIVE", outstanding_amount__gt=0, monthly_deduction=0
            )
            if active_advances_without_deduction.exists():
                integrity_issues.append(
                    f"{active_advances_without_deduction.count()} active advances without monthly deduction"
                )

            return {
                "is_valid": len(integrity_issues) == 0,
                "issues_found": len(integrity_issues),
                "issues": integrity_issues,
                "validation_time": timezone.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error in data integrity validation: {str(e)}")
            raise ValidationError(f"Failed to validate data integrity: {str(e)}")

    @staticmethod
    def recalculate_period_totals(period_id: str, user: CustomUser) -> PayrollPeriod:
        if not PayrollAccessControl.can_process_payroll(user):
            raise ValidationError(
                "You don't have permission to recalculate period totals"
            )

        try:
            with transaction.atomic():
                period = PayrollPeriod.objects.select_for_update().get(id=period_id)

                old_totals = {
                    "total_gross_salary": float(period.total_gross_salary),
                    "total_net_salary": float(period.total_net_salary),
                    "total_employees": period.total_employees,
                }

                period.calculate_period_totals()

                log_payroll_activity(
                    user,
                    "PERIOD_TOTALS_RECALCULATED",
                    {
                        "period_id": str(period.id),
                        "old_totals": old_totals,
                        "new_totals": {
                            "total_gross_salary": float(period.total_gross_salary),
                            "total_net_salary": float(period.total_net_salary),
                            "total_employees": period.total_employees,
                        },
                    },
                )

                return period

        except PayrollPeriod.DoesNotExist:
            raise ValidationError("Payroll period not found")
        except Exception as e:
            logger.error(f"Error recalculating period totals: {str(e)}")
            raise ValidationError(f"Failed to recalculate totals: {str(e)}")


class PayrollServiceManager:
    @staticmethod
    def get_service_status() -> Dict[str, Any]:
        return {
            "services_available": [
                "PayrollPeriodService",
                "PayslipCalculationService",
                "SalaryAdvanceService",
                "EmployeePayrollService",
                "PayrollReportingService",
                "BankTransferService",
                "PayrollConfigurationService",
                "PayrollAuditService",
                "PayrollDashboardService",
                "PayrollAnalyticsService",
                "PayrollValidationService",
                "PayrollMaintenanceService",
            ],
            "total_services": 12,
            "cache_enabled": True,
            "audit_enabled": True,
            "validation_enabled": True,
            "maintenance_enabled": True,
        }

    @staticmethod
    def initialize_payroll_services():
        try:
            from .models import initialize_payroll_system

            initialize_payroll_system()

            logger.info("Payroll services initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Error initializing payroll services: {str(e)}")
            return False

    @staticmethod
    def get_system_health() -> Dict[str, Any]:
        try:
            health_status = {
                "database_connection": True,
                "cache_connection": True,
                "services_operational": True,
                "last_check": timezone.now().isoformat(),
            }

            try:
                PayrollPeriod.objects.count()
            except Exception:
                health_status["database_connection"] = False

            try:
                from django.core.cache import cache

                cache.set("health_check", "ok", 10)
                cache.get("health_check")
            except Exception:
                health_status["cache_connection"] = False

            health_status["overall_status"] = all(
                [
                    health_status["database_connection"],
                    health_status["cache_connection"],
                    health_status["services_operational"],
                ]
            )

            return health_status

        except Exception as e:
            logger.error(f"Error checking system health: {str(e)}")
            return {
                "overall_status": False,
                "error": str(e),
                "last_check": timezone.now().isoformat(),
            }


def get_payroll_service_registry():
    return {
        "period_management": PayrollPeriodService,
        "payslip_calculation": PayslipCalculationService,
        "salary_advances": SalaryAdvanceService,
        "employee_payroll": EmployeePayrollService,
        "reporting": PayrollReportingService,
        "bank_transfers": BankTransferService,
        "configuration": PayrollConfigurationService,
        "audit": PayrollAuditService,
        "dashboard": PayrollDashboardService,
        "analytics": PayrollAnalyticsService,
        "validation": PayrollValidationService,
        "maintenance": PayrollMaintenanceService,
    }


if __name__ == "__main__":
    PayrollServiceManager.initialize_payroll_services()
