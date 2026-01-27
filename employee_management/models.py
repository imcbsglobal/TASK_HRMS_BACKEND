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

    employee_id = models.CharField(max_length=50, unique=True)
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
    date_of_joining = models.DateField()
    salary = models.DecimalField(max_digits=10, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.employee_id
