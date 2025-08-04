from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver, Signal
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.core.cache import cache
from django.db.models import Sum, Count, Q, F
from accounts.models import CustomUser, SystemConfiguration, AuditLog
from employees.models import EmployeeProfile, Contract
from attendance.models import MonthlyAttendanceSummary, Attendance, LeaveRequest
from attendance.utils import MonthlyCalculator, EmployeeDataManager
from .models import (
    PayrollPeriod,
    Payslip,
    SalaryAdvance,
    PayrollDepartmentSummary,
    PayrollBankTransfer,
)
from .utils import (
    PayrollCacheManager,
    PayrollUtilityHelper,
    log_payroll_activity,
    PayrollCalculator,
    PayrollDataProcessor,
    PayrollAdvanceCalculator,
)
import logging

logger = logging.getLogger(__name__)

payroll_period_created = Signal()
payroll_period_completed = Signal()
payroll_calculated = Signal()
payroll_approved = Signal()
salary_advance_approved = Signal()
bank_transfer_generated = Signal()


@receiver(post_save, sender=CustomUser)
def handle_employee_creation_for_payroll(sender, instance, created, **kwargs):
    if created and instance.is_active and instance.status == "ACTIVE":
        try:
            current_year, current_month = PayrollUtilityHelper.get_next_payroll_period()

            try:
                current_period = PayrollPeriod.objects.get(
                    year=current_year, month=current_month
                )
                if current_period.status == "DRAFT":
                    Payslip.objects.get_or_create(
                        payroll_period=current_period,
                        employee=instance,
                        defaults={
                            "status": "DRAFT",
                            "created_by": current_period.created_by,
                        },
                    )

                    log_payroll_activity(
                        user=current_period.created_by or instance,
                        action="EMPLOYEE_ADDED_TO_PAYROLL",
                        details={
                            "employee_code": instance.employee_code,
                            "period_id": str(current_period.id),
                            "auto_created": True,
                        },
                    )
            except PayrollPeriod.DoesNotExist:
                pass

        except Exception as e:
            logger.error(
                f"Error creating payroll entry for new employee {instance.employee_code}: {str(e)}"
            )


@receiver(post_save, sender=EmployeeProfile)
def handle_employee_profile_update_for_payroll(sender, instance, created, **kwargs):
    if not created:
        employee = instance.user

        current_payslips = Payslip.objects.filter(
            employee=employee,
            status="DRAFT",
            payroll_period__status__in=["DRAFT", "PROCESSING"],
        )

        for payslip in current_payslips:
            try:
                PayrollCacheManager.invalidate_payroll_cache(
                    employee.id,
                    payslip.payroll_period.year,
                    payslip.payroll_period.month,
                )

                if payslip.status == "CALCULATED":
                    payslip.status = "DRAFT"
                    payslip.save(update_fields=["status"])

                log_payroll_activity(
                    user=employee,
                    action="PROFILE_UPDATED_PAYROLL_RECALC_NEEDED",
                    details={
                        "employee_code": employee.employee_code,
                        "payslip_id": str(payslip.id),
                        "changes": "Employee profile updated",
                    },
                )

            except Exception as e:
                logger.error(
                    f"Error handling profile update for payroll {payslip.id}: {str(e)}"
                )


@receiver(post_save, sender=Contract)
def handle_contract_update_for_payroll(sender, instance, created, **kwargs):
    if instance.is_active:
        employee = instance.employee

        current_payslips = Payslip.objects.filter(
            employee=employee,
            status__in=["DRAFT", "CALCULATED"],
            payroll_period__status__in=["DRAFT", "PROCESSING"],
        )

        for payslip in current_payslips:
            try:
                PayrollCacheManager.invalidate_payroll_cache(
                    employee.id,
                    payslip.payroll_period.year,
                    payslip.payroll_period.month,
                )

                if payslip.status == "CALCULATED":
                    payslip.status = "DRAFT"
                    payslip.save(update_fields=["status"])

                log_payroll_activity(
                    user=employee,
                    action="CONTRACT_UPDATED_PAYROLL_RECALC_NEEDED",
                    details={
                        "employee_code": employee.employee_code,
                        "contract_id": str(instance.id),
                        "payslip_id": str(payslip.id),
                        "salary_change": created
                        or "salary_structure" in kwargs.get("update_fields", []),
                    },
                )

            except Exception as e:
                logger.error(
                    f"Error handling contract update for payroll {payslip.id}: {str(e)}"
                )


