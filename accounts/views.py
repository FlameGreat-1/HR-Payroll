from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required

from .models import UserSession
from .utils import get_client_ip, get_user_agent, log_user_activity

User = get_user_model()


class BaseViewMixin:
    """Base mixin for any remaining custom views"""

    def get_client_context(self, request):
        return {
            "ip_address": get_client_ip(request),
            "user_agent": get_user_agent(request),
        }

    def log_activity(self, user, action, description, request, additional_data=None):
        log_user_activity(
            user=user,
            action=action,
            description=description,
            request=request,
            additional_data=additional_data or {},
        )


@csrf_exempt
def health_check(request):
    """System health check endpoint"""
    try:
        user_count = User.objects.count()
        active_sessions = UserSession.objects.filter(is_active=True).count()

        return JsonResponse(
            {
                "status": "healthy",
                "timestamp": timezone.now().isoformat(),
                "users": user_count,
                "active_sessions": active_sessions,
            }
        )

    except Exception as e:
        return JsonResponse(
            {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": timezone.now().isoformat(),
            },
            status=500,
        )


def handler404(request, exception):
    """Custom 404 error handler"""
    return render(request, "accounts/404.html", status=404)


def handler500(request):
    """Custom 500 error handler"""
    return render(request, "accounts/500.html", status=500)


def handler403(request, exception):
    """Custom 403 error handler"""
    return render(request, "accounts/403.html", status=403)
