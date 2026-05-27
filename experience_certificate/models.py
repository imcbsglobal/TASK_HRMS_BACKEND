from django.conf import settings
from django.db import models
from django.utils import timezone

from employee_management.models import Employee


class ExperienceCertificate(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('revoked', 'Revoked'),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='experience_certificates',
    )
    certificate_number = models.CharField(max_length=30, unique=True, editable=False)

    employee_name = models.CharField(max_length=200)
    employee_code = models.CharField(max_length=30, blank=True)
    designation = models.CharField(max_length=150)
    department = models.CharField(max_length=150, blank=True)
    employment_type = models.CharField(max_length=80, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    issue_date = models.DateField(default=timezone.localdate)

    conduct = models.CharField(max_length=200, blank=True, default='good')
    responsibilities = models.TextField(blank=True)
    remarks = models.TextField(blank=True)
    signatory_name = models.CharField(max_length=150, blank=True)
    signatory_designation = models.CharField(max_length=150, blank=True, default='HR Manager')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='experience_certificates',
        limit_choices_to={'role': 'ADMIN'},
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='issued_experience_certificates',
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-created_at']

    def save(self, *args, **kwargs):
        if not self.certificate_number:
            year = timezone.localdate().year
            prefix = f'EXPC-{year}-'
            last = (
                ExperienceCertificate.objects
                .filter(certificate_number__startswith=prefix)
                .order_by('-certificate_number')
                .first()
            )
            next_number = 1
            if last:
                try:
                    next_number = int(last.certificate_number.rsplit('-', 1)[1]) + 1
                except (IndexError, ValueError):
                    next_number = 1
            self.certificate_number = f'{prefix}{next_number:04d}'

        if self.employee_id:
            self.employee_name = self.employee_name or (
                f'{self.employee.first_name} {self.employee.last_name}'.strip()
            )
            self.employee_code = self.employee_code or self.employee.employee_id
            self.designation = self.designation or self.employee.position
            self.department = self.department or (
                self.employee.department.name if self.employee.department else ''
            )
            self.employment_type = self.employment_type or self.employee.employment_type
            self.start_date = self.start_date or self.employee.date_of_joining

        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.certificate_number} - {self.employee_name}'

