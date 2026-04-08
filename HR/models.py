from django.db import models
import os
from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage

class R2HRStorage(S3Boto3Storage):
    bucket_name = os.getenv('CLOUDFLARE_R2_BUCKET', 'taskhrms')
    access_key = os.getenv('CLOUDFLARE_R2_ACCESS_KEY')
    secret_key = os.getenv('CLOUDFLARE_R2_SECRET_KEY')
    endpoint_url = os.getenv('CLOUDFLARE_R2_BUCKET_ENDPOINT')
    custom_domain = os.getenv('CLOUDFLARE_R2_PUBLIC_URL', '').replace('https://', '').replace('http://', '') if os.getenv('CLOUDFLARE_R2_PUBLIC_URL') else None
    file_overwrite = False
    default_acl = None


# ─────────────────────────────────────────────────────────────
#  Pipeline Stage (company-customisable)
# ─────────────────────────────────────────────────────────────

class PipelineStage(models.Model):
    """
    Custom interview stages defined by each company.
    The three built-in stages (uploaded, selected, rejected) are handled
    in code and are never stored here.
    """
    key = models.SlugField(max_length=60, unique=True)   # e.g. "hr_round"
    title = models.CharField(max_length=100)              # e.g. "HR Round"
    order = models.PositiveSmallIntegerField(default=0)   # display order

    # ── Tenant isolation ─────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pipeline_stages',
        limit_choices_to={'role': 'ADMIN'},
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


# ─────────────────────────────────────────────────────────────
#  Candidate
# ─────────────────────────────────────────────────────────────

class Candidate(models.Model):
    # Fixed status choices: uploaded / selected / rejected are always present.
    # Custom stages from PipelineStage use their `key` as the status value.
    FIXED_STATUS_CHOICES = [
        ("uploaded", "CV Uploaded"),
        ("selected", "Selected"),
        ("rejected", "Rejected"),
    ]

    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    location = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=200, blank=True)
    experience = models.CharField(max_length=100, blank=True)
    education = models.CharField(max_length=200, blank=True)
    skills = models.JSONField(default=list)

    cv = models.FileField(storage=R2HRStorage(), upload_to="cvs/")
    # status stores either a fixed key or a PipelineStage.key
    status = models.CharField(max_length=60, default="uploaded")

    # ── Tenant isolation ──────────────────────────────────────────────────────
    admin_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='candidates',
        limit_choices_to={'role': 'ADMIN'},
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class CandidateRating(models.Model):
    candidate = models.OneToOneField(
        Candidate,
        on_delete=models.CASCADE,
        related_name="rating"
    )

    appearance = models.PositiveSmallIntegerField(default=0)
    knowledge = models.PositiveSmallIntegerField(default=0)
    confidence = models.PositiveSmallIntegerField(default=0)
    attitude = models.PositiveSmallIntegerField(default=0)
    communication = models.PositiveSmallIntegerField(default=0)

    languages = models.JSONField(default=list)
    expected_salary = models.CharField(max_length=100, blank=True)
    experience = models.CharField(max_length=100, blank=True)
    remarks = models.TextField(blank=True)

    def __str__(self):
        return f"Rating - {self.candidate.name}"


class OfferLetter(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("sent", "Sent"),
        ("accepted", "Accepted"),
        ("declined", "Declined"),
    ]

    candidate = models.OneToOneField(
        Candidate,
        on_delete=models.CASCADE,
        related_name="offer_letter"
    )
    position = models.CharField(max_length=200, blank=True, default="")
    department = models.CharField(max_length=200, blank=True)
    salary = models.CharField(max_length=100, blank=True, default="")
    joining_date = models.DateField(null=True, blank=True)
    offer_date = models.DateField(auto_now_add=True)
    work_location = models.CharField(max_length=200, blank=True)
    work_hours = models.CharField(max_length=100, blank=True, default="9:00 AM - 6:00 PM")
    company_name = models.CharField(max_length=200, blank=True, default="Our Company")
    hr_name = models.CharField(max_length=200, blank=True)
    hr_designation = models.CharField(max_length=200, blank=True, default="HR Manager")
    additional_benefits = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Offer Letter - {self.candidate.name}"

from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

@receiver(post_delete, sender=Candidate)
def auto_delete_cv_on_delete(sender, instance, **kwargs):
    if instance.cv:
        instance.cv.delete(save=False)

@receiver(pre_save, sender=Candidate)
def auto_delete_cv_on_change(sender, instance, **kwargs):
    if not instance.pk:
        return False
    try:
        old_candidate = Candidate.objects.get(pk=instance.pk)
        old_file = old_candidate.cv
    except Candidate.DoesNotExist:
        return False
    
    new_file = instance.cv
    if old_file and old_file != new_file:
        old_file.delete(save=False)