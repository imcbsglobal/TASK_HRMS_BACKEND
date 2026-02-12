# master/models.py
from django.db import models


class LeaveType(models.Model):
    """
    Model to store different types of leaves
    """
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Name of the leave type (e.g., Sick Leave, Annual Leave)"
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Description of the leave type"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this leave type is currently active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'master_leave_types'
        ordering = ['name']
        verbose_name = 'Leave Type'
        verbose_name_plural = 'Leave Types'

    def __str__(self):
        return self.name