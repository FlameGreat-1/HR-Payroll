from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "attendance"
    verbose_name = "Attendance Management System"

    def ready(self):
        """
        Import signal handlers when Django starts.
        This ensures all signals are properly connected.
        """
        try:
            import attendance.signals  
        except ImportError:
            pass

        try:
            import attendance.tasks
        except ImportError:
            pass

        try:
            from .utils import CacheManager, AttendanceSettings

            CacheManager.initialize_cache()
            AttendanceSettings.initialize_default_settings()
        except ImportError:
            pass
