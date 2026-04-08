from django.db import models
from django.conf import settings
from HR.models import Candidate
import os
from storages.backends.s3boto3 import S3Boto3Storage


class R2EmployeeImageStorage(S3Boto3Storage):
    bucket_name  = os.getenv('CLOUDFLARE_R2_BUCKET', 'taskhrms')
    access_key   = os.getenv('CLOUDFLARE_R2_ACCESS_KEY')
    secret_key   = os.getenv('CLOUDFLARE_R2_SECRET_KEY')
    endpoint_url = os.getenv('CLOUDFLARE_R2_BUCKET_ENDPOINT')
    custom_domain = (
        os.getenv('CLOUDFLARE_R2_PUBLIC_URL', '')
        .replace('https://', '')
        .replace('http://', '')
        or None
    )
    file_overwrite = False
    default_acl    = None


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------
class Department(models.Model):
    name        = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    # ── Tenant isolation ─────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='departments',
        limit_choices_to={'role': 'ADMIN'},
    )

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Employee
# ---------------------------------------------------------------------------
class Employee(models.Model):
    candidate = models.OneToOneField(
        Candidate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    employee_id = models.CharField(max_length=20, unique=True, editable=False)

    # ── Basic Details ─────────────────────────────────────────────────────────
    first_name    = models.CharField(max_length=100)
    last_name     = models.CharField(max_length=100, blank=True, default='')
    email         = models.EmailField()
    phone         = models.CharField(max_length=20, blank=True)
    profile_image = models.ImageField(
        storage=R2EmployeeImageStorage(),
        upload_to='employee_images/',
        null=True,
        blank=True,
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        related_name='employees',
    )

    position        = models.CharField(max_length=100)
    employment_type = models.CharField(max_length=50)
    status          = models.CharField(max_length=50, default='active')

    WORK_LOCATION_CHOICES = [
        ('in_office',     'In Office'),
        ('out_of_office', 'Out of Office'),
    ]
    work_location = models.CharField(
        max_length=20,
        choices=WORK_LOCATION_CHOICES,
        default='in_office',
    )

    date_of_birth   = models.DateField(null=True, blank=True)
    date_of_joining = models.DateField()

    address                         = models.TextField(blank=True)
    emergency_contact               = models.CharField(max_length=20, blank=True)
    emergency_contact_name          = models.CharField(max_length=100, blank=True)
    emergency_contact_relationship  = models.CharField(max_length=50, blank=True)

    # ── Salary Details ────────────────────────────────────────────────────────
    salary          = models.DecimalField(max_digits=10, decimal_places=2)
    salary_currency = models.CharField(max_length=10, default='USD', blank=True)

    # ── Bank Details ──────────────────────────────────────────────────────────
    bank_name           = models.CharField(max_length=100, blank=True)
    account_number      = models.CharField(max_length=50, blank=True)
    account_holder_name = models.CharField(max_length=100, blank=True)
    ifsc_code           = models.CharField(max_length=20, blank=True)
    branch_name         = models.CharField(max_length=100, blank=True)
    account_type        = models.CharField(
        max_length=20,
        choices=[
            ('savings', 'Savings'),
            ('current', 'Current'),
        ],
        blank=True,
    )

    # ── PF (Provident Fund) Details ───────────────────────────────────────────
    pf_enabled           = models.BooleanField(default=False)
    pf_number            = models.CharField(max_length=50, blank=True)
    pf_contribution_type = models.CharField(
        max_length=20,
        choices=[
            ('percentage', 'Percentage of Basic'),
            ('fixed',      'Fixed Amount'),
        ],
        default='percentage',
        blank=True,
    )
    employee_pf_contribution = models.DecimalField(
        max_digits=6, decimal_places=2, default=12.00,
        help_text='Employee PF contribution percentage or fixed amount',
    )
    employer_pf_contribution = models.DecimalField(
        max_digits=6, decimal_places=2, default=12.00,
        help_text='Employer PF contribution percentage or fixed amount',
    )

    # ── Overtime Settings ─────────────────────────────────────────────────────
    overtime_enabled   = models.BooleanField(default=False)
    overtime_rate_type = models.CharField(
        max_length=20,
        choices=[
            ('multiplier', 'Multiplier (e.g. 1.5x)'),
            ('fixed',      'Fixed Amount per Hour'),
        ],
        default='multiplier',
        blank=True,
    )
    overtime_rate = models.DecimalField(
        max_digits=6, decimal_places=2, default=1.50,
        help_text='Overtime multiplier (e.g. 1.5) or fixed hourly rate',
    )
    max_overtime_hours_per_month = models.DecimalField(
        max_digits=5, decimal_places=1, default=40.0,
        help_text='Maximum allowed overtime hours per month',
    )

    # ── Shift Details ─────────────────────────────────────────────────────────
    SHIFT_CHOICES = [
        ('general',   'General  (9 AM – 6 PM)'),
        ('morning',   'Morning  (6 AM – 2 PM)'),
        ('afternoon', 'Afternoon (2 PM – 10 PM)'),
        ('night',     'Night    (10 PM – 6 AM)'),
        ('custom',    'Custom'),
    ]
    shift_type       = models.CharField(max_length=20, choices=SHIFT_CHOICES, default='general', blank=True)
    shift_start_time = models.TimeField(null=True, blank=True)
    shift_end_time   = models.TimeField(null=True, blank=True)
    weekly_off_days  = models.CharField(
        max_length=100, blank=True, default='Saturday,Sunday',
        help_text="Comma-separated off days, e.g. 'Saturday,Sunday'",
    )

    # ── Custom Fields ─────────────────────────────────────────────────────────
    custom_fields = models.JSONField(default=dict, blank=True)

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='employees',
        limit_choices_to={'role': 'ADMIN'},
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.employee_id:
            last_emp = Employee.objects.order_by('-id').first()
            if last_emp:
                last_number = int(last_emp.employee_id.replace('EMP', ''))
                self.employee_id = f'EMP{last_number + 1:04d}'
            else:
                self.employee_id = 'EMP0001'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.employee_id} - {self.first_name} {self.last_name}'


