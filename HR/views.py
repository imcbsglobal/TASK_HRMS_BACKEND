from django.shortcuts import render
from django.http import HttpResponse
from django.core.mail import EmailMessage
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from .models import Candidate, CandidateRating, OfferLetter, PipelineStage
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

# ─────────────────────────────────────────────────────────────
#  Fixed (built-in) stage keys — these cannot be deleted
# ─────────────────────────────────────────────────────────────
FIXED_STAGES = {"uploaded", "selected", "rejected"}


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
        # Prevent overriding fixed stages
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

        # Move any candidates still in this stage back to 'uploaded'
        _candidate_qs(request.user).filter(status=stage.key).update(status="uploaded")

        stage.delete()
        return Response({"success": True})


# ─────────────────────────────────────────────────────────────
#  Existing Candidate Views (unchanged logic)
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
            "experience": extracted.get("experience", ""),
            "education": extracted.get("education", ""),
            "skills": extracted.get("skills", []),
            "cv": file,
        }

        serializer = CandidateSerializer(data=data)
        if serializer.is_valid():
            admin = _get_admin_owner(request.user)
            serializer.save(admin_owner=admin)
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

        # Allow fixed stages and any existing custom stage key
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
#  Offer Letter Views (unchanged)
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

        serializer = OfferLetterSerializer(offer, data=request.data, partial=True)
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
            pdf_bytes = generate_offer_letter_pdf(offer, candidate)
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
            pdf_bytes = generate_offer_letter_pdf(offer, candidate)

            email = EmailMessage(
                subject=f"Offer Letter – {offer.position} at {offer.company_name}",
                body=(
                    f"Dear {candidate.name},\n\n"
                    f"Please find attached your offer letter for the position of {offer.position} "
                    f"at {offer.company_name}.\n\n"
                    f"We look forward to welcoming you to our team.\n\n"
                    f"Best regards,\n"
                    f"{offer.hr_name or 'HR Team'}\n"
                    f"{offer.company_name}"
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