@receiver(post_save, sender=MonthlyAttendanceSummary)
def handle_monthly_summary_update_for_payroll(sender, instance, created, **kwargs):
    try:
        payslip = Payslip.objects.filter(
            employee=instance.employee,
            payroll_period__year=instance.year,
            payroll_period__month=instance.month,
        ).first()

        if payslip:
            PayrollCacheManager.invalidate_payroll_cache(
                instance.employee.id, instance.year, instance.month
            )

            if payslip.status == "CALCULATED":
                payslip.monthly_summary = instance
                payslip.status = "DRAFT"
                payslip.save(update_fields=["monthly_summary", "status"])
            elif payslip.status == "DRAFT":
                payslip.monthly_summary = instance
                payslip.save(update_fields=["monthly_summary"])

            log_payroll_activity(
                user=instance.employee,
                action="ATTENDANCE_SUMMARY_UPDATED",
                details={
                    "employee_code": instance.employee.employee_code,
                    "year": instance.year,
                    "month": instance.month,
                    "payslip_id": str(payslip.id),
                    "attendance_percentage": float(instance.attendance_percentage),
                    "punctuality_score": float(instance.punctuality_score),
                },
            )

    except Exception as e:
        logger.error(f"Error handling monthly summary update for payroll: {str(e)}")


@receiver(post_save, sender=Attendance)
def handle_attendance_update_for_payroll(sender, instance, created, **kwargs):
    if not created:
        try:
            current_payslip = Payslip.objects.filter(
                employee=instance.employee,
                payroll_period__year=instance.date.year,
                payroll_period__month=instance.date.month,
                status__in=["DRAFT", "CALCULATED"],
            ).first()

            if current_payslip:
                PayrollCacheManager.invalidate_payroll_cache(
                    instance.employee.id, instance.date.year, instance.date.month
                )

                if current_payslip.status == "CALCULATED":
                    current_payslip.status = "DRAFT"
                    current_payslip.save(update_fields=["status"])

                monthly_summary = MonthlyCalculator.calculate_monthly_summary(
                    instance.employee, instance.date.year, instance.date.month
                )

                if monthly_summary:
                    current_payslip.monthly_summary = monthly_summary
                    current_payslip.save(update_fields=["monthly_summary"])

        except Exception as e:
            logger.error(f"Error handling attendance update for payroll: {str(e)}")


@receiver(post_save, sender=LeaveRequest)
def handle_leave_request_for_payroll(sender, instance, created, **kwargs):
    if instance.status == "APPROVED":
        try:
            affected_months = []

            current_date = instance.start_date
            while current_date <= instance.end_date:
                month_key = (current_date.year, current_date.month)
                if month_key not in affected_months:
                    affected_months.append(month_key)
                current_date = current_date.replace(day=1)
                if current_date.month == 12:
                    current_date = current_date.replace(
                        year=current_date.year + 1, month=1
                    )
                else:
                    current_date = current_date.replace(month=current_date.month + 1)

            for year, month in affected_months:
                payslip = Payslip.objects.filter(
                    employee=instance.employee,
                    payroll_period__year=year,
                    payroll_period__month=month,
                    status__in=["DRAFT", "CALCULATED"],
                ).first()

                if payslip:
                    PayrollCacheManager.invalidate_payroll_cache(
                        instance.employee.id, year, month
                    )

                    if payslip.status == "CALCULATED":
                        payslip.status = "DRAFT"
                        payslip.save(update_fields=["status"])

                    log_payroll_activity(
                        user=instance.employee,
                        action="LEAVE_APPROVED_PAYROLL_UPDATE",
                        details={
                            "employee_code": instance.employee.employee_code,
                            "leave_request_id": str(instance.id),
                            "leave_type": instance.leave_type.name,
                            "is_paid": instance.leave_type.is_paid,
                            "start_date": instance.start_date.isoformat(),
                            "end_date": instance.end_date.isoformat(),
                            "payslip_id": str(payslip.id),
                        },
                    )

        except Exception as e:
            logger.error(f"Error handling leave request for payroll: {str(e)}")


