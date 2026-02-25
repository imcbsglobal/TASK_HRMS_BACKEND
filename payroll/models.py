from django.db import models
from django.conf import settings
from employee_management.models import Employee
from master.models import Allowance, Deduction
from django.utils import timezone
from decimal import Decimal


class Payroll(models.Model):
    """
    Model to store monthly payroll records for employees
    """
    
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
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('processed', 'Processed'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ]
    
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='payrolls',
        help_text="Employee for this payroll record"
    )
    
    year = models.IntegerField(
        help_text="Payroll year"
    )
    
    month = models.IntegerField(
        choices=MONTH_CHOICES,
        help_text="Payroll month"
    )
    
    # Salary components
    basic_salary = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Basic salary from employee record"
    )
    
    total_allowances = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Sum of all allowances for this month"
    )
    
    total_deductions = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Sum of all deductions for this month"
    )
    
    net_salary = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Net salary after allowances and deductions"
    )
    
    # Days tracking
    total_days_in_month = models.IntegerField(
        default=30,
        help_text="Total days in the month"
    )
    
    total_working_days = models.IntegerField(
        default=0,
        help_text="Total working days (based on attendance)"
    )
    
    # Status and notes
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        help_text="Payroll status"
    )
    
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Additional notes or remarks"
    )
    
    # Processing details
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='processed_payrolls',
        help_text="User who processed this payroll"
    )
    
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the payroll was processed"
    )
    
    # Payment details
    payment_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date when payment was made"
    )
    
    payment_reference = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Payment transaction reference"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'payroll'
        ordering = ['-year', '-month', 'employee__first_name']
        verbose_name = 'Payroll'
        verbose_name_plural = 'Payrolls'
        unique_together = ['employee', 'year', 'month']
        indexes = [
            models.Index(fields=['employee', 'year', 'month']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        month_name = dict(self.MONTH_CHOICES).get(self.month, '')
        return f"{self.employee.first_name} {self.employee.last_name} - {month_name} {self.year}"
    
    @property
    def employee_name(self):
        return f"{self.employee.first_name} {self.employee.last_name}"
    
    @property
    def month_display(self):
        return dict(self.MONTH_CHOICES).get(self.month, '')
    
    def calculate_net_salary(self):
        """Calculate net salary based on basic salary, allowances, and deductions"""
        self.net_salary = Decimal(str(self.basic_salary)) + Decimal(str(self.total_allowances)) - Decimal(str(self.total_deductions))
        return self.net_salary
    
    def save(self, *args, **kwargs):
        # Auto-calculate net salary before saving
        self.calculate_net_salary()
        super().save(*args, **kwargs)
