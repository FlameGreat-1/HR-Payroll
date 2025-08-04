from django.contrib.admin import AdminSite
from django.urls import reverse
from django.utils.html import format_html
from django.template.response import TemplateResponse
from django.db.models import Count, Avg, Sum, Q, Max, Min
from django.utils import timezone
from datetime import timedelta, date
from decimal import Decimal
from collections import defaultdict

from .models import (
    CustomUser,
    Department,
    Role,
    AuditLog,
    UserSession,
    SystemConfiguration,
    PasswordResetToken,
    APIKey,
)
from employees.models import EmployeeProfile, Education, Contract
from employees.admin import EmployeeProfileAdmin, EducationAdmin, ContractAdmin
from core.models import AuditLog
class HRAdminSite(AdminSite):
    site_header = "HR Payroll System"
    site_title = "HR Admin Portal"
    index_title = "Enterprise HR Analytics Dashboard"
    site_url = None
    enable_nav_sidebar = True

    def index(self, request, extra_context=None):
        extra_context = extra_context or {}
        current_date = timezone.now().date()
        current_month_start = timezone.now().replace(day=1).date()

        try:
            hr_metrics = self._get_hr_core_metrics()
            security_metrics = self._get_security_metrics()
            employee_analytics = self._get_employee_analytics()
            education_analytics = self._get_education_analytics()
            contract_analytics = self._get_contract_analytics()
            department_analytics = self._get_department_analytics()
            role_analytics = self._get_role_analytics()
            session_analytics = self._get_session_analytics()
            audit_analytics = self._get_audit_analytics()
            alert_system = self._get_alert_system()

            recent_activities = AuditLog.objects.select_related("user").order_by(
                "-timestamp"
            )[:10]
            dept_stats = Department.objects.annotate(
                employee_count=Count("employees", filter=Q(employees__is_active=True))
            ).order_by("-employee_count")[:5]

            extra_context.update(
                {
                    "hr_stats": {
                        "total_employees": hr_metrics["total_employees"],
                        "new_employees_this_month": hr_metrics["new_hires_this_month"],
                        "active_sessions": session_analytics["active_sessions"],
                        "total_departments": hr_metrics["active_departments"],
                        "total_roles": hr_metrics["active_roles"],
                    },
                    "hr_metrics": hr_metrics,
                    "security_metrics": security_metrics,
                    "employee_analytics": employee_analytics,
                    "education_analytics": education_analytics,
                    "contract_analytics": contract_analytics,
                    "department_analytics": department_analytics,
                    "role_analytics": role_analytics,
                    "session_analytics": session_analytics,
                    "audit_analytics": audit_analytics,
                    "alert_system": alert_system,
                    "recent_activities": recent_activities,
                    "dept_stats": dept_stats,
                    "current_user": request.user,
                    "dashboard_updated": timezone.now(),
                }
            )

        except Exception as e:
            extra_context.update(
                {
                    "hr_stats": {
                        "total_employees": 0,
                        "new_employees_this_month": 0,
                        "active_sessions": 0,
                        "total_departments": 0,
                        "total_roles": 0,
                    },
                    "hr_metrics": {
                        "total_employees": 0,
                        "total_profiles": 0,
                        "new_hires_this_month": 0,
                        "active_departments": 0,
                        "active_roles": 0,
                        "total_payroll": 0,
                        "average_salary": 0,
                        "profile_completion_rate": 0,
                    },
                    "security_metrics": {
                        "locked_accounts": 0,
                        "failed_login_users": 0,
                        "password_expired_count": 0,
                        "unverified_accounts": 0,
                        "recent_password_resets": 0,
                        "active_api_keys": 0,
                        "expired_api_keys": 0,
                        "security_score": {
                            "score": 100,
                            "grade": "A",
                            "status": "excellent",
                        },
                    },
                    "employee_analytics": {
                        "employment_status_distribution": [],
                        "grade_level_distribution": [],
                        "marital_status_distribution": [],
                        "salary_by_grade": [],
                        "probation_employees": 0,
                        "confirmed_employees": 0,
                        "years_of_service_distribution": {},
                        "total_active_profiles": 0,
                    },
                    "education_analytics": {
                        "education_level_distribution": [],
                        "verification_status": [],
                        "pending_verifications": 0,
                        "recent_verifications": 0,
                        "top_institutions": [],
                        "education_by_department": [],
                        "total_education_records": 0,
                    },
                    "contract_analytics": {
                        "contract_type_distribution": [],
                        "contract_status_distribution": [],
                        "active_contracts": 0,
                        "expiring_30_days": 0,
                        "expiring_7_days": 0,
                        "expired_contracts": 0,
                        "contract_value_by_type": [],
                        "unsigned_contracts": 0,
                        "probation_period_analysis": [],
                        "total_contract_value": 0,
                    },
                    "department_analytics": {
                        "department_employee_count": [],
                        "department_salary_analysis": [],
                        "department_contract_analysis": [],
                        "largest_departments": [],
                        "highest_paid_departments": [],
                        "total_departments": 0,
                    },
                    "role_analytics": {
                        "role_distribution": [],
                        "role_salary_analysis": [],
                        "role_permissions": [],
                        "management_roles": [],
                        "role_level_distribution": [],
                        "total_roles": 0,
                    },
                    "session_analytics": {
                        "active_sessions": 0,
                        "sessions_today": 0,
                        "device_distribution": [],
                        "location_distribution": [],
                        "peak_login_hours": [],
                        "recent_logins": [],
                        "concurrent_users": 0,
                    },
                    "audit_analytics": {
                        "total_audit_logs": 0,
                        "logs_today": 0,
                        "action_distribution": [],
                        "user_activity_ranking": [],
                        "model_activity": [],
                        "recent_critical_actions": [],
                        "login_attempts": 0,
                        "failed_logins": 0,
                        "success_rate": 100,
                    },
                    "alert_system": {
                        "probation_ending": {
                            "count": 0,
                            "items": [],
                            "priority": "low",
                        },
                        "contract_expiry": {"count": 0, "items": [], "priority": "low"},
                        "education_verification": {
                            "count": 0,
                            "items": [],
                            "priority": "low",
                        },
                        "password_expiry": {"count": 0, "items": [], "priority": "low"},
                        "security_issues": {"count": 0, "items": [], "priority": "low"},
                        "document_missing": {
                            "count": 0,
                            "items": [],
                            "priority": "low",
                        },
                        "salary_review": {"count": 0, "items": [], "priority": "low"},
                        "api_expiry": {"count": 0, "items": [], "priority": "low"},
                        "inactive_sessions": {
                            "count": 0,
                            "items": [],
                            "priority": "low",
                        },
                        "compliance_issues": {
                            "missing_profiles": 0,
                            "incomplete_profiles": 0,
                            "missing_contracts": 0,
                        },
                    },
                    "recent_activities": [],
                    "dept_stats": [],
                    "current_user": request.user,
                    "error_message": str(e),
                }
            )

        return super().index(request, extra_context)

    def _get_hr_core_metrics(self):
        current_date = timezone.now().date()
        current_month_start = timezone.now().replace(day=1).date()

        total_users = CustomUser.objects.filter(is_active=True).count()
        total_profiles = EmployeeProfile.objects.filter(is_active=True).count()
        new_hires_this_month = CustomUser.objects.filter(
            hire_date__gte=current_month_start, is_active=True
        ).count()

        active_departments = Department.objects.filter(is_active=True).count()
        active_roles = Role.objects.filter(is_active=True).count()

        total_payroll = EmployeeProfile.objects.filter(is_active=True).aggregate(
            total=Sum("basic_salary")
        )["total"] or Decimal("0.00")

        avg_salary = EmployeeProfile.objects.filter(is_active=True).aggregate(
            avg=Avg("basic_salary")
        )["avg"] or Decimal("0.00")

        return {
            "total_employees": total_users,
            "total_profiles": total_profiles,
            "new_hires_this_month": new_hires_this_month,
            "active_departments": active_departments,
            "active_roles": active_roles,
            "total_payroll": total_payroll,
            "average_salary": avg_salary,
            "profile_completion_rate": (
                (total_profiles / total_users * 100) if total_users > 0 else 0
            ),
        }

    def _get_security_metrics(self):
        current_time = timezone.now()

        locked_accounts = CustomUser.objects.filter(
            account_locked_until__gt=current_time
        ).count()

        failed_login_users = CustomUser.objects.filter(
            failed_login_attempts__gt=0
        ).count()

        password_expired_users = [
            user
            for user in CustomUser.objects.filter(is_active=True)
            if user.is_password_expired()
        ]

        unverified_accounts = CustomUser.objects.filter(
            is_verified=False, is_active=True
        ).count()

        recent_password_resets = PasswordResetToken.objects.filter(
            created_at__gte=current_time - timedelta(days=7)
        ).count()

        active_api_keys = APIKey.objects.filter(is_active=True).count()
        expired_api_keys = APIKey.objects.filter(
            expires_at__lt=current_time, is_active=True
        ).count()

        return {
            "locked_accounts": locked_accounts,
            "failed_login_users": failed_login_users,
            "password_expired_count": len(password_expired_users),
            "unverified_accounts": unverified_accounts,
            "recent_password_resets": recent_password_resets,
            "active_api_keys": active_api_keys,
            "expired_api_keys": expired_api_keys,
            "security_score": self._calculate_security_score(
                locked_accounts,
                failed_login_users,
                len(password_expired_users),
                unverified_accounts,
            ),
        }

    def _get_employee_analytics(self):
        employment_status_dist = (
            EmployeeProfile.objects.filter(is_active=True)
            .values("employment_status")
            .annotate(count=Count("id"))
            .order_by("employment_status")
        )

        grade_level_dist = (
            EmployeeProfile.objects.filter(is_active=True)
            .values("grade_level")
            .annotate(count=Count("id"))
            .order_by("grade_level")
        )

        marital_status_dist = (
            EmployeeProfile.objects.filter(is_active=True, marital_status__isnull=False)
            .values("marital_status")
            .annotate(count=Count("id"))
            .order_by("marital_status")
        )

        salary_by_grade = (
            EmployeeProfile.objects.filter(is_active=True)
            .values("grade_level")
            .annotate(
                avg_salary=Avg("basic_salary"),
                min_salary=Min("basic_salary"),
                max_salary=Max("basic_salary"),
                count=Count("id"),
            )
            .order_by("grade_level")
        )

        probation_employees = EmployeeProfile.objects.filter(
            employment_status="PROBATION", is_active=True
        ).count()

        confirmed_employees = EmployeeProfile.objects.filter(
            employment_status="CONFIRMED", is_active=True
        ).count()

        years_of_service_dist = self._calculate_years_of_service_distribution()

        return {
            "employment_status_distribution": list(employment_status_dist),
            "grade_level_distribution": list(grade_level_dist),
            "marital_status_distribution": list(marital_status_dist),
            "salary_by_grade": list(salary_by_grade),
            "probation_employees": probation_employees,
            "confirmed_employees": confirmed_employees,
            "years_of_service_distribution": years_of_service_dist,
            "total_active_profiles": EmployeeProfile.objects.filter(
                is_active=True
            ).count(),
        }

    def _get_education_analytics(self):
        education_level_dist = (
            Education.objects.filter(is_active=True)
            .values("education_level")
            .annotate(count=Count("id"))
            .order_by("education_level")
        )

        verification_status = (
            Education.objects.filter(is_active=True)
            .values("is_verified")
            .annotate(count=Count("id"))
        )

        pending_verifications = Education.objects.filter(
            is_verified=False, is_active=True
        ).count()

        recent_verifications = Education.objects.filter(
            verified_at__gte=timezone.now() - timedelta(days=30), is_verified=True
        ).count()

        top_institutions = (
            Education.objects.filter(is_active=True)
            .values("institution")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        education_by_department = (
            Education.objects.filter(is_active=True, employee__department__isnull=False)
            .values("employee__department__name", "education_level")
            .annotate(count=Count("id"))
            .order_by("employee__department__name", "education_level")
        )

        return {
            "education_level_distribution": list(education_level_dist),
            "verification_status": list(verification_status),
            "pending_verifications": pending_verifications,
            "recent_verifications": recent_verifications,
            "top_institutions": list(top_institutions),
            "education_by_department": list(education_by_department),
            "total_education_records": Education.objects.filter(is_active=True).count(),
        }

    def _get_contract_analytics(self):
        current_date = timezone.now().date()

        contract_type_dist = (
            Contract.objects.filter(is_active=True)
            .values("contract_type")
            .annotate(count=Count("id"))
            .order_by("contract_type")
        )

        contract_status_dist = (
            Contract.objects.filter(is_active=True)
            .values("status")
            .annotate(count=Count("id"))
            .order_by("status")
        )

        active_contracts = Contract.objects.filter(
            status="ACTIVE", is_active=True
        ).count()

        expiring_contracts_30 = Contract.objects.filter(
            status="ACTIVE",
            end_date__lte=current_date + timedelta(days=30),
            end_date__gte=current_date,
            is_active=True,
        ).count()

        expiring_contracts_7 = Contract.objects.filter(
            status="ACTIVE",
            end_date__lte=current_date + timedelta(days=7),
            end_date__gte=current_date,
            is_active=True,
        ).count()

        expired_contracts = Contract.objects.filter(
            end_date__lt=current_date, status="ACTIVE", is_active=True
        ).count()

        contract_value_by_type = (
            Contract.objects.filter(is_active=True, status="ACTIVE")
            .values("contract_type")
            .annotate(
                total_value=Sum("basic_salary"),
                avg_value=Avg("basic_salary"),
                count=Count("id"),
            )
            .order_by("-total_value")
        )

        unsigned_contracts = Contract.objects.filter(
            signed_date__isnull=True, status="ACTIVE", is_active=True
        ).count()

        probation_period_analysis = (
            Contract.objects.filter(is_active=True)
            .values("probation_period_months")
            .annotate(count=Count("id"))
            .order_by("probation_period_months")
        )

        return {
            "contract_type_distribution": list(contract_type_dist),
            "contract_status_distribution": list(contract_status_dist),
            "active_contracts": active_contracts,
            "expiring_30_days": expiring_contracts_30,
            "expiring_7_days": expiring_contracts_7,
            "expired_contracts": expired_contracts,
            "contract_value_by_type": list(contract_value_by_type),
            "unsigned_contracts": unsigned_contracts,
            "probation_period_analysis": list(probation_period_analysis),
            "total_contract_value": Contract.objects.filter(
                status="ACTIVE", is_active=True
            ).aggregate(total=Sum("basic_salary"))["total"]
            or Decimal("0.00"),
        }

    def _get_department_analytics(self):
        dept_employee_count = (
            Department.objects.filter(is_active=True)
            .annotate(
                employee_count=Count("employees", filter=Q(employees__is_active=True)),
                profile_count=Count(
                    "employees__employee_profile",
                    filter=Q(employees__employee_profile__is_active=True),
                ),
            )
            .order_by("-employee_count")
        )

        dept_salary_analysis = (
            Department.objects.filter(is_active=True)
            .annotate(
                total_salary=Sum(
                    "employees__employee_profile__basic_salary",
                    filter=Q(employees__employee_profile__is_active=True),
                ),
                avg_salary=Avg(
                    "employees__employee_profile__basic_salary",
                    filter=Q(employees__employee_profile__is_active=True),
                ),
                employee_count=Count("employees", filter=Q(employees__is_active=True)),
            )
            .order_by("-total_salary")
        )

        dept_grade_distribution = (
            Department.objects.filter(is_active=True)
            .prefetch_related("employees__employee_profile")
            .annotate(
                employee_count=Count("employees", filter=Q(employees__is_active=True))
            )
            .filter(employee_count__gt=0)
        )

        dept_contract_analysis = (
            Department.objects.filter(is_active=True)
            .annotate(
                active_contracts=Count(
                    "contracts",
                    filter=Q(contracts__status="ACTIVE", contracts__is_active=True),
                ),
                total_contract_value=Sum(
                    "contracts__basic_salary",
                    filter=Q(contracts__status="ACTIVE", contracts__is_active=True),
                ),
            )
            .order_by("-active_contracts")
        )

        largest_departments = dept_employee_count[:5]
        highest_paid_departments = dept_salary_analysis[:5]

        return {
            "department_employee_count": list(dept_employee_count),
            "department_salary_analysis": list(dept_salary_analysis),
            "department_contract_analysis": list(dept_contract_analysis),
            "largest_departments": list(largest_departments),
            "highest_paid_departments": list(highest_paid_departments),
            "total_departments": Department.objects.filter(is_active=True).count(),
        }

    def _get_role_analytics(self):
        role_distribution = (
            Role.objects.filter(is_active=True)
            .annotate(user_count=Count("users", filter=Q(users__is_active=True)))
            .order_by("-user_count")
        )

        role_salary_analysis = (
            Role.objects.filter(is_active=True)
            .annotate(
                avg_salary=Avg(
                    "users__employee_profile__basic_salary",
                    filter=Q(users__employee_profile__is_active=True),
                ),
                total_salary=Sum(
                    "users__employee_profile__basic_salary",
                    filter=Q(users__employee_profile__is_active=True),
                ),
                user_count=Count("users", filter=Q(users__is_active=True)),
            )
            .order_by("-avg_salary")
        )

        role_permissions = (
            Role.objects.filter(is_active=True)
            .annotate(permission_count=Count("permissions"))
            .order_by("-permission_count")
        )

        management_roles = Role.objects.filter(
            can_manage_employees=True, is_active=True
        ).annotate(user_count=Count("users", filter=Q(users__is_active=True)))

        role_level_distribution = (
            Role.objects.filter(is_active=True)
            .values("level")
            .annotate(
                count=Count("id"),
                user_count=Count("users", filter=Q(users__is_active=True)),
            )
            .order_by("level")
        )

        return {
            "role_distribution": list(role_distribution),
            "role_salary_analysis": list(role_salary_analysis),
            "role_permissions": list(role_permissions),
            "management_roles": list(management_roles),
            "role_level_distribution": list(role_level_distribution),
            "total_roles": Role.objects.filter(is_active=True).count(),
        }

    def _get_session_analytics(self):
        current_time = timezone.now()

        active_sessions = UserSession.objects.filter(is_active=True).count()

        sessions_today = UserSession.objects.filter(
            login_time__date=current_time.date()
        ).count()

        device_distribution = (
            UserSession.objects.filter(is_active=True)
            .values("device_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        location_distribution = (
            UserSession.objects.filter(is_active=True, location__isnull=False)
            .values("location")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        peak_login_hours = []

        recent_logins = (
            UserSession.objects.filter(
                login_time__gte=current_time - timedelta(hours=24)
            )
            .select_related("user")
            .order_by("-login_time")[:10]
        )

        concurrent_users = (
            UserSession.objects.filter(is_active=True).values("user").distinct().count()
        )

        return {
            "active_sessions": active_sessions,
            "sessions_today": sessions_today,
            "device_distribution": list(device_distribution),
            "location_distribution": list(location_distribution),
            "peak_login_hours": list(peak_login_hours),
            "recent_logins": list(recent_logins),
            "concurrent_users": concurrent_users,
        }

    def _get_audit_analytics(self):
        current_time = timezone.now()

        total_audit_logs = AuditLog.objects.count()

        logs_today = AuditLog.objects.filter(
            timestamp__date=current_time.date()
        ).count()

        action_distribution = (
            AuditLog.objects.values("action")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        user_activity_ranking = (
            AuditLog.objects.filter(
                user__isnull=False, timestamp__gte=current_time - timedelta(days=30)
            )
            .values("user__employee_code", "user__first_name", "user__last_name")
            .annotate(activity_count=Count("id"))
            .order_by("-activity_count")[:10]
        )

        model_activity = (
            AuditLog.objects.filter(model_name__isnull=False)
            .values("model_name")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        recent_critical_actions = (
            AuditLog.objects.filter(
                action__in=["DELETE", "PERMISSION_CHANGE", "SYSTEM_CHANGE"],
                timestamp__gte=current_time - timedelta(days=7),
            )
            .select_related("user")
            .order_by("-timestamp")[:10]
        )

        login_attempts = AuditLog.objects.filter(
            action="LOGIN", timestamp__gte=current_time - timedelta(days=30)
        ).count()

        failed_logins = 0

        return {
            "total_audit_logs": total_audit_logs,
            "logs_today": logs_today,
            "action_distribution": list(action_distribution),
            "user_activity_ranking": list(user_activity_ranking),
            "model_activity": list(model_activity),
            "recent_critical_actions": list(recent_critical_actions),
            "login_attempts": login_attempts,
            "failed_logins": failed_logins,
            "success_rate": (
                100
                if login_attempts == 0
                else ((login_attempts - failed_logins) / login_attempts * 100)
            ),
        }

    def _get_alert_system(self):
        current_date = timezone.now().date()
        current_time = timezone.now()

        probation_ending_alerts = (
            EmployeeProfile.objects.filter(
                employment_status="PROBATION",
                probation_end_date__lte=current_date + timedelta(days=7),
                probation_end_date__gte=current_date,
                is_active=True,
            )
            .select_related("user")
            .order_by("probation_end_date")
        )

        contract_expiry_alerts = (
            Contract.objects.filter(
                status="ACTIVE",
                end_date__lte=current_date + timedelta(days=30),
                end_date__gte=current_date,
                is_active=True,
            )
            .select_related("employee")
            .order_by("end_date")
        )

        education_verification_alerts = (
            Education.objects.filter(
                is_verified=False,
                created_at__lte=current_time - timedelta(days=30),
                is_active=True,
            )
            .select_related("employee")
            .order_by("created_at")
        )

        password_expiry_alerts = [
            user
            for user in CustomUser.objects.filter(is_active=True)
            if user.is_password_expired()
            or (
                user.password_changed_at
                and user.password_changed_at <= current_time - timedelta(days=80)
            )
        ]

        security_alerts = CustomUser.objects.filter(
            Q(account_locked_until__gt=current_time)
            | Q(failed_login_attempts__gte=3)
            | Q(is_verified=False, is_active=True)
        ).order_by("-failed_login_attempts")

        document_alerts = Contract.objects.filter(
            signed_date__isnull=True,
            status="ACTIVE",
            created_at__lte=current_time - timedelta(days=7),
            is_active=True,
        ).select_related("employee")

        salary_review_alerts = (
            EmployeeProfile.objects.filter(
                updated_at__lte=current_time - timedelta(days=365), is_active=True
            )
            .select_related("user")
            .order_by("updated_at")
        )

        api_expiry_alerts = APIKey.objects.filter(
            expires_at__lte=current_time + timedelta(days=7),
            expires_at__gte=current_time,
            is_active=True,
        ).select_related("user")

        inactive_session_alerts = UserSession.objects.filter(
            last_activity__lte=current_time - timedelta(hours=24), is_active=True
        ).select_related("user")

        compliance_alerts = {
            "missing_profiles": CustomUser.objects.filter(
                is_active=True, employee_profile__isnull=True
            ).count(),
            "incomplete_profiles": EmployeeProfile.objects.filter(
                Q(bank_account_number__isnull=True)
                | Q(tax_identification_number__isnull=True),
                is_active=True,
            ).count(),
            "missing_contracts": CustomUser.objects.filter(
                is_active=True, contracts__isnull=True
            ).count(),
        }

        return {
            "probation_ending": {
                "count": probation_ending_alerts.count(),
                "items": list(probation_ending_alerts[:5]),
                "priority": "high" if probation_ending_alerts.count() > 0 else "low",
            },
            "contract_expiry": {
                "count": contract_expiry_alerts.count(),
                "items": list(contract_expiry_alerts[:5]),
                "priority": "high" if contract_expiry_alerts.count() > 5 else "medium",
            },
            "education_verification": {
                "count": education_verification_alerts.count(),
                "items": list(education_verification_alerts[:5]),
                "priority": (
                    "medium" if education_verification_alerts.count() > 10 else "low"
                ),
            },
            "password_expiry": {
                "count": len(password_expiry_alerts),
                "items": password_expiry_alerts[:5],
                "priority": "high" if len(password_expiry_alerts) > 0 else "low",
            },
            "security_issues": {
                "count": security_alerts.count(),
                "items": list(security_alerts[:5]),
                "priority": "critical" if security_alerts.count() > 0 else "low",
            },
            "document_missing": {
                "count": document_alerts.count(),
                "items": list(document_alerts[:5]),
                "priority": "medium" if document_alerts.count() > 0 else "low",
            },
            "salary_review": {
                "count": salary_review_alerts.count(),
                "items": list(salary_review_alerts[:5]),
                "priority": "low",
            },
            "api_expiry": {
                "count": api_expiry_alerts.count(),
                "items": list(api_expiry_alerts),
                "priority": "medium" if api_expiry_alerts.count() > 0 else "low",
            },
            "inactive_sessions": {
                "count": inactive_session_alerts.count(),
                "items": list(inactive_session_alerts[:10]),
                "priority": "low",
            },
            "compliance_issues": compliance_alerts,
        }

    def _calculate_security_score(
        self, locked_accounts, failed_login_users, password_expired, unverified_accounts
    ):
        total_users = CustomUser.objects.filter(is_active=True).count()
        if total_users == 0:
            return {"score": 100, "grade": "A", "status": "excellent"}

        security_issues = (
            locked_accounts
            + failed_login_users
            + password_expired
            + unverified_accounts
        )
        security_percentage = max(0, 100 - (security_issues / total_users * 100))

        if security_percentage >= 95:
            return {"score": security_percentage, "grade": "A", "status": "excellent"}
        elif security_percentage >= 85:
            return {"score": security_percentage, "grade": "B", "status": "good"}
        elif security_percentage >= 70:
            return {"score": security_percentage, "grade": "C", "status": "fair"}
        elif security_percentage >= 50:
            return {"score": security_percentage, "grade": "D", "status": "poor"}
        else:
            return {"score": security_percentage, "grade": "F", "status": "critical"}

    def _calculate_years_of_service_distribution(self):
        current_date = timezone.now().date()
        distribution = {
            "0-1 years": 0,
            "1-3 years": 0,
            "3-5 years": 0,
            "5-10 years": 0,
            "10+ years": 0,
        }

        active_employees = CustomUser.objects.filter(
            is_active=True, hire_date__isnull=False
        )

        for employee in active_employees:
            years_of_service = (current_date - employee.hire_date).days / 365.25

            if years_of_service < 1:
                distribution["0-1 years"] += 1
            elif years_of_service < 3:
                distribution["1-3 years"] += 1
            elif years_of_service < 5:
                distribution["3-5 years"] += 1
            elif years_of_service < 10:
                distribution["5-10 years"] += 1
            else:
                distribution["10+ years"] += 1

        return distribution

    def get_dashboard_summary(self):
        return {
            "total_employees": CustomUser.objects.filter(is_active=True).count(),
            "total_departments": Department.objects.filter(is_active=True).count(),
            "total_contracts": Contract.objects.filter(is_active=True).count(),
            "active_sessions": UserSession.objects.filter(is_active=True).count(),
            "pending_alerts": self._get_total_pending_alerts(),
            "system_health": self._get_system_health_score(),
        }

    def _get_total_pending_alerts(self):
        current_date = timezone.now().date()
        current_time = timezone.now()

        probation_alerts = EmployeeProfile.objects.filter(
            employment_status="PROBATION",
            probation_end_date__lte=current_date + timedelta(days=7),
            is_active=True,
        ).count()

        contract_alerts = Contract.objects.filter(
            status="ACTIVE",
            end_date__lte=current_date + timedelta(days=30),
            is_active=True,
        ).count()

        verification_alerts = Education.objects.filter(
            is_verified=False, is_active=True
        ).count()

        security_alerts = CustomUser.objects.filter(
            Q(account_locked_until__gt=current_time) | Q(failed_login_attempts__gte=3),
            is_active=True,
        ).count()

        return (
            probation_alerts + contract_alerts + verification_alerts + security_alerts
        )

    def _get_system_health_score(self):
        total_users = CustomUser.objects.filter(is_active=True).count()
        active_sessions = UserSession.objects.filter(is_active=True).count()
        recent_activities = AuditLog.objects.filter(
            timestamp__gte=timezone.now() - timedelta(hours=24)
        ).count()

        health_indicators = {
            "user_activity": min(100, (active_sessions / max(1, total_users)) * 100),
            "system_activity": min(100, recent_activities / 10),
            "data_completeness": self._calculate_data_completeness(),
            "security_health": 85,
        }

        overall_score = sum(health_indicators.values()) / len(health_indicators)

        return {
            "score": round(overall_score, 2),
            "indicators": health_indicators,
            "status": (
                "healthy"
                if overall_score >= 80
                else "warning" if overall_score >= 60 else "critical"
            ),
        }

    def _calculate_data_completeness(self):
        total_users = CustomUser.objects.filter(is_active=True).count()
        if total_users == 0:
            return 100

        complete_profiles = EmployeeProfile.objects.filter(
            is_active=True,
            bank_account_number__isnull=False,
            tax_identification_number__isnull=False,
        ).count()

        users_with_contracts = (
            CustomUser.objects.filter(is_active=True, contracts__status="ACTIVE")
            .distinct()
            .count()
        )

        profile_completeness = (complete_profiles / total_users) * 100
        contract_completeness = (users_with_contracts / total_users) * 100

        return (profile_completeness + contract_completeness) / 2


hr_admin_site = HRAdminSite(name="hr_admin")
