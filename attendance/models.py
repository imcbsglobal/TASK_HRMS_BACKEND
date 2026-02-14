from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

class Attendance(models.Model):
    STATUS_CHOICES = [
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('late', 'Late'),
        ('half_day', 'Half Day'),
    ]
    
    LATE_REQUEST_STATUS = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(default=timezone.now)
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='absent')
    total_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    notes = models.TextField(blank=True, null=True)
    
    # Late request fields
    late_request = models.BooleanField(default=False)
    late_request_reason = models.TextField(blank=True, null=True)
    late_request_status = models.CharField(
        max_length=20, 
        choices=LATE_REQUEST_STATUS, 
        null=True, 
        blank=True
    )
    late_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='approved_late_requests'
    )
    late_approved_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-date', '-check_in_time']
        unique_together = ['user', 'date']
        verbose_name_plural = 'Attendances'
    
    def __str__(self):
        return f"{self.user.username} - {self.date} - {self.status}"
    
    def calculate_hours(self):
        """Calculate total hours worked"""
        if self.check_in_time and self.check_out_time:
            delta = self.check_out_time - self.check_in_time
            hours = delta.total_seconds() / 3600
            self.total_hours = round(hours, 2)
            return self.total_hours
        return 0.00
    
    def determine_status(self):
        """Determine attendance status - simplified version"""
        # If late request is approved, mark as late
        if self.late_request and self.late_request_status == 'approved':
            self.status = 'late'
        # If checked in, mark as present
        elif self.check_in_time:
            # Check for half day based on total hours
            if self.check_out_time and self.total_hours > 0 and self.total_hours < 4:
                self.status = 'half_day'
            else:
                self.status = 'present'
        # Otherwise absent
        else:
            self.status = 'absent'
    
    def save(self, *args, **kwargs):
        # Calculate hours if both times are present
        if self.check_in_time and self.check_out_time:
            self.calculate_hours()
        
        # Determine status
        self.determine_status()
        
        super().save(*args, **kwargs)


class AttendanceSettings(models.Model):
    """Global attendance settings"""
    office_start_time = models.TimeField(default='09:00:00')
    office_end_time = models.TimeField(default='18:00:00')
    grace_period_minutes = models.IntegerField(default=15)
    minimum_hours_full_day = models.DecimalField(max_digits=4, decimal_places=2, default=8.00)
    minimum_hours_half_day = models.DecimalField(max_digits=4, decimal_places=2, default=4.00)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Attendance Settings'
        verbose_name_plural = 'Attendance Settings'
    
    def __str__(self):
        return f"Attendance Settings - {self.office_start_time} to {self.office_end_time}"