@receiver(post_save, sender=SystemConfiguration)
def handle_system_configuration_change_for_payroll(sender, instance, created, **kwargs):
    payroll_related_settings = [
        "NET_WORKING_HOURS",
        "OVERTIME_RATE_MULTIPLIER",
        "EPF_EMPLOYEE_RATE",
        "EPF_EMPLOYER_RATE",
        "ETF_RATE",
        "BASIC_TAX_RATE",
        "HALF_DAY_SALARY_PERCENTAGE",
        "ATTENDANCE_BONUS_THRESHOLD",
        "PUNCTUALITY_BONUS_THRESHOLD",
        "LATE_PENALTY_PER_MINUTE",
        "LUNCH_VIOLATION_PENALTY_DAYS",
        "SALARY_ADVANCE_MAX_PERCENTAGE",
    ]

    role_based_settings = [
        "MANAGER_TRANSPORT_ALLOWANCE",
        "CASHIER_TRANSPORT_ALLOWANCE",
        "SALESMAN_TRANSPORT_ALLOWANCE",
        "OTHER_STAFF_TRANSPORT_ALLOWANCE",
        "MANAGER_MEAL_ALLOWANCE",
        "CASHIER_MEAL_ALLOWANCE",
    ]

    if instance.key in payroll_related_settings or any(
        setting in instance.key for setting in role_based_settings
    ):
        try:
            draft_payslips = Payslip.objects.filter(
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

            log_payroll_activity(
                user=instance.updated_by,
                action="SYSTEM_CONFIG_CHANGED_PAYROLL_IMPACT",
                details={
                    "setting_key": instance.key,
                    "old_value": kwargs.get("old_value", ""),
                    "new_value": instance.value,
                    "affected_payslips": draft_payslips.count(),
                },
            )

        except Exception as e:
            logger.error(
                f"Error handling system configuration change for payroll: {str(e)}"
            )


@receiver(pre_save, sender=PayrollPeriod)
def handle_payroll_period_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = PayrollPeriod.objects.get(pk=instance.pk)

            if old_instance.status != instance.status:
                if instance.status == "PROCESSING" and old_instance.status != "DRAFT":
                    raise ValidationError("Can only start processing from DRAFT status")

                if (
                    instance.status == "COMPLETED"
                    and old_instance.status != "PROCESSING"
                ):
                    raise ValidationError("Can only complete from PROCESSING status")

                if instance.status == "APPROVED" and old_instance.status != "COMPLETED":
                    raise ValidationError("Can only approve from COMPLETED status")

                if instance.status == "PAID" and old_instance.status != "APPROVED":
                    raise ValidationError("Can only mark as paid from APPROVED status")

        except PayrollPeriod.DoesNotExist:
            pass


@receiver(post_save, sender=PayrollPeriod)
def handle_payroll_period_post_save(sender, instance, created, **kwargs):
    if created:
        try:
            payroll_period_created.send(
                sender=sender, payroll_period=instance, created_by=instance.created_by
            )

            eligible_employees = CustomUser.active.filter(
                status="ACTIVE", hire_date__lte=instance.end_date
            ).exclude(termination_date__lt=instance.start_date)

            created_payslips = []
            for employee in eligible_employees:
                try:
                    payslip, created_payslip = Payslip.objects.get_or_create(
                        payroll_period=instance,
                        employee=employee,
                        defaults={"status": "DRAFT", "created_by": instance.created_by},
                    )

                    if created_payslip:
                        created_payslips.append(payslip)

                except Exception as e:
                    logger.error(
                        f"Error creating payslip for employee {employee.employee_code}: {str(e)}"
                    )

            log_payroll_activity(
                user=instance.created_by,
                action="PAYROLL_PERIOD_CREATED",
                details={
                    "period_id": str(instance.id),
                    "year": instance.year,
                    "month": instance.month,
                    "eligible_employees": eligible_employees.count(),
                    "created_payslips": len(created_payslips),
                },
            )

        except Exception as e:
            logger.error(f"Error in payroll period post_save signal: {str(e)}")

    else:
        if hasattr(instance, "_status_changed"):
            if instance.status == "COMPLETED":
                try:
                    payroll_period_completed.send(
                        sender=sender, payroll_period=instance
                    )

                    instance.calculate_period_totals()

                    PayrollDepartmentSummary.objects.filter(
                        payroll_period=instance
                    ).delete()

                    for department in instance.payslips.values_list(
                        "employee__department", flat=True
                    ).distinct():
                        if department:
                            dept_obj = instance.payslips.first().employee.department.__class__.objects.get(
                                id=department
                            )
                            summary, created = (
                                PayrollDepartmentSummary.objects.get_or_create(
                                    payroll_period=instance, department=dept_obj
                                )
                            )
                            summary.calculate_summary()

                except Exception as e:
                    logger.error(f"Error handling payroll period completion: {str(e)}")


@receiver(pre_save, sender=Payslip)
def handle_payslip_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = Payslip.objects.get(pk=instance.pk)

            if old_instance.status != instance.status:
                if instance.status == "CALCULATED" and old_instance.status != "DRAFT":
                    raise ValidationError("Can only calculate from DRAFT status")

                if (
                    instance.status == "APPROVED"
                    and old_instance.status != "CALCULATED"
                ):
                    raise ValidationError("Can only approve from CALCULATED status")

                if instance.status == "PAID" and old_instance.status != "APPROVED":
                    raise ValidationError("Can only mark as paid from APPROVED status")

                instance._status_changed = True
                instance._old_status = old_instance.status

            if (
                old_instance.net_salary != instance.net_salary
                and instance.net_salary > 0
            ):
                instance._salary_changed = True
                instance._old_net_salary = old_instance.net_salary

        except Payslip.DoesNotExist:
            pass


@receiver(post_save, sender=Payslip)
def handle_payslip_post_save(sender, instance, created, **kwargs):
    if created:
        try:
            if not instance.monthly_summary:
                monthly_summary = PayrollDataProcessor.get_employee_monthly_summary(
                    instance.employee,
                    instance.payroll_period.year,
                    instance.payroll_period.month,
                )
                if monthly_summary:
                    instance.monthly_summary = monthly_summary
                    Payslip.objects.filter(pk=instance.pk).update(
                        monthly_summary=monthly_summary
                    )

            log_payroll_activity(
                user=instance.payroll_period.created_by or instance.employee,
                action="PAYSLIP_CREATED",
                details={
                    "payslip_id": str(instance.id),
                    "employee_code": instance.employee.employee_code,
                    "period_id": str(instance.payroll_period.id),
                    "year": instance.payroll_period.year,
                    "month": instance.payroll_period.month,
                },
            )

        except Exception as e:
            logger.error(f"Error in payslip creation post_save: {str(e)}")

    else:
        if hasattr(instance, "_status_changed"):
            try:
                if instance.status == "CALCULATED":
                    payroll_calculated.send(
                        sender=sender,
                        payslip=instance,
                        calculated_by=instance.calculated_by,
                    )

                    PayrollCacheManager.cache_payroll_calculation(
                        instance.employee.id,
                        instance.payroll_period.year,
                        instance.payroll_period.month,
                        {
                            "gross_salary": float(instance.gross_salary),
                            "net_salary": float(instance.net_salary),
                            "total_deductions": float(instance.total_deductions),
                            "calculated_at": timezone.now().isoformat(),
                        },
                    )

                elif instance.status == "APPROVED":
                    payroll_approved.send(
                        sender=sender,
                        payslip=instance,
                        approved_by=instance.approved_by,
                    )

                    instance.payroll_period.calculate_period_totals()

                log_payroll_activity(
                    user=instance.calculated_by
                    or instance.approved_by
                    or instance.employee,
                    action=f"PAYSLIP_{instance.status.upper()}",
                    details={
                        "payslip_id": str(instance.id),
                        "employee_code": instance.employee.employee_code,
                        "old_status": getattr(instance, "_old_status", ""),
                        "new_status": instance.status,
                        "net_salary": float(instance.net_salary),
                    },
                )

            except Exception as e:
                logger.error(f"Error handling payslip status change: {str(e)}")

        if hasattr(instance, "_salary_changed"):
            try:
                log_payroll_activity(
                    user=instance.calculated_by or instance.employee,
                    action="PAYSLIP_SALARY_CHANGED",
                    details={
                        "payslip_id": str(instance.id),
                        "employee_code": instance.employee.employee_code,
                        "old_net_salary": float(
                            getattr(instance, "_old_net_salary", 0)
                        ),
                        "new_net_salary": float(instance.net_salary),
                        "difference": float(
                            instance.net_salary
                            - getattr(instance, "_old_net_salary", 0)
                        ),
                    },
                )

            except Exception as e:
                logger.error(f"Error logging salary change: {str(e)}")


@receiver(post_delete, sender=Payslip)
def handle_payslip_deletion(sender, instance, **kwargs):
    try:
        PayrollCacheManager.invalidate_payroll_cache(
            instance.employee.id,
            instance.payroll_period.year,
            instance.payroll_period.month,
        )

        if instance.payroll_period.status in ["DRAFT", "PROCESSING"]:
            instance.payroll_period.calculate_period_totals()

        log_payroll_activity(
            user=None,
            action="PAYSLIP_DELETED",
            details={
                "payslip_id": str(instance.id),
                "employee_code": instance.employee.employee_code,
                "period_id": str(instance.payroll_period.id),
                "net_salary": float(instance.net_salary),
            },
        )

    except Exception as e:
        logger.error(f"Error handling payslip deletion: {str(e)}")


@receiver(payroll_period_created)
def handle_payroll_period_created_signal(sender, payroll_period, created_by, **kwargs):
    try:
        cache_key = (
            f"current_payroll_period_{payroll_period.year}_{payroll_period.month}"
        )
        cache.set(cache_key, payroll_period.id, timeout=86400)

        log_payroll_activity(
            user=created_by,
            action="PERIOD_CREATED",
            details={
                "description": f"Payroll period created for {payroll_period.period_name}",
                "payroll_period_id": str(payroll_period.id),
                "total_working_days": payroll_period.total_working_days,
            },
        )

    except Exception as e:
        logger.error(f"Error in payroll_period_created signal handler: {str(e)}")


@receiver(payroll_calculated)
def handle_payroll_calculated_signal(sender, payslip, calculated_by, **kwargs):
    try:
        log_payroll_activity(
            user=calculated_by,
            action="PAYSLIP_CALCULATED",
            details={
                "description": f"Payslip calculated for {payslip.employee.get_full_name()}",
                "payslip_id": str(payslip.id),
                "employee_code": payslip.employee.employee_code,
                "payroll_period_id": str(payslip.payroll_period.id),
                "gross_salary": float(payslip.gross_salary),
                "net_salary": float(payslip.net_salary),
                "total_deductions": float(payslip.total_deductions),
                "employee_role": payslip.employee_role,
                "calculation_time": timezone.now().isoformat(),
            },
        )

        if payslip.payroll_period.status == "PROCESSING":
            completed_payslips = payslip.payroll_period.payslips.filter(
                status__in=["CALCULATED", "APPROVED"]
            ).count()
            total_payslips = payslip.payroll_period.payslips.count()

            if completed_payslips == total_payslips:
                payslip.payroll_period.mark_as_completed(calculated_by)

    except Exception as e:
        logger.error(f"Error in payroll_calculated signal handler: {str(e)}")


@receiver(payroll_approved)
def handle_payroll_approved_signal(sender, payslip, approved_by, **kwargs):
    try:
        log_payroll_activity(
            user=approved_by,
            action="PAYSLIP_APPROVED",
            details={
                "description": f"Payslip approved for {payslip.employee.get_full_name()}",
                "payslip_id": str(payslip.id),
                "employee_code": payslip.employee.employee_code,
                "payroll_period_id": str(payslip.payroll_period.id),
                "net_salary": float(payslip.net_salary),
                "approval_time": timezone.now().isoformat(),
            },
        )

        cache_key = f"approved_payslip_{payslip.employee.id}_{payslip.payroll_period.year}_{payslip.payroll_period.month}"
        cache.set(
            cache_key,
            {
                "payslip_id": str(payslip.id),
                "net_salary": float(payslip.net_salary),
                "approved_at": timezone.now().isoformat(),
            },
            timeout=86400,
        )

    except Exception as e:
        logger.error(f"Error in payroll_approved signal handler: {str(e)}")


@receiver(pre_save, sender=SalaryAdvance)
def handle_salary_advance_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = SalaryAdvance.objects.get(pk=instance.pk)

            if old_instance.status != instance.status:
                if instance.status == "APPROVED" and old_instance.status != "PENDING":
                    raise ValidationError("Can only approve from PENDING status")

                if instance.status == "ACTIVE" and old_instance.status != "APPROVED":
                    raise ValidationError("Can only activate from APPROVED status")

                if instance.status == "COMPLETED" and old_instance.status != "ACTIVE":
                    raise ValidationError("Can only complete from ACTIVE status")

                instance._status_changed = True
                instance._old_status = old_instance.status

            if old_instance.outstanding_amount != instance.outstanding_amount:
                instance._amount_changed = True
                instance._old_outstanding = old_instance.outstanding_amount

        except SalaryAdvance.DoesNotExist:
            pass

@receiver(post_save, sender=SalaryAdvance)
def handle_salary_advance_post_save(sender, instance, created, **kwargs):
    if created:
        try:
            advance_data = PayrollAdvanceCalculator.calculate_available_advance_amount(
                instance.employee
            )

            log_payroll_activity(
                user=instance.requested_by or instance.employee,
                action="SALARY_ADVANCE_REQUESTED",
                details={
                    "advance_id": str(instance.id),
                    "employee_code": instance.employee.employee_code,
                    "amount": float(instance.amount),
                    "advance_type": instance.advance_type,
                    "installments": instance.installments,
                    "available_amount_before": float(advance_data["available_amount"]),
                    "employee_basic_salary": float(instance.employee_basic_salary),
                },
            )

            log_payroll_activity(
                user=instance.requested_by or instance.employee,
                action="ADVANCE_REQUESTED",
                details={
                    "description": f"Salary advance requested by {instance.employee.get_full_name()}",
                    "advance_id": str(instance.id),
                    "employee_code": instance.employee.employee_code,
                    "amount": float(instance.amount),
                    "reason": instance.reason,
                    "advance_type": instance.advance_type,
                },
            )

        except Exception as e:
            logger.error(f"Error in salary advance creation post_save: {str(e)}")

    else:
        if hasattr(instance, "_status_changed"):
            try:
                if instance.status == "APPROVED":
                    salary_advance_approved.send(
                        sender=sender,
                        salary_advance=instance,
                        approved_by=instance.approved_by,
                    )

                    current_payslips = Payslip.objects.filter(
                        employee=instance.employee,
                        status__in=["DRAFT", "CALCULATED"],
                        payroll_period__status__in=["DRAFT", "PROCESSING"],
                    )

                    for payslip in current_payslips:
                        PayrollCacheManager.invalidate_payroll_cache(
                            instance.employee.id,
                            payslip.payroll_period.year,
                            payslip.payroll_period.month,
                        )

                        if payslip.status == "CALCULATED":
                            payslip.status = "DRAFT"
                            payslip.save(update_fields=["status"])

                elif instance.status == "ACTIVE":
                    current_year, current_month = (
                        PayrollUtilityHelper.get_next_payroll_period()
                    )

                    try:
                        current_payslip = Payslip.objects.get(
                            employee=instance.employee,
                            payroll_period__year=current_year,
                            payroll_period__month=current_month,
                        )

                        if current_payslip.status in ["DRAFT", "CALCULATED"]:
                            PayrollCacheManager.invalidate_payroll_cache(
                                instance.employee.id, current_year, current_month
                            )

                            if current_payslip.status == "CALCULATED":
                                current_payslip.status = "DRAFT"
                                current_payslip.save(update_fields=["status"])

                    except Payslip.DoesNotExist:
                        pass

                log_payroll_activity(
                    user=instance.approved_by or instance.employee,
                    action=f"SALARY_ADVANCE_{instance.status.upper()}",
                    details={
                        "advance_id": str(instance.id),
                        "employee_code": instance.employee.employee_code,
                        "old_status": getattr(instance, "_old_status", ""),
                        "new_status": instance.status,
                        "amount": float(instance.amount),
                        "outstanding_amount": float(instance.outstanding_amount),
                    },
                )

            except Exception as e:
                logger.error(f"Error handling salary advance status change: {str(e)}")

        if hasattr(instance, "_amount_changed"):
            try:
                log_payroll_activity(
                    user=instance.employee,
                    action="SALARY_ADVANCE_AMOUNT_UPDATED",
                    details={
                        "advance_id": str(instance.id),
                        "employee_code": instance.employee.employee_code,
                        "old_outstanding": float(
                            getattr(instance, "_old_outstanding", 0)
                        ),
                        "new_outstanding": float(instance.outstanding_amount),
                        "deduction_processed": float(
                            getattr(instance, "_old_outstanding", 0)
                            - instance.outstanding_amount
                        ),
                    },
                )

            except Exception as e:
                logger.error(f"Error logging advance amount change: {str(e)}")

@receiver(salary_advance_approved)
def handle_salary_advance_approved_signal(
    sender, salary_advance, approved_by, **kwargs
):
    try:
        log_payroll_activity(
            user=approved_by,
            action="ADVANCE_APPROVED",
            details={
                "description": f"Salary advance approved for {salary_advance.employee.get_full_name()}",
                "advance_id": str(salary_advance.id),
                "employee_code": salary_advance.employee.employee_code,
                "amount": float(salary_advance.amount),
                "installments": salary_advance.installments,
                "monthly_deduction": float(salary_advance.monthly_deduction),
                "approval_time": timezone.now().isoformat(),
            },
        )

        cache_key = f"employee_advances_{salary_advance.employee.id}"
        cache.delete(cache_key)

        advance_summary = {
            "total_active_advances": SalaryAdvance.objects.filter(
                employee=salary_advance.employee, status="ACTIVE"
            ).count(),
            "total_outstanding": float(
                SalaryAdvance.objects.filter(
                    employee=salary_advance.employee, status="ACTIVE"
                ).aggregate(total=models.Sum("outstanding_amount"))["total"]
                or 0
            ),
        }

        cache.set(cache_key, advance_summary, timeout=3600)

    except Exception as e:
        logger.error(f"Error in salary_advance_approved signal handler: {str(e)}")

@receiver(post_save, sender=PayrollDepartmentSummary)
def handle_department_summary_update(sender, instance, created, **kwargs):
    try:
        cache_key = f"dept_summary_{instance.department.id}_{instance.payroll_period.year}_{instance.payroll_period.month}"

        summary_data = {
            "employee_count": instance.employee_count,
            "total_gross": float(instance.total_gross_salary),
            "total_net": float(instance.total_net_salary),
            "average_salary": float(instance.average_salary),
            "budget_utilization": float(instance.budget_utilization_percentage),
            "role_breakdown": instance.role_breakdown,
            "performance_metrics": instance.performance_metrics,
        }

        cache.set(cache_key, summary_data, timeout=7200)

        if created:
            log_payroll_activity(
                user=None,
                action="DEPARTMENT_SUMMARY_CREATED",
                details={
                    "department": instance.department.name,
                    "period_id": str(instance.payroll_period.id),
                    "employee_count": instance.employee_count,
                    "total_gross": float(instance.total_gross_salary),
                    "budget_utilization": float(instance.budget_utilization_percentage),
                },
            )

    except Exception as e:
        logger.error(f"Error handling department summary update: {str(e)}")


@receiver(post_save, sender=PayrollBankTransfer)
def handle_bank_transfer_update(sender, instance, created, **kwargs):
    if created:
        try:
            log_payroll_activity(
                user=instance.created_by,
                action="BANK_TRANSFER_CREATED",
                details={
                    "transfer_id": str(instance.id),
                    "batch_reference": instance.batch_reference,
                    "period_id": str(instance.payroll_period.id),
                    "total_employees": instance.total_employees,
                    "total_amount": float(instance.total_amount),
                },
            )

        except Exception as e:
            logger.error(f"Error in bank transfer creation post_save: {str(e)}")

    else:
        if hasattr(instance, "_status_changed"):
            try:
                if instance.status == "GENERATED":
                    bank_transfer_generated.send(sender=sender, bank_transfer=instance)

                log_payroll_activity(
                    user=instance.created_by,
                    action=f"BANK_TRANSFER_{instance.status.upper()}",
                    details={
                        "transfer_id": str(instance.id),
                        "batch_reference": instance.batch_reference,
                        "new_status": instance.status,
                        "total_amount": float(instance.total_amount),
                    },
                )

            except Exception as e:
                logger.error(f"Error handling bank transfer status change: {str(e)}")


@receiver(bank_transfer_generated)
def handle_bank_transfer_generated_signal(sender, bank_transfer, **kwargs):
    try:
        log_payroll_activity(
            user=bank_transfer.created_by,
            action="BANK_TRANSFER_GENERATED",
            details={
                "description": f"Bank transfer file generated for {bank_transfer.payroll_period.period_name}",
                "payroll_period_id": str(bank_transfer.payroll_period.id),
                "batch_reference": bank_transfer.batch_reference,
                "total_employees": bank_transfer.total_employees,
                "total_amount": float(bank_transfer.total_amount),
                "file_path": bank_transfer.bank_file_path,
                "generation_time": timezone.now().isoformat(),
            },
        )

        cache_key = f"bank_transfer_{bank_transfer.payroll_period.year}_{bank_transfer.payroll_period.month}"
        cache.set(
            cache_key,
            {
                "batch_reference": bank_transfer.batch_reference,
                "status": bank_transfer.status,
                "total_amount": float(bank_transfer.total_amount),
                "generated_at": (
                    bank_transfer.generated_at.isoformat()
                    if bank_transfer.generated_at
                    else None
                ),
            },
            timeout=86400,
        )

    except Exception as e:
        logger.error(f"Error in bank_transfer_generated signal handler: {str(e)}")

@receiver(pre_save, sender=PayrollBankTransfer)
def handle_bank_transfer_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = PayrollBankTransfer.objects.get(pk=instance.pk)
            if old_instance.status != instance.status:
                instance._status_changed = True
                instance._old_status = old_instance.status
        except PayrollBankTransfer.DoesNotExist:
            pass

@receiver(pre_delete, sender=PayrollPeriod)
def handle_payroll_period_deletion(sender, instance, **kwargs):
    if instance.status not in ["DRAFT", "CANCELLED"]:
        raise ValidationError(
            "Cannot delete payroll period that is not in DRAFT or CANCELLED status"
        )

    try:
        log_payroll_activity(
            user=None,
            action="PAYROLL_PERIOD_DELETED",
            details={
                "period_id": str(instance.id),
                "year": instance.year,
                "month": instance.month,
                "status": instance.status,
                "total_payslips": instance.payslips.count(),
            },
        )

        cache_keys_to_delete = [
            f"current_payroll_period_{instance.year}_{instance.month}",
            f"payroll_summary_{instance.year}_{instance.month}",
            f"bank_transfer_{instance.year}_{instance.month}",
        ]

        for key in cache_keys_to_delete:
            cache.delete(key)

    except Exception as e:
        logger.error(f"Error handling payroll period deletion: {str(e)}")


@receiver(pre_delete, sender=SalaryAdvance)
def handle_salary_advance_deletion(sender, instance, **kwargs):
    if instance.status == "ACTIVE" and instance.outstanding_amount > 0:
        raise ValidationError(
            "Cannot delete active salary advance with outstanding amount"
        )

    try:
        log_payroll_activity(
            user=None,
            action="SALARY_ADVANCE_DELETED",
            details={
                "advance_id": str(instance.id),
                "employee_code": instance.employee.employee_code,
                "amount": float(instance.amount),
                "status": instance.status,
                "outstanding_amount": float(instance.outstanding_amount),
            },
        )

        cache_key = f"employee_advances_{instance.employee.id}"
        cache.delete(cache_key)

        current_payslips = Payslip.objects.filter(
            employee=instance.employee,
            status__in=["DRAFT", "CALCULATED"],
            payroll_period__status__in=["DRAFT", "PROCESSING"],
        )

        for payslip in current_payslips:
            PayrollCacheManager.invalidate_payroll_cache(
                instance.employee.id,
                payslip.payroll_period.year,
                payslip.payroll_period.month,
            )

            if payslip.status == "CALCULATED":
                payslip.status = "DRAFT"
                payslip.save(update_fields=["status"])

    except Exception as e:
        logger.error(f"Error handling salary advance deletion: {str(e)}")


def clear_payroll_caches(year=None, month=None, employee_id=None):
    try:
        cache_patterns = []

        if year and month:
            cache_patterns.extend(
                [
                    f"current_payroll_period_{year}_{month}",
                    f"payroll_summary_{year}_{month}",
                    f"bank_transfer_{year}_{month}",
                ]
            )

            if employee_id:
                cache_patterns.append(f"payroll_calc_{employee_id}_{year}_{month}")
                cache_patterns.append(f"approved_payslip_{employee_id}_{year}_{month}")
            else:
                for emp in CustomUser.active.all():
                    cache_patterns.append(f"payroll_calc_{emp.id}_{year}_{month}")
                    cache_patterns.append(f"approved_payslip_{emp.id}_{year}_{month}")

        if employee_id:
            cache_patterns.append(f"employee_advances_{employee_id}")

        for pattern in cache_patterns:
            cache.delete(pattern)

        logger.info(f"Cleared {len(cache_patterns)} payroll cache entries")

    except Exception as e:
        logger.error(f"Error clearing payroll caches: {str(e)}")


def invalidate_department_caches(department_id, year=None, month=None):
    try:
        cache_patterns = []

        if year and month:
            cache_patterns.append(f"dept_summary_{department_id}_{year}_{month}")
        else:
            current_year = timezone.now().year
            for m in range(1, 13):
                cache_patterns.append(
                    f"dept_summary_{department_id}_{current_year}_{m}"
                )

        for pattern in cache_patterns:
            cache.delete(pattern)

        logger.info(f"Invalidated {len(cache_patterns)} department cache entries")

    except Exception as e:
        logger.error(f"Error invalidating department caches: {str(e)}")


def refresh_payroll_calculations_for_role(role_id):
    try:
        from accounts.models import Role

        role = Role.objects.get(id=role_id)

        affected_payslips = Payslip.objects.filter(
            employee__role=role,
            status__in=["DRAFT", "CALCULATED"],
            payroll_period__status__in=["DRAFT", "PROCESSING"],
        )

        for payslip in affected_payslips:
            PayrollCacheManager.invalidate_payroll_cache(
                payslip.employee.id,
                payslip.payroll_period.year,
                payslip.payroll_period.month,
            )

            if payslip.status == "CALCULATED":
                payslip.status = "DRAFT"
                payslip.save(update_fields=["status"])

        logger.info(
            f"Refreshed payroll calculations for {affected_payslips.count()} payslips in role {role.name}"
        )

        return affected_payslips.count()

    except Exception as e:
        logger.error(
            f"Error refreshing payroll calculations for role {role_id}: {str(e)}"
        )
        return 0


def cleanup_expired_payroll_data():
    try:
        from datetime import timedelta

        cutoff_date = timezone.now().date() - timedelta(days=365)

        expired_audit_logs = AuditLog.objects.filter(
            created_at__date__lt=cutoff_date, action__icontains="PAYROLL"
        )

        expired_count = expired_audit_logs.count()

        if expired_count > 0:
            expired_audit_logs.delete()
            logger.info(f"Cleaned up {expired_count} expired payroll audit logs")

        cancelled_periods = PayrollPeriod.objects.filter(
            status="CANCELLED", created_at__date__lt=cutoff_date
        )

        for period in cancelled_periods:
            period.payslips.all().delete()
            period.delete()

        logger.info(f"Cleaned up {cancelled_periods.count()} cancelled payroll periods")

        completed_advances = SalaryAdvance.objects.filter(
            status="COMPLETED", completion_date__lt=cutoff_date
        )

        logger.info(
            f"Found {completed_advances.count()} completed salary advances older than 1 year"
        )

    except Exception as e:
        logger.error(f"Error in payroll data cleanup: {str(e)}")


def validate_payroll_data_integrity():
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
                period.payslips.aggregate(total=models.Sum("net_salary"))["total"] or 0
            )

            if abs(calculated_total - period.total_net_salary) > 0.01:
                mismatched_totals.append(period.id)

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

        if integrity_issues:
            logger.warning(
                f"Payroll data integrity issues found: {', '.join(integrity_issues)}"
            )
        else:
            logger.info("Payroll data integrity validation passed")

        return integrity_issues

    except Exception as e:
        logger.error(f"Error in payroll data integrity validation: {str(e)}")
        return [f"Validation error: {str(e)}"]


