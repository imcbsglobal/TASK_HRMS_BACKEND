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
        ('leave', 'On Leave'),
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
    
    # Location tracking fields
    check_in_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_in_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_in_address = models.TextField(blank=True, null=True)
    
    check_out_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_out_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_out_address = models.TextField(blank=True, null=True)
    
    # Admin verification fields
    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='verified_attendances'
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    
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
    
    def get_check_in_map_url(self):
        if self.check_in_latitude and self.check_in_longitude:
            return f"https://www.google.com/maps?q={self.check_in_latitude},{self.check_in_longitude}"
        return None
    
    def get_check_out_map_url(self):
        if self.check_out_latitude and self.check_out_longitude:
            return f"https://www.google.com/maps?q={self.check_out_latitude},{self.check_out_longitude}"
        return None
    
    def calculate_hours(self):
        if self.check_in_time and self.check_out_time:
            delta = self.check_out_time - self.check_in_time
            hours = delta.total_seconds() / 3600
            self.total_hours = round(hours, 2)
            return self.total_hours
        return 0.00
    
    def determine_status(self):
        if self.is_verified:
            return
        if self.check_in_time:
            if self.check_out_time:
                self.status = 'present'
            else:
                self.status = 'half_day'
        else:
            self.status = 'absent'
    
    def save(self, *args, **kwargs):
        if self.check_in_time and self.check_out_time:
            self.calculate_hours()
        self.determine_status()
        super().save(*args, **kwargs)


class LeaveRequest(models.Model):
    """Leave request model for employees to request time off"""
    
    LEAVE_TYPE_CHOICES = [
        ('sick', 'Sick Leave'),
        ('casual', 'Casual Leave'),
        ('annual', 'Annual Leave'),
        ('maternity', 'Maternity Leave'),
        ('paternity', 'Paternity Leave'),
        ('unpaid', 'Unpaid Leave'),
        ('other', 'Other'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='leave_requests'
    )
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES, default='casual')
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Admin action fields
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_leave_requests'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Leave Request'
        verbose_name_plural = 'Leave Requests'
    
    def __str__(self):
        return f"{self.user.username} - {self.leave_type} - {self.start_date} to {self.end_date} ({self.status})"
    
    @property
    def total_days(self):
        """Calculate total leave days (inclusive)"""
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return 0


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