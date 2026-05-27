from django.db import models
from django.conf import settings
from employee_management.models import Employee
from django.utils import timezone


class LeaveType(models.Model):
    """
    Model to store different types of leaves and holidays
    """
    
    CATEGORY_CHOICES = [
        ('sick_leave', 'Sick Leave'),
        ('casual_leave', 'Casual Leave'),
        ('special_leave', 'Special Leave'),
        ('mandatory_holiday', 'Mandatory Holiday'),
    ]
    
    PAYMENT_STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('unpaid', 'Unpaid'),
    ]
    
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Name of the leave/holiday (e.g., NEW YEARS DAY, ANNUAL LEAVE)"
    )
    date = models.DateField(
        null=True,
        blank=True,
        help_text="Date for specific holidays (optional for ongoing allowances)"
    )
    category = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES,
        default='casual_leave',
        help_text="Category of the leave/holiday"
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default='unpaid',
        help_text="Payment status for this leave type"
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

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='leave_types',
        limit_choices_to={'role': 'ADMIN'},
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

class Allowance(models.Model):
    """Model to store employee allowances"""
    
    MONTH_CHOICES = [
        (1, 'January'),
        (2, 'February'),
        (3, 'March'),
        (4, 'April'),
        (5, 'May'),
        (6, 'June'),
        (7, 'July'),
        (8, 'August'),
        (9, 'September'),
        (10, 'October'),
        (11, 'November'),
        (12, 'December'),
    ]
    
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='allowances',
        help_text="Employee receiving the allowance"
    )
    allowance_name = models.CharField(
        max_length=100,
        help_text="Name/Type of the allowance (e.g., Transport, Housing, Medical)"
    )
    year = models.IntegerField(
        help_text="Year for the allowance"
    )
    month = models.IntegerField(
        choices=MONTH_CHOICES,
        help_text="Month for the allowance"
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount of the allowance"
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Additional notes or description"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this allowance is currently active"
    )

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='allowances',
        limit_choices_to={'role': 'ADMIN'},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'master_allowances'
        ordering = ['-year', '-month', 'employee__first_name']
        verbose_name = 'Allowance'
        verbose_name_plural = 'Allowances'
        unique_together = ['employee', 'allowance_name', 'year', 'month']

    def __str__(self):
        return f"{self.employee.first_name} {self.employee.last_name} - {self.allowance_name} ({self.year}/{self.month})"
    
    @property
    def employee_name(self):
        return f"{self.employee.first_name} {self.employee.last_name}"
    
    @property
    def month_display(self):
        return dict(self.MONTH_CHOICES).get(self.month, '')


class Holiday(models.Model):
    """Model to store company/public holidays"""

    TYPE_CHOICES = [
        ('national',  'National'),
        ('regional',  'Regional'),
        ('company',   'Company'),
        ('optional',  'Optional'),
    ]

    name = models.CharField(
        max_length=150,
        help_text="Name of the holiday (e.g., Christmas Day)"
    )
    date = models.DateField(
        help_text="Date of the holiday"
    )
    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default='national',
        help_text="Type of holiday"
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional description or notes"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this holiday is currently visible"
    )

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='holidays',
        limit_choices_to={'role': 'ADMIN'},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'master_holidays'
        ordering = ['date']
        verbose_name = 'Holiday'
        verbose_name_plural = 'Holidays'

    def __str__(self):
        return f"{self.name} ({self.date})"

    @property
    def type_display(self):
        return dict(self.TYPE_CHOICES).get(self.type, self.type)


