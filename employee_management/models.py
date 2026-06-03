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

    # ── Section (optional) ────────────────────────────────────────────────────
    section = models.ForeignKey(
        'master.Section',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
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

    GENDER_CHOICES = [
        ('male',   'Male'),
        ('female', 'Female'),
        ('other',  'Other'),
    ]
    gender = models.CharField(
        max_length=10,
        choices=GENDER_CHOICES,
        blank=True,
        default='',
    )

    # ── Probation Details ─────────────────────────────────────────────────────
    probation_period_months = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text='Duration of the probation period in months (e.g. 3 or 6). '
                  'Leave blank if no probation applies.',
    )
    probation_end_date = models.DateField(
        null=True,
        blank=True,
        help_text='Auto-calculated from date_of_joining + probation_period_months when saved.',
    )

    @property
    def probation_status(self):
        """
        Returns one of:
          'no_probation'          – probation_period_months is not set
          'on_probation'          – today is before or on probation_end_date
          'probation_completed'   – today is after probation_end_date
        """
        if not self.probation_period_months or not self.probation_end_date:
            return 'no_probation'
        from django.utils.timezone import now
        today = now().date()
        if today <= self.probation_end_date:
            return 'on_probation'
        return 'probation_completed'

    address                         = models.TextField(blank=True)
    emergency_contact               = models.CharField(max_length=20, blank=True)
    emergency_contact_name          = models.CharField(max_length=100, blank=True)
    emergency_contact_relationship  = models.CharField(max_length=50, blank=True)

    # ── Salary Details ────────────────────────────────────────────────────────
    salary          = models.DecimalField(max_digits=10, decimal_places=2)
    salary_currency = models.CharField(max_length=10, default='USD', blank=True)

    # ── Increment Details ─────────────────────────────────────────────────────
    INCREMENT_CYCLE_CHOICES = [
        (1,  'Every Month'),
        (2,  'Every 2 Months'),
        (3,  'Every Quarter (3 Months)'),
        (4,  'Every 4 Months'),
        (6,  'Every 6 Months (Half-Yearly)'),
        (12, 'Every Year (Annual)'),
        (18, 'Every 18 Months'),
        (24, 'Every 2 Years'),
        (0,  'Custom / Manual'),
    ]
    last_increment_date = models.DateField(
        null=True,
        blank=True,
        help_text='Date when the last salary increment was applied',
    )
    increment_cycle_months = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=INCREMENT_CYCLE_CHOICES,
        help_text='How often this employee receives a salary increment (in months). '
                  '0 = manual/custom, null = not configured.',
    )
    next_increment_date = models.DateField(
        null=True,
        blank=True,
        help_text='Scheduled date for the next salary increment. '
                  'Auto-calculated from last_increment_date + increment_cycle_months when saved.',
    )

    def _compute_next_increment_date(self):
        """
        Auto-compute next_increment_date from last_increment_date + increment_cycle_months.
        Only runs when cycle > 0 and last_increment_date is set.
        Does NOT overwrite a manually supplied next_increment_date when cycle == 0.
        """
        from dateutil.relativedelta import relativedelta
        if (
            self.last_increment_date
            and self.increment_cycle_months
            and self.increment_cycle_months > 0
        ):
            return self.last_increment_date + relativedelta(months=self.increment_cycle_months)
        return self.next_increment_date  # keep whatever was set manually

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

    # ── Duty Time ─────────────────────────────────────────────────────────────
    duty_start_time = models.TimeField(null=True, blank=True, help_text='Employee duty start time')
    duty_end_time   = models.TimeField(null=True, blank=True, help_text='Employee duty end time')

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
        # Auto-compute next_increment_date when a cycle is configured
        self.next_increment_date = self._compute_next_increment_date()
        # Auto-compute probation_end_date from date_of_joining + probation_period_months
        if self.date_of_joining and self.probation_period_months:
            from dateutil.relativedelta import relativedelta
            self.probation_end_date = self.date_of_joining + relativedelta(months=self.probation_period_months)
        elif not self.probation_period_months:
            self.probation_end_date = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.employee_id} - {self.first_name} {self.last_name}'


# ---------------------------------------------------------------------------
# Salary Increment History
# ---------------------------------------------------------------------------
class SalaryIncrementHistory(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='salary_increment_logs',
    )
    increment_date = models.DateField(help_text='Date the salary increment was applied')
    old_salary = models.DecimalField(max_digits=10, decimal_places=2)
    new_salary = models.DecimalField(max_digits=10, decimal_places=2)
    increment_amount = models.DecimalField(max_digits=10, decimal_places=2)
    increment_percentage = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        help_text='Calculated as (increment_amount / old_salary) * 100',
    )
    increment_cycle_months = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=Employee.INCREMENT_CYCLE_CHOICES,
    )
    next_increment_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='salary_increment_logs',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-increment_date', '-created_at']

    def __str__(self):
        return f'{self.employee} salary increment on {self.increment_date}'


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
# Employee Document
# ---------------------------------------------------------------------------
class EmployeeDocument(models.Model):
    """
    Stores multiple documents attached to an employee (e.g. ID proof, offer letter,
    certificates, etc.). Files are stored on the same Cloudflare R2 bucket under
    the employee_documents/ prefix.
    """

    DOCUMENT_TYPE_CHOICES = [
        ('id_proof',          'ID Proof'),
        ('address_proof',     'Address Proof'),
        ('educational',       'Educational Certificate'),
        ('experience',        'Experience Certificate'),
        ('offer_letter',      'Offer Letter'),
        ('joining_letter',    'Joining Letter'),
        ('contract',          'Contract / Agreement'),
        ('nda',               'NDA'),
        ('insurance',         'Insurance Document'),
        ('medical',           'Medical Certificate'),
        ('payslip',           'Payslip'),
        ('bank_document',     'Bank Document'),
        ('visa',              'Visa / Work Permit'),
        ('passport',          'Passport Copy'),
        ('other',             'Other'),
    ]

    employee      = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    document_type = models.CharField(
        max_length=30,
        choices=DOCUMENT_TYPE_CHOICES,
        default='other',
    )
    title         = models.CharField(max_length=255, help_text='Descriptive name for the document')
    file          = models.FileField(
        storage=R2EmployeeImageStorage(),
        upload_to='employee_documents/',
    )
    notes         = models.TextField(blank=True)
    uploaded_at   = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.title} ({self.get_document_type_display()}) — {self.employee}'

    @property
    def file_name(self):
        """Return just the original filename without the upload path."""
        import os
        return os.path.basename(self.file.name) if self.file else ''

    @property
    def file_size(self):
        """Return file size in bytes; None if unavailable."""
        try:
            return self.file.size
        except Exception:
            return None


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


@receiver(post_delete, sender=EmployeeDocument)
def auto_delete_document_on_delete(sender, instance, **kwargs):
    """Delete document file from R2 when the EmployeeDocument record is deleted."""
    if instance.file:
        instance.file.delete(save=False)