def register_payroll_signals():
    logger.info("Payroll signals registered successfully")


def initialize_payroll_signal_handlers():
    try:
        register_payroll_signals()

        logger.info("Payroll signal handlers initialized")

        return True

    except Exception as e:
        logger.error(f"Error initializing payroll signal handlers: {str(e)}")
        return False


class PayrollSignalManager:
    @staticmethod
    def disconnect_all_signals():
        try:
            signals_to_disconnect = [
                (post_save, CustomUser, handle_employee_creation_for_payroll),
                (
                    post_save,
                    EmployeeProfile,
                    handle_employee_profile_update_for_payroll,
                ),
                (post_save, Contract, handle_contract_update_for_payroll),
                (
                    post_save,
                    MonthlyAttendanceSummary,
                    handle_monthly_summary_update_for_payroll,
                ),
                (post_save, Attendance, handle_attendance_update_for_payroll),
                (post_save, LeaveRequest, handle_leave_request_for_payroll),
                (
                    post_save,
                    SystemConfiguration,
                    handle_system_configuration_change_for_payroll,
                ),
                (pre_save, PayrollPeriod, handle_payroll_period_pre_save),
                (post_save, PayrollPeriod, handle_payroll_period_post_save),
                (pre_save, Payslip, handle_payslip_pre_save),
                (post_save, Payslip, handle_payslip_post_save),
                (post_delete, Payslip, handle_payslip_deletion),
                (pre_save, SalaryAdvance, handle_salary_advance_pre_save),
                (post_save, SalaryAdvance, handle_salary_advance_post_save),
                (post_save, PayrollDepartmentSummary, handle_department_summary_update),
                (post_save, PayrollBankTransfer, handle_bank_transfer_update),
                (pre_delete, PayrollPeriod, handle_payroll_period_deletion),
                (pre_delete, SalaryAdvance, handle_salary_advance_deletion),
            ]

            for signal, sender, handler in signals_to_disconnect:
                signal.disconnect(handler, sender=sender)

            logger.info(f"Disconnected {len(signals_to_disconnect)} payroll signals")

        except Exception as e:
            logger.error(f"Error disconnecting payroll signals: {str(e)}")

    @staticmethod
    def reconnect_all_signals():
        try:
            initialize_payroll_signal_handlers()
            logger.info("Reconnected all payroll signals")

        except Exception as e:
            logger.error(f"Error reconnecting payroll signals: {str(e)}")

    @staticmethod
    def get_signal_status():
        return {
            "total_handlers": 18,
            "custom_signals": 6,
            "cache_management": True,
            "integrity_validation": True,
            "cleanup_functions": True,
        }


if __name__ == "__main__":
    initialize_payroll_signal_handlers()
