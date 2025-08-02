from django.contrib.admin import AdminSite
from django.urls import reverse
from django.utils.html import format_html
from django.template.response import TemplateResponse
from django.db.models import Count, Avg
from django.utils import timezone
from datetime import timedelta

from employees.models import EmployeeProfile, Education, Contract
from employees.admin import EmployeeProfileAdmin, EducationAdmin, ContractAdmin

class HRAdminSite(AdminSite):
    site_header = "HR Payroll System"
    site_title = "HR Admin Portal"
    index_title = ""
    site_url = None
    enable_nav_sidebar = True

    def index(self, request, extra_context=None):
        """Custom dashboard with HR statistics"""
        from .models import CustomUser, Department, Role, AuditLog, UserSession
        from employees.models import EmployeeProfile, Education, Contract
        from employees.utils import EmployeeUtils, ContractUtils

        # ... rest of your original index method code stays exactly the same ...
        extra_context = extra_context or {}

        # Basic HR Statistics
        total_employees = CustomUser.objects.filter(is_active=True).count()
        new_employees_this_month = CustomUser.objects.filter(
            date_joined__gte=timezone.now().replace(day=1)
        ).count()
        active_sessions = UserSession.objects.filter(is_active=True).count()
        recent_activities = AuditLog.objects.select_related("user").order_by(
            "-timestamp"
        )[:10]

        # Employee Module Statistics
        employee_stats = EmployeeUtils.get_employee_summary_stats()
        contract_stats = ContractUtils.get_contract_summary_stats()

        # Probation Alerts
        probation_ending_soon = EmployeeProfile.objects.filter(
            employment_status="PROBATION",
            probation_end_date__lte=timezone.now().date() + timedelta(days=7),
            probation_end_date__gte=timezone.now().date(),
            is_active=True,
        ).count()

        # Contract Expiry Alerts
        contracts_expiring_soon = Contract.objects.filter(
            status="ACTIVE",
            end_date__lte=timezone.now().date() + timedelta(days=30),
            end_date__gte=timezone.now().date(),
            is_active=True,
        ).count()

        # Education Verification Pending
        pending_education_verification = Education.objects.filter(
            is_verified=False, is_active=True
        ).count()

        # Department statistics with employee profiles
        dept_stats = Department.objects.annotate(
            employee_count=Count("employees"),
            profile_count=Count("employees__employee_profile"),
        ).order_by("-employee_count")[:5]

        # Recent Employee Activities
        recent_employee_activities = (
            AuditLog.objects.filter(
                action__in=[
                    "USER_CREATED",
                    "PROFILE_UPDATE",
                    "CONTRACT_CREATED",
                    "EDUCATION_ADDED",
                ]
            )
            .select_related("user")
            .order_by("-timestamp")[:5]
        )

        extra_context.update(
            {
                "hr_stats": {
                    "total_employees": total_employees,
                    "new_employees_this_month": new_employees_this_month,
                    "active_sessions": active_sessions,
                    "total_departments": Department.objects.filter(
                        is_active=True
                    ).count(),
                    "total_roles": Role.objects.filter(is_active=True).count(),
                },
                "employee_stats": {
                    "total_profiles": employee_stats["total_employees"],
                    "on_probation": employee_stats["by_employment_status"].get(
                        "PROBATION", 0
                    ),
                    "confirmed": employee_stats["by_employment_status"].get(
                        "CONFIRMED", 0
                    ),
                    "average_salary": employee_stats["salary_stats"]["avg_salary"] or 0,
                    "probation_ending_soon": probation_ending_soon,
                },
                "contract_stats": {
                    "total_contracts": contract_stats["total_contracts"],
                    "active_contracts": contract_stats["by_status"].get("ACTIVE", 0),
                    "expiring_soon": contracts_expiring_soon,
                    "expired": contract_stats["expired"],
                },
                "alerts": {
                    "probation_ending": probation_ending_soon,
                    "contracts_expiring": contracts_expiring_soon,
                    "pending_verification": pending_education_verification,
                },
                "recent_activities": recent_activities,
                "recent_employee_activities": recent_employee_activities,
                "dept_stats": dept_stats,
                "current_user": request.user,
            }
        )

        return super().index(request, extra_context)


# Create custom admin site instance
hr_admin_site = HRAdminSite(name="hr_admin")
