from django.db import models
from HR.models import Candidate


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Employee(models.Model):
    candidate = models.OneToOneField(
        Candidate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    
    employee_id = models.CharField(
        max_length=20,
        unique=True,
        editable=False
    )

    # Basic Details
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)

    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        related_name="employees"
    )

    position = models.CharField(max_length=100)
    employment_type = models.CharField(max_length=50)
    status = models.CharField(max_length=50, default="active")

    date_of_birth = models.DateField(null=True, blank=True)
    date_of_joining = models.DateField()

    address = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=20, blank=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_relationship = models.CharField(max_length=50, blank=True)

    # Salary Details
    salary = models.DecimalField(max_digits=10, decimal_places=2)
    salary_currency = models.CharField(max_length=10, default="USD", blank=True)
    payment_frequency = models.CharField(
        max_length=20, 
        choices=[
            ('monthly', 'Monthly'),
            ('biweekly', 'Bi-weekly'),
            ('weekly', 'Weekly')
        ],
        default='monthly',
        blank=True
    )
    bonus_eligible = models.BooleanField(default=False)
    bonus_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    allowances = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    deductions = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Bank Details
    bank_name = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=50, blank=True)
    account_holder_name = models.CharField(max_length=100, blank=True)
    ifsc_code = models.CharField(max_length=20, blank=True)
    branch_name = models.CharField(max_length=100, blank=True)
    account_type = models.CharField(
        max_length=20,
        choices=[
            ('savings', 'Savings'),
            ('current', 'Current')
        ],
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

  
    def save(self, *args, **kwargs):
        if not self.employee_id:
            last_emp = Employee.objects.order_by("-id").first()
            if last_emp:
                last_number = int(last_emp.employee_id.replace("EMP", ""))
                self.employee_id = f"EMP{last_number + 1:04d}"
            else:
                self.employee_id = "EMP0001"

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee_id} - {self.first_name} {self.last_name}"