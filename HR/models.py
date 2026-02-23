from django.db import models


class Candidate(models.Model):
    STATUS_CHOICES = [
        ("uploaded", "CV Uploaded"),
        ("interview", "Interview"),
        ("interview1", "Interview 1"),
        ("interview2", "Interview 2"),
        ("pending", "Decision Pending"),
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

    cv = models.FileField(upload_to="cvs/")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="uploaded")

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
    # Allow blank/null so partial drafts can be saved without all fields
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