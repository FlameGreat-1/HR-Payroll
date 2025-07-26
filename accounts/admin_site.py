from django.contrib.admin import AdminSite
from django.urls import reverse
from django.utils.html import format_html
from django.template.response import TemplateResponse
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta


class HRAdminSite(AdminSite):
    site_header = "HR Payroll System"
    site_title = "HR Admin Portal"
    index_title = ""  
    site_url = None
    enable_nav_sidebar = True

    def index(self, request, extra_context=None):
        """Custom dashboard with HR statistics"""
        from .models import CustomUser, Department, Role, AuditLog, UserSession

        extra_context = extra_context or {}

        # HR Statistics
        total_employees = CustomUser.objects.filter(is_active=True).count()
        new_employees_this_month = CustomUser.objects.filter(
            date_joined__gte=timezone.now().replace(day=1)
        ).count()
        active_sessions = UserSession.objects.filter(is_active=True).count()
        recent_activities = AuditLog.objects.select_related("user").order_by(
            "-timestamp"
        )[:10]

        # Department statistics
        dept_stats = Department.objects.annotate(
            employee_count=Count("employees")
        ).order_by("-employee_count")[:5]

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
                "recent_activities": recent_activities,
                "dept_stats": dept_stats,
                "current_user": request.user,
            }
        )

        return super().index(request, extra_context)


# Create custom admin site instance
hr_admin_site = HRAdminSite(name="hr_admin")
