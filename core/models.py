from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

CustomUser = get_user_model()


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ("CREATE", "Create"),
        ("UPDATE", "Update"),
        ("DELETE", "Delete"),
        ("LOGIN", "Login"),
        ("LOGOUT", "Logout"),
        ("VIEW", "View"),
        ("PERMISSION_CHANGE", "Permission Change"),
        ("SYSTEM_CHANGE", "System Change"),
        ("BANK_TRANSFER_CREATED", "Bank Transfer Created"),
        ("BANK_TRANSFER_PROCESSED", "Bank Transfer Processed"),
        ("BANK_TRANSFER_COMPLETED", "Bank Transfer Completed"),
        ("BANK_TRANSFER_FAILED", "Bank Transfer Failed"),
        ("BANK_TRANSFER_PENDING", "Bank Transfer Pending"),
        ("BANK_TRANSFER_SENT", "Bank Transfer Sent"),
        ("BANK_TRANSFER_GENERATED", "Bank Transfer Generated"),
    ]

    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=100, null=True, blank=True)
    object_id = models.CharField(max_length=100, null=True, blank=True)
    description = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["timestamp"]),
            models.Index(fields=["user", "timestamp"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        return f"{self.user} - {self.action} - {self.timestamp}"

    @classmethod
    def log_action(
        cls,
        user,
        action,
        model_name=None,
        object_id=None,
        description=None,
        ip_address=None,
        user_agent=None,
    ):
        try:
            return cls.objects.create(
                user=user,
                action=action,
                model_name=model_name,
                object_id=object_id,
                description=description,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except Exception as e:
            print(f"Failed to create audit log: {e}")
            return None

    def get_action_display(self):
        return dict(self.ACTION_CHOICES).get(self.action, self.action)
