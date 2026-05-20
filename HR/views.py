from django.shortcuts import render
from django.http import HttpResponse
from django.core.mail import EmailMessage
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from .models import Candidate, CandidateRating, OfferLetter, PipelineStage
from login.models import CompanySettings
from .serializers import (
    CandidateSerializer, CandidateRatingSerializer,
    OfferLetterSerializer, PipelineStageSerializer,
)
from .utils import extract_text, extract_fields
from .offer_pdf import generate_offer_letter_pdf

# ─────────────────────────────────────────────────────────────
#  Tenant helpers
# ─────────────────────────────────────────────────────────────

def _get_admin_owner(user):
    if user.role == 'ADMIN':
        return user
    if user.role == 'USER':
        return user.admin_owner
    return None

def _pipeline_stage_qs(user):
    if user.role == 'SUPER_ADMIN':
        return PipelineStage.objects.all()
    admin = _get_admin_owner(user)
    if admin is None:
        return PipelineStage.objects.none()
    return PipelineStage.objects.filter(admin_owner=admin)

def _candidate_qs(user):
    if user.role == 'SUPER_ADMIN':
        return Candidate.objects.all()
    admin = _get_admin_owner(user)
    if admin is None:
        return Candidate.objects.none()
    return Candidate.objects.filter(admin_owner=admin)

def _company_settings_dict(user):
    admin = _get_admin_owner(user)
    if admin is None:
        return {}

    settings_obj = CompanySettings.objects.filter(owner=admin).first()
    if not settings_obj:
        return {
            "name": admin.company_name or "Company",
            "tagline": "",
            "email": "",
            "phone": "",
            "website": "",
            "address": "",
            "logo": "",
            "primaryColor": "#6d3ef6",
            "currency": "USD",
        }

    return {
        "name": settings_obj.name,
        "tagline": settings_obj.tagline,
        "email": settings_obj.email,
        "phone": settings_obj.phone,
        "website": settings_obj.website,
        "address": settings_obj.address,
        "logo": settings_obj.logo,
        "primaryColor": settings_obj.primaryColor,
        "currency": settings_obj.currency,
    }

# ─────────────────────────────────────────────────────────────
#  Fixed (built-in) stage keys — these cannot be deleted
# ─────────────────────────────────────────────────────────────
FIXED_STAGES = {"uploaded", "shortlisted", "cv_rejected", "start_interview", "selected", "rejected"}


# ─────────────────────────────────────────────────────────────
#  Pipeline Stage Views
# ─────────────────────────────────────────────────────────────

