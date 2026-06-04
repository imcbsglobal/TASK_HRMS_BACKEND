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
        ('waived', 'Waived'),
    ]

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_attendances',
        null=True, blank=True,
        help_text="The admin/tenant who owns this attendance record.",
    )
    # ─────────────────────────────────────────────────────────────────────────

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(default=timezone.now)
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    check_out_waived = models.BooleanField(default=False)
    is_wfh = models.BooleanField(default=False, help_text='True if this attendance is for a work-from-home day.')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='absent')
    total_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    total_break_minutes = models.IntegerField(default=0, help_text='Total break duration in minutes for this day.')
    notes = models.TextField(blank=True, null=True)

    check_in_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_in_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_in_address = models.TextField(blank=True, null=True)

    check_out_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_out_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_out_address = models.TextField(blank=True, null=True)

    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_attendances')
    verified_at = models.DateTimeField(null=True, blank=True)

    late_request = models.BooleanField(default=False)
    late_request_reason = models.TextField(blank=True, null=True)
    late_request_status = models.CharField(max_length=20, choices=LATE_REQUEST_STATUS, null=True, blank=True)
    late_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_late_requests')
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
                # Both check-in and check-out recorded → full day present
                self.status = 'present'
            else:
                # Checked in but not yet checked out → half day (in progress)
                self.status = 'half_day'
        else:
            self.status = 'absent'

    def save(self, *args, **kwargs):
        if self.check_in_time and self.check_out_time:
            self.calculate_hours()
        self.determine_status()
        super().save(*args, **kwargs)


class BreakRecord(models.Model):
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_break_records',
        null=True, blank=True,
        help_text="The admin/tenant who owns this break record.",
    )
    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.CASCADE,
        related_name='breaks',
        help_text='The attendance record this break belongs to.',
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='break_records')
    break_start = models.DateTimeField(help_text='When the break started.')
    break_end = models.DateTimeField(null=True, blank=True, help_text='When the break ended (null = ongoing).')
    duration_minutes = models.IntegerField(default=0, help_text='Break duration in minutes.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['break_start']
        verbose_name = 'Break Record'
        verbose_name_plural = 'Break Records'

    def __str__(self):
        return f"{self.user.username} break - {self.break_start}"

    @property
    def is_active(self):
        return self.break_end is None

    def calculate_duration(self):
        if self.break_start and self.break_end:
            seconds = (self.break_end - self.break_start).total_seconds()
            self.duration_minutes = max(0, int(seconds // 60))
        return self.duration_minutes

    def save(self, *args, **kwargs):
        if self.break_end:
            self.calculate_duration()
        super().save(*args, **kwargs)


class LateArrivalRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('approved',  'Approved'),
        ('rejected',  'Rejected'),
        ('cancelled', 'Cancelled'),
        ('waived',    'Waived'),
    ]

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_late_arrival_requests',
        null=True, blank=True,
        help_text="The admin/tenant who owns this request.",
    )
    # ─────────────────────────────────────────────────────────────────────────

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='late_arrival_requests')
    date = models.DateField()
    expected_arrival_time = models.TimeField(help_text="Expected / actual late arrival time (HH:MM)")
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_late_arrivals')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Late Arrival Request'
        verbose_name_plural = 'Late Arrival Requests'
        unique_together = ['user', 'date']

    def __str__(self):
        return f"{self.user.username} - {self.date} @ {self.expected_arrival_time} ({self.status})"


class LeaveRequest(models.Model):
    LEAVE_TYPE_CHOICES = [
        ('sick', 'Sick Leave'), ('casual', 'Casual Leave'), ('annual', 'Annual Leave'),
        ('maternity', 'Maternity Leave'), ('paternity', 'Paternity Leave'),
        ('unpaid', 'Unpaid Leave'), ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'), ('approved', 'Approved'),
        ('rejected', 'Rejected'), ('cancelled', 'Cancelled'),
        ('waived', 'Waived'),
    ]
    DURATION_TYPE_CHOICES = [
        ('full_day', 'Full Day'),
        ('half_day', 'Half Day'),
    ]

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_leave_requests',
        null=True, blank=True,
        help_text="The admin/tenant who owns this leave request.",
    )
    # ─────────────────────────────────────────────────────────────────────────

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES, default='casual')
    duration_type = models.CharField(
        max_length=10,
        choices=DURATION_TYPE_CHOICES,
        default='full_day',
        help_text="Whether the leave is for a full day or half day.",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_leave_requests')
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
        if self.start_date and self.end_date:
            days = (self.end_date - self.start_date).days + 1
            if self.duration_type == 'half_day':
                return 0.5
            return days
        return 0


class AttendanceSettings(models.Model):
    """Global attendance settings – one record per tenant (admin_owner)."""

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_attendance_settings',
        null=True, blank=True,
        help_text="The admin/tenant who owns these settings.",
    )
    # ─────────────────────────────────────────────────────────────────────────

    office_start_time = models.TimeField(default='09:00:00')
    office_end_time = models.TimeField(default='18:00:00')
    grace_period_minutes = models.IntegerField(default=15)
    minimum_hours_full_day = models.DecimalField(max_digits=4, decimal_places=2, default=8.00)
    minimum_hours_half_day = models.DecimalField(max_digits=4, decimal_places=2, default=4.00)

    office_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        help_text="Office latitude – set this to enable geofence enforcement"
    )
    office_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        help_text="Office longitude – set this to enable geofence enforcement"
    )
    office_radius_meters = models.IntegerField(
        default=100,
        help_text="Allowed check-in/out radius in metres for IN_OFFICE users"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Attendance Settings'
        verbose_name_plural = 'Attendance Settings'

    def __str__(self):
        return f"Attendance Settings - {self.office_start_time} to {self.office_end_time}"


class EarlyDepartureRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('approved',  'Approved'),
        ('rejected',  'Rejected'),
        ('cancelled', 'Cancelled'),
        ('waived',    'Waived'),
    ]

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_early_departure_requests',
        null=True, blank=True,
        help_text="The admin/tenant who owns this request.",
    )
    # ─────────────────────────────────────────────────────────────────────────

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='early_departure_requests',
    )
    date = models.DateField()
    expected_departure_time = models.TimeField(
        help_text="Planned early departure time (HH:MM)"
    )
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_early_departures',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Early Departure Request'
        verbose_name_plural = 'Early Departure Requests'
        unique_together = ['user', 'date']

    def __str__(self):
        return (
            f"{self.user.username} – {self.date} "
            f"@ {self.expected_departure_time} ({self.status})"
        )


class EmployeeFaceData(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='face_data')
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_face_data',
        null=True, blank=True,
    )
    reference_image = models.ImageField(upload_to='face_data/')

    # Pre-computed Facenet512 embedding stored as a JSON array of 512 floats.
    # Populated automatically when a face is registered via register-face.
    # At punch time we do a single cosine-distance calculation instead of
    # running DeepFace.verify() (which reloads the model on every request).
    face_embedding = models.TextField(
        null=True, blank=True,
        help_text="JSON-serialised Facenet512 embedding vector (512 floats).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Face Data for {self.user.username}"
    
# ─────────────────────────────────────────────────────────────────────────────
# ADD THIS CLASS to the bottom of your existing models.py
# ─────────────────────────────────────────────────────────────────────────────

class SalaryAdvanceRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('approved',  'Approved'),
        ('rejected',  'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_salary_advance_requests',
        null=True, blank=True,
        help_text="The admin/tenant who owns this request.",
    )
    # ─────────────────────────────────────────────────────────────────────────

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='salary_advance_requests',
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Requested advance amount in the local currency."
    )
    reason = models.TextField(help_text="Reason for requesting the salary advance.")
    repayment_months = models.PositiveIntegerField(
        default=1,
        help_text="Number of months over which the advance will be repaid."
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_salary_advances',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, null=True)
    approved_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        null=True, blank=True,
        help_text="Amount actually approved (may differ from requested amount)."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Salary Advance Request'
        verbose_name_plural = 'Salary Advance Requests'

    def __str__(self):
        return (
            f"{self.user.username} – ₹{self.amount} "
            f"({self.status})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# WORK FROM HOME REQUEST
# ─────────────────────────────────────────────────────────────────────────────

class WFHRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('approved',  'Approved'),
        ('rejected',  'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_wfh_requests',
        null=True, blank=True,
        help_text="The admin/tenant who owns this request.",
    )
    # ─────────────────────────────────────────────────────────────────────────

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wfh_requests',
    )
    date = models.DateField(help_text="Date for which WFH is requested.")
    reason = models.TextField(help_text="Reason for working from home.")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_wfh_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'WFH Request'
        verbose_name_plural = 'WFH Requests'
        unique_together = ['user', 'date']

    def __str__(self):
        return f"{self.user.username} – WFH {self.date} ({self.status})"