class Deduction(models.Model):
    """Model to store employee deductions"""
    
    MONTH_CHOICES = [
        (1, 'January'),
        (2, 'February'),
        (3, 'March'),
        (4, 'April'),
        (5, 'May'),
        (6, 'June'),
        (7, 'July'),
        (8, 'August'),
        (9, 'September'),
        (10, 'October'),
        (11, 'November'),
        (12, 'December'),
    ]
    
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='deductions',
        help_text="Employee with the deduction"
    )
    deduction_name = models.CharField(
        max_length=100,
        help_text="Name/Type of the deduction (e.g., Tax, Insurance, Loan)"
    )
    year = models.IntegerField(
        help_text="Year for the deduction"
    )
    month = models.IntegerField(
        choices=MONTH_CHOICES,
        help_text="Month for the deduction"
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount of the deduction"
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Additional notes or description"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this deduction is currently active"
    )

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='deductions',
        limit_choices_to={'role': 'ADMIN'},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'master_deductions'
        ordering = ['-year', '-month', 'employee__first_name']
        verbose_name = 'Deduction'
        verbose_name_plural = 'Deductions'
        unique_together = ['employee', 'deduction_name', 'year', 'month']

    def __str__(self):
        return f"{self.employee.first_name} {self.employee.last_name} - {self.deduction_name} ({self.year}/{self.month})"
    
    @property
    def employee_name(self):
        return f"{self.employee.first_name} {self.employee.last_name}"
    
    @property
    def month_display(self):
        return dict(self.MONTH_CHOICES).get(self.month, '')

class JobTitle(models.Model):
    """Model to store job titles for employees"""

    name = models.CharField(
        max_length=150,
        help_text="Job title name (e.g., Software Engineer, HR Manager)"
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional description of the job title"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this job title is currently active"
    )

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='job_titles',
        limit_choices_to={'role': 'ADMIN'},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'master_job_titles'
        ordering = ['name']
        verbose_name = 'Job Title'
        verbose_name_plural = 'Job Titles'
        unique_together = ['name', 'admin_owner']

    def __str__(self):
        return self.name


class Announcement(models.Model):
    """Model to store HR announcements shown on the dashboard"""

    TAG_CHOICES = [
        ('general',     'General'),
        ('performance', 'Performance'),
        ('benefits',    'Benefits'),
        ('training',    'Training'),
        ('holiday',     'Holiday'),
        ('policy',      'Policy'),
        ('event',       'Event'),
    ]

    title = models.CharField(
        max_length=255,
        help_text="Announcement headline"
    )
    body = models.TextField(
        blank=True,
        default="",
        help_text="Full announcement details"
    )
    date = models.DateField(
        help_text="Announcement date"
    )
    tag = models.CharField(
        max_length=20,
        choices=TAG_CHOICES,
        default='general',
        help_text="Category tag"
    )
    icon = models.CharField(
        max_length=10,
        default='📢',
        help_text="Emoji icon for this announcement"
    )
    is_pinned = models.BooleanField(
        default=False,
        help_text="Pin to top of the list"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this announcement is visible"
    )

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='announcements',
        limit_choices_to={'role': 'ADMIN'},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'master_announcements'
        ordering = ['-is_pinned', '-date', '-created_at']
        verbose_name = 'Announcement'
        verbose_name_plural = 'Announcements'

    def __str__(self):
        return self.title
    
# ─────────────────────────────────────────────────────────────────────────────
# ADD THIS CLASS to master/models.py
# It stores the full payroll-policy JSON blob per tenant (admin_owner).
# One row per admin — use get_or_create on reads, and save() on writes.
# ─────────────────────────────────────────────────────────────────────────────

class PayrollPolicy(models.Model):
    """
    Stores the company-level payroll policy as a single JSON document.
    One record per admin tenant. Created automatically on first save.
    """

    policy_data = models.JSONField(
        default=dict,
        help_text="Full payroll-policy configuration (attendance, overtime, leave)"
    )

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='payroll_policy',
        limit_choices_to={'role': 'ADMIN'},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'master_payroll_policy'
        verbose_name = 'Payroll Policy'
        verbose_name_plural = 'Payroll Policies'

    def __str__(self):
        owner = self.admin_owner.email if self.admin_owner else "Global"
        return f"Payroll Policy — {owner}"