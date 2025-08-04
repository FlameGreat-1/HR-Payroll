from django.apps import AppConfig


class PayrollConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payroll"

    def ready(self):
        """Import signals when Django starts"""
        try:
            # Import signals to register them
            import payroll.signals

            # Initialize signal handlers
            from payroll.signals import initialize_payroll_signal_handlers

            initialize_payroll_signal_handlers()

        except ImportError as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error importing payroll signals: {e}")
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error initializing payroll signals: {e}")
