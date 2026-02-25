# master/models.py
from django.db import models
from employee_management.models import Employee


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