# ---------------------------------------------------------------------------
# Custom Field Definition
# ---------------------------------------------------------------------------
class CustomFieldDefinition(models.Model):
    """
    Defines custom fields that can be added to employees.
    Each ADMIN tenant can define their own set of fields.
    """
    FIELD_TYPES = [
        ('text',     'Text'),
        ('number',   'Number'),
        ('date',     'Date'),
        ('email',    'Email'),
        ('phone',    'Phone'),
        ('textarea', 'Text Area'),
        ('select',   'Dropdown'),
        ('checkbox', 'Checkbox'),
    ]

    field_name  = models.CharField(max_length=100, help_text='Internal field name (no spaces)')
    field_label = models.CharField(max_length=200, help_text='Display label for the field')
    field_type  = models.CharField(max_length=20, choices=FIELD_TYPES, default='text')

    # For select/dropdown fields — comma-separated options
    field_options = models.TextField(
        blank=True,
        help_text="For dropdown fields: comma-separated options (e.g., 'Option1,Option2,Option3')",
    )

    is_required   = models.BooleanField(default=False)
    default_value = models.CharField(max_length=500, blank=True)
    help_text     = models.CharField(max_length=500, blank=True)
    display_order = models.IntegerField(default=0)
    is_active     = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='custom_field_definitions',
        limit_choices_to={'role': 'ADMIN'},
    )

    class Meta:
        ordering = ['display_order', 'field_label']
        # field_name must be unique per tenant, not globally
        unique_together = [('field_name', 'admin_owner')]

    def __str__(self):
        return f'{self.field_label} ({self.field_type})'

    def get_options_list(self):
        """Return field options as a list."""
        if self.field_options:
            return [opt.strip() for opt in self.field_options.split(',')]
        return []


# ---------------------------------------------------------------------------
# Employee Asset
# ---------------------------------------------------------------------------
class EmployeeAsset(models.Model):
    CONDITION_CHOICES = [
        ('new',     'New'),
        ('good',    'Good'),
        ('fair',    'Fair'),
        ('damaged', 'Damaged'),
    ]
    STATUS_CHOICES = [
        ('assigned', 'Assigned'),
        ('returned', 'Returned'),
        ('lost',     'Lost'),
    ]

    employee      = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='assets')
    asset_name    = models.CharField(max_length=200)
    asset_tag     = models.CharField(max_length=100, blank=True)
    category      = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=100, blank=True)
    condition     = models.CharField(max_length=20, choices=CONDITION_CHOICES, default='good')
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default='assigned')
    assigned_date = models.DateField()
    return_date   = models.DateField(null=True, blank=True)
    notes         = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.asset_name} → {self.employee}'


# ---------------------------------------------------------------------------
# Signals — auto-delete images from Cloudflare R2
# ---------------------------------------------------------------------------
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver


@receiver(post_delete, sender=Employee)
def auto_delete_image_on_delete(sender, instance, **kwargs):
    """Delete image from R2 when the Employee record is deleted."""
    if instance.profile_image:
        instance.profile_image.delete(save=False)


@receiver(pre_save, sender=Employee)
def auto_delete_image_on_change(sender, instance, **kwargs):
    """Delete old image from R2 when the Employee is updated with a new image."""
    if not instance.pk:
        return

    try:
        old_file = Employee.objects.get(pk=instance.pk).profile_image
    except Employee.DoesNotExist:
        return

    new_file = instance.profile_image
    if old_file and old_file != new_file:
        old_file.delete(save=False)