class PipelineStageListView(APIView):
    """
    GET  /pipeline-stages/  → returns all custom stages ordered by `order`
    POST /pipeline-stages/  → create a new custom stage
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        stages = _pipeline_stage_qs(request.user)
        return Response(PipelineStageSerializer(stages, many=True).data)

    def post(self, request):
        key = request.data.get("key", "")
        if key in FIXED_STAGES:
            return Response(
                {"error": f"'{key}' is a reserved stage and cannot be customised."},
                status=400,
            )
        serializer = PipelineStageSerializer(data=request.data)
        if serializer.is_valid():
            admin = _get_admin_owner(request.user)
            serializer.save(admin_owner=admin)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class PipelineStageDetailView(APIView):
    """
    PATCH  /pipeline-stages/<pk>/  → rename or reorder a custom stage
    DELETE /pipeline-stages/<pk>/  → remove a custom stage
    """
    permission_classes = [permissions.IsAuthenticated]

    def _get_stage(self, request, pk):
        try:
            return _pipeline_stage_qs(request.user).get(pk=pk)
        except PipelineStage.DoesNotExist:
            return None

    def patch(self, request, pk):
        stage = self._get_stage(request, pk)
        if not stage:
            return Response({"error": "Stage not found"}, status=404)

        serializer = PipelineStageSerializer(stage, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        stage = self._get_stage(request, pk)
        if not stage:
            return Response({"error": "Stage not found"}, status=404)

        _candidate_qs(request.user).filter(status=stage.key).update(status="shortlisted")
        stage.delete()
        return Response({"success": True})


# ─────────────────────────────────────────────────────────────
#  Candidate Views
# ─────────────────────────────────────────────────────────────

class CandidateUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        file = request.FILES.get("cv")
        if not file:
            return Response({"error": "CV required"}, status=400)

        text = extract_text(file)
        extracted = extract_fields(text)

        data = {
            "name": extracted.get("name") or file.name.split(".")[0],
            "email": extracted.get("email", ""),
            "phone": extracted.get("phone", ""),
            "location": extracted.get("location", ""),
            "role": extracted.get("role", ""),
            "experience": extracted.get("experience", ""),
            "education": extracted.get("education", ""),
            "skills": extracted.get("skills", []),
            "cv": file,
        }

        serializer = CandidateSerializer(data=data)
        if serializer.is_valid():
            admin = _get_admin_owner(request.user)
            serializer.save(admin_owner=admin, status="shortlisted")
            return Response(serializer.data)
        return Response(serializer.errors, status=400)


class CandidateListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        candidates = _candidate_qs(request.user).order_by("-created_at")
        return Response(CandidateSerializer(candidates, many=True).data)


class CandidateStatusUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        new_status = request.data.get("status")

        valid_custom_keys = set(
            _pipeline_stage_qs(request.user).values_list("key", flat=True)
        )
        if new_status not in FIXED_STAGES and new_status not in valid_custom_keys:
            return Response({"error": f"Invalid status '{new_status}'"}, status=400)

        try:
            candidate = _candidate_qs(request.user).get(pk=pk)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        candidate.status = new_status
        candidate.save()
        return Response({"success": True})


class CandidateUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        try:
            candidate = _candidate_qs(request.user).get(pk=pk)
            fields = ["name", "email", "phone", "location", "role", "experience", "education", "skills"]
            for field in fields:
                if field in request.data:
                    setattr(candidate, field, request.data[field])
            candidate.save()
            return Response(CandidateSerializer(candidate).data)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)


class CandidateDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, pk):
        try:
            candidate = _candidate_qs(request.user).get(pk=pk)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        candidate.delete()   # signals in models.py auto-delete the CV from R2
        return Response({"success": True}, status=200)


class CandidateRatingView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, candidate_id):
        try:
            candidate = _candidate_qs(request.user).get(id=candidate_id)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        rating = CandidateRating.objects.filter(candidate=candidate).first()
        if not rating:
            return Response({})
        return Response(CandidateRatingSerializer(rating).data)

    def post(self, request, candidate_id):
        try:
            candidate = _candidate_qs(request.user).get(id=candidate_id)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        rating, _ = CandidateRating.objects.get_or_create(candidate=candidate)
        serializer = CandidateRatingSerializer(rating, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# ─────────────────────────────────────────────────────────────
#  Offer Letter Views
# ─────────────────────────────────────────────────────────────

class OfferLetterView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, candidate_id):
        try:
            candidate = _candidate_qs(request.user).get(id=candidate_id)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        try:
            offer = OfferLetter.objects.get(candidate=candidate)
            return Response(OfferLetterSerializer(offer).data)
        except OfferLetter.DoesNotExist:
            return Response({})

    def post(self, request, candidate_id):
        try:
            candidate = _candidate_qs(request.user).get(pk=candidate_id)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        try:
            offer = OfferLetter.objects.get(candidate=candidate)
            created = False
        except OfferLetter.DoesNotExist:
            offer = OfferLetter(candidate=candidate)
            created = True

        data = request.data.copy()
        company = _company_settings_dict(request.user)
        if company.get("name") and not data.get("company_name"):
            data["company_name"] = company["name"]

        serializer = OfferLetterSerializer(offer, data=data, partial=True)
        if serializer.is_valid():
            serializer.save(candidate=candidate)
            return Response(serializer.data, status=201 if created else 200)
        return Response(serializer.errors, status=400)


class DownloadOfferLetterView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, candidate_id):
        try:
            candidate = _candidate_qs(request.user).get(pk=candidate_id)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        try:
            offer = OfferLetter.objects.get(candidate=candidate)
        except OfferLetter.DoesNotExist:
            return Response({"error": "Offer letter not created yet"}, status=404)

        if not offer.position or not offer.joining_date:
            return Response(
                {"error": "Offer letter is incomplete. Please fill in Position and Joining Date before downloading."},
                status=400,
            )

        try:
            pdf_bytes = generate_offer_letter_pdf(
                offer,
                candidate,
                _company_settings_dict(request.user),
            )
        except Exception as e:
            return Response({"error": f"PDF generation failed: {str(e)}"}, status=500)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        filename = f"Offer_Letter_{candidate.name.replace(' ', '_')}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class SendOfferLetterView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, candidate_id):
        try:
            candidate = _candidate_qs(request.user).get(pk=candidate_id)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        try:
            offer = OfferLetter.objects.get(candidate=candidate)
        except OfferLetter.DoesNotExist:
            return Response({"error": "Please create an offer letter first"}, status=404)

        if not candidate.email:
            return Response({"error": "Candidate does not have an email address"}, status=400)

        if not offer.position or not offer.joining_date:
            return Response(
                {"error": "Offer letter is incomplete. Please fill in at least Position and Joining Date."},
                status=400,
            )

        try:
            company = _company_settings_dict(request.user)
            company_name = company.get("name") or offer.company_name or "Company"
            pdf_bytes = generate_offer_letter_pdf(offer, candidate, company)

            email = EmailMessage(
                subject=f"Offer Letter - {offer.position} at {company_name}",
                body=(
                    f"Dear {candidate.name},\n\n"
                    f"Please find attached your offer letter for the position of {offer.position} "
                    f"at {company_name}.\n\n"
                    f"We look forward to welcoming you to our team.\n\n"
                    f"Best regards,\n"
                    f"{offer.hr_name or 'HR Team'}\n"
                    f"{company_name}"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[candidate.email],
            )
            filename = f"Offer_Letter_{candidate.name.replace(' ', '_')}.pdf"
            email.attach(filename, pdf_bytes, "application/pdf")
            email.send()

            offer.status = "sent"
            offer.save()

            return Response({"success": True, "message": f"Offer letter sent to {candidate.email}"})

        except Exception as e:
            return Response({"error": str(e)}, status=500)


# ─────────────────────────────────────────────────────────────
#  Upload CV Page Views
# ─────────────────────────────────────────────────────────────

class UploadCVListView(APIView):
    """
    GET  /HR/upload-cv/  → candidates with status in {uploaded, shortlisted, cv_rejected}
    POST /HR/upload-cv/  → upload a new CV (creates candidate with status=uploaded)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = _candidate_qs(request.user).order_by("-created_at")
        return Response(CandidateSerializer(qs, many=True).data)

    def post(self, request):
        file = request.FILES.get("cv")
        if not file:
            return Response({"error": "CV file is required."}, status=400)

        text = extract_text(file)
        extracted = extract_fields(text)

        data = {
            "name": extracted.get("name") or file.name.rsplit(".", 1)[0],
            "email": extracted.get("email", ""),
            "phone": extracted.get("phone", ""),
            "location": extracted.get("location", ""),
            "role": extracted.get("role", ""),
            "experience": extracted.get("experience", ""),
            "education": extracted.get("education", ""),
            "skills": extracted.get("skills", []),
            "cv": file,
        }

        serializer = CandidateSerializer(data=data)
        if serializer.is_valid():
            admin = _get_admin_owner(request.user)
            serializer.save(admin_owner=admin, status="uploaded")
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class UploadCVStatusUpdateView(APIView):
    """
    PATCH /HR/upload-cv/<pk>/status/
    Allowed transitions: uploaded ↔ shortlisted ↔ cv_rejected
    """
    permission_classes = [permissions.IsAuthenticated]

    UPLOAD_PAGE_STATUSES = {"uploaded", "shortlisted", "cv_rejected"}

    def patch(self, request, pk):
        new_status = request.data.get("status")
        if new_status not in self.UPLOAD_PAGE_STATUSES:
            return Response(
                {"error": f"'{new_status}' is not a valid status for this page. "
                          f"Allowed: uploaded, shortlisted, cv_rejected."},
                status=400,
            )

        try:
            candidate = _candidate_qs(request.user).get(pk=pk)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found."}, status=404)

        if candidate.status not in self.UPLOAD_PAGE_STATUSES:
            return Response(
                {"error": "This candidate is already in the interview pipeline "
                          "and cannot be edited from the Upload CV page."},
                status=400,
            )

        candidate.status = new_status
        candidate.save()
        return Response(CandidateSerializer(candidate).data)


class UploadCVInterviewDateView(APIView):
    """
    PATCH /HR/upload-cv/<pk>/interview/
    Sets or clears the interview date, time, and note for a candidate.

    Body:
        {
            "interview_date": "2025-09-15",  # null / omit to clear
            "interview_time": "10:30",       # optional
            "interview_note": "Zoom call"    # optional
        }
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        try:
            candidate = _candidate_qs(request.user).get(pk=pk)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found."}, status=404)

        candidate.interview_date = request.data.get("interview_date") or None
        candidate.interview_time = request.data.get("interview_time", "")
        candidate.interview_note = request.data.get("interview_note", "")
        candidate.save()

        return Response(CandidateSerializer(candidate).data)
