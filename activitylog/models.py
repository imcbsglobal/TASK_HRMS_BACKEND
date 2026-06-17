from django.db import models
from login.models import User


class ActivityLog(models.Model):
    # Action types
    ACTION_TYPE_CHOICES = (
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
        ('OTHER', 'Other'),
    )

    # Core fields
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='activity_logs',
        help_text="User who performed the action"
    )
    action_type = models.CharField(
        max_length=20,
        choices=ACTION_TYPE_CHOICES,
        default='OTHER',
        help_text="Type of action performed"
    )
    module = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text="Module/section where the action occurred (e.g., Attendance, Payroll, Employees)"
    )
    description = models.TextField(
        help_text="Detailed description of the action"
    )
    ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        help_text="IP address of the user when the action was performed"
    )
    user_agent = models.TextField(
        blank=True,
        default='',
        help_text="User agent string of the user's browser/device"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)

    # Tenant isolation (using user's admin owner)
    admin_owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='admin_activity_logs',
        help_text="Admin owner for tenant isolation (auto-filled from user)"
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Activity Logs'

    def __str__(self):
        return f"{self.user.username} - {self.action_type} - {self.created_at}"

    def save(self, *args, **kwargs):
        # Auto-fill admin_owner based on user
        if not self.admin_owner:
            if self.user.admin_owner:
                self.admin_owner = self.user.admin_owner
            elif self.user.role in ('ADMIN', 'SUPER_ADMIN'):
                self.admin_owner = self.user
        super().save(*args, **kwargs)

