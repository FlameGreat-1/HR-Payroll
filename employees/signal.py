from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from accounts.models import CustomUser, AuditLog, SystemConfiguration
from .models import EmployeeProfile, Education, Contract
from datetime import timedelta


@receiver(post_save, sender=CustomUser)
def create_employee_profile(sender, instance, created, **kwargs):
    if created and instance.is_active and not instance.is_superuser:
        if not hasattr(instance, "employee_profile"):
            EmployeeProfile.objects.create(
                user=instance,
                employment_status="PROBATION",
                basic_salary=0.00,
                created_by=getattr(instance, "_created_by", None),
            )


@receiver(post_save, sender=EmployeeProfile)
def log_employee_profile_changes(sender, instance, created, **kwargs):
    if created:
        AuditLog.log_action(
            user=instance.created_by or instance.user,
            action="USER_CREATED",
            description=f"Employee profile created for {instance.user.get_full_name()}",
            ip_address="127.0.0.1",
            additional_data={
                "employee_id": instance.employee_id,
                "employment_status": instance.employment_status,
                "basic_salary": str(instance.basic_salary),
            },
        )
    else:
        AuditLog.log_action(
            user=instance.user,
            action="PROFILE_UPDATE",
            description=f"Employee profile updated for {instance.user.get_full_name()}",
            ip_address="127.0.0.1",
            additional_data={
                "employee_id": instance.employee_id,
                "employment_status": instance.employment_status,
            },
        )


@receiver(pre_save, sender=EmployeeProfile)
def handle_employment_status_change(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = EmployeeProfile.objects.get(pk=instance.pk)

            if old_instance.employment_status != instance.employment_status:
                if (
                    instance.employment_status == "CONFIRMED"
                    and not instance.confirmation_date
                ):
                    instance.confirmation_date = timezone.now().date()

                if SystemConfiguration.get_bool_setting("EMAIL_NOTIFICATIONS_ENABLED"):
                    send_employment_status_notification(
                        instance, old_instance.employment_status
                    )
        except EmployeeProfile.DoesNotExist:
            pass


@receiver(post_save, sender=Education)
def log_education_changes(sender, instance, created, **kwargs):
    action = "EDUCATION_ADDED" if created else "EDUCATION_UPDATED"
    AuditLog.log_action(
        user=instance.created_by or instance.employee,
        action=action,
        description=f"Education record {action.lower()} for {instance.employee.get_full_name()}",
        ip_address="127.0.0.1",
        additional_data={
            "qualification": instance.qualification,
            "institution": instance.institution,
            "education_level": instance.education_level,
        },
    )


@receiver(post_save, sender=Contract)
def handle_contract_changes(sender, instance, created, **kwargs):
    if created:
        AuditLog.log_action(
            user=instance.created_by or instance.employee,
            action="CONTRACT_CREATED",
            description=f"Contract {instance.contract_number} created for {instance.employee.get_full_name()}",
            ip_address="127.0.0.1",
            additional_data={
                "contract_number": instance.contract_number,
                "contract_type": instance.contract_type,
                "basic_salary": str(instance.basic_salary),
            },
        )

        if SystemConfiguration.get_bool_setting("EMAIL_NOTIFICATIONS_ENABLED"):
            send_contract_notification(instance, "created")

    else:
        old_instance = Contract.objects.get(pk=instance.pk)
        if old_instance.status != instance.status:
            AuditLog.log_action(
                user=instance.employee,
                action="CONTRACT_STATUS_CHANGED",
                description=f"Contract {instance.contract_number} status changed from {old_instance.status} to {instance.status}",
                ip_address="127.0.0.1",
                additional_data={
                    "contract_number": instance.contract_number,
                    "old_status": old_instance.status,
                    "new_status": instance.status,
                },
            )

            if SystemConfiguration.get_bool_setting("EMAIL_NOTIFICATIONS_ENABLED"):
                send_contract_notification(instance, "status_changed")


@receiver(post_save, sender=Contract)
def update_employee_profile_on_contract_activation(sender, instance, **kwargs):
    if instance.status == "ACTIVE":
        try:
            employee_profile = instance.employee.employee_profile

            if instance.contract_type == "PERMANENT":
                employee_profile.employment_status = "CONFIRMED"
            elif instance.contract_type == "PROBATION":
                employee_profile.employment_status = "PROBATION"
            elif instance.contract_type == "FIXED_TERM":
                employee_profile.employment_status = "CONTRACT"

            employee_profile.basic_salary = instance.basic_salary
            employee_profile.save()

        except EmployeeProfile.DoesNotExist:
            pass


@receiver(post_delete, sender=EmployeeProfile)
def log_employee_profile_deletion(sender, instance, **kwargs):
    AuditLog.log_action(
        user=None,
        action="USER_DELETED",
        description=f"Employee profile deleted for {instance.user.get_full_name()}",
        ip_address="127.0.0.1",
        additional_data={
            "employee_id": instance.employee_id,
            "deleted_at": timezone.now().isoformat(),
        },
    )


def send_employment_status_notification(employee_profile, old_status):
    try:
        subject = f"Employment Status Update - {employee_profile.user.get_full_name()}"

        if employee_profile.employment_status == "CONFIRMED":
            message = f"""
            Dear {employee_profile.user.first_name},
            
            Congratulations! Your employment status has been updated to CONFIRMED.
            Your confirmation date is {employee_profile.confirmation_date}.
            
            Best regards,
            HR Department
            """
        else:
            message = f"""
            Dear {employee_profile.user.first_name},
            
            Your employment status has been updated from {old_status} to {employee_profile.employment_status}.
            
            If you have any questions, please contact the HR department.
            
            Best regards,
            HR Department
            """

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[employee_profile.user.email],
            fail_silently=True,
        )
    except Exception:
        pass


def send_contract_notification(contract, notification_type):
    try:
        if notification_type == "created":
            subject = f"New Contract Created - {contract.contract_number}"
            message = f"""
            Dear {contract.employee.first_name},
            
            A new contract ({contract.contract_number}) has been created for you.
            
            Contract Details:
            - Type: {contract.get_contract_type_display()}
            - Start Date: {contract.start_date}
            - End Date: {contract.end_date or 'Permanent'}
            - Basic Salary: {contract.basic_salary}
            
            Please review the contract details and contact HR if you have any questions.
            
            Best regards,
            HR Department
            """

        elif notification_type == "status_changed":
            subject = f"Contract Status Update - {contract.contract_number}"
            message = f"""
            Dear {contract.employee.first_name},
            
            Your contract ({contract.contract_number}) status has been updated to {contract.get_status_display()}.
            
            If you have any questions, please contact the HR department.
            
            Best regards,
            HR Department
            """

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[contract.employee.email],
            fail_silently=True,
        )
    except Exception:
        pass


def send_probation_ending_reminders():
    from django.core.management.base import BaseCommand

    reminder_days = SystemConfiguration.get_int_setting("PROBATION_REMINDER_DAYS", 7)
    reminder_date = timezone.now().date() + timedelta(days=reminder_days)

    employees_on_probation = EmployeeProfile.objects.filter(
        employment_status="PROBATION", probation_end_date=reminder_date, is_active=True
    )

    for employee_profile in employees_on_probation:
        try:
            subject = f"Probation Period Ending Soon - {employee_profile.user.get_full_name()}"
            message = f"""
            Dear {employee_profile.user.first_name},
            
            This is a reminder that your probation period will end on {employee_profile.probation_end_date}.
            
            Please ensure all required documentation and evaluations are completed.
            
            If you have any questions, please contact your manager or HR department.
            
            Best regards,
            HR Department
            """

            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[employee_profile.user.email],
                fail_silently=True,
            )

            if employee_profile.user.manager:
                manager_subject = f"Employee Probation Ending - {employee_profile.user.get_full_name()}"
                manager_message = f"""
                Dear {employee_profile.user.manager.first_name},
                
                This is a reminder that {employee_profile.user.get_full_name()}'s probation period will end on {employee_profile.probation_end_date}.
                
                Please complete the probation evaluation and make a decision regarding confirmation.
                
                Best regards,
                HR Department
                """

                send_mail(
                    subject=manager_subject,
                    message=manager_message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[employee_profile.user.manager.email],
                    fail_silently=True,
                )
        except Exception:
            continue


def send_contract_expiry_reminders():
    reminder_days = SystemConfiguration.get_int_setting(
        "CONTRACT_EXPIRY_REMINDER_DAYS", 30
    )
    reminder_date = timezone.now().date() + timedelta(days=reminder_days)

    expiring_contracts = Contract.objects.filter(
        status="ACTIVE", end_date=reminder_date, is_active=True
    )

    for contract in expiring_contracts:
        try:
            subject = f"Contract Expiring Soon - {contract.contract_number}"
            message = f"""
            Dear {contract.employee.first_name},
            
            This is a reminder that your contract ({contract.contract_number}) will expire on {contract.end_date}.
            
            Please contact HR department to discuss renewal or next steps.
            
            Best regards,
            HR Department
            """

            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[contract.employee.email],
                fail_silently=True,
            )

            hr_subject = f"Contract Expiring - {contract.contract_number}"
            hr_message = f"""
            Contract {contract.contract_number} for {contract.employee.get_full_name()} will expire on {contract.end_date}.
            
            Please take necessary action for renewal or termination.
            """

            hr_emails = CustomUser.objects.filter(
                role__name__in=["HR_ADMIN", "HR_MANAGER"], is_active=True
            ).values_list("email", flat=True)

            if hr_emails:
                send_mail(
                    subject=hr_subject,
                    message=hr_message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=list(hr_emails),
                    fail_silently=True,
                )
        except Exception:
            continue
