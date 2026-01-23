from django.db import models

# Create your models here.
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
