from django.shortcuts import render
from django.http import HttpResponse
from django.core.mail import EmailMessage
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Candidate, CandidateRating, OfferLetter, PipelineStage
from .serializers import (
    CandidateSerializer, CandidateRatingSerializer,
    OfferLetterSerializer, PipelineStageSerializer,
)
from .utils import extract_text, extract_fields
from .offer_pdf import generate_offer_letter_pdf

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

    def get(self, request):
        stages = PipelineStage.objects.all()
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
            serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class PipelineStageDetailView(APIView):
    """
    PATCH  /pipeline-stages/<pk>/  → rename or reorder a custom stage
    DELETE /pipeline-stages/<pk>/  → remove a custom stage
    """

    def _get_stage(self, pk):
        try:
            return PipelineStage.objects.get(pk=pk)
        except PipelineStage.DoesNotExist:
            return None

    def patch(self, request, pk):
        stage = self._get_stage(pk)
        if not stage:
            return Response({"error": "Stage not found"}, status=404)

        serializer = PipelineStageSerializer(stage, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        stage = self._get_stage(pk)
        if not stage:
            return Response({"error": "Stage not found"}, status=404)

        # Move any candidates still in this stage back to 'uploaded'
        Candidate.objects.filter(status=stage.key).update(status="uploaded")

        stage.delete()
        return Response({"success": True})


# ─────────────────────────────────────────────────────────────
#  Existing Candidate Views (unchanged logic)
# ─────────────────────────────────────────────────────────────

class CandidateUploadView(APIView):
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
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)


class CandidateListView(APIView):
    def get(self, request):
        candidates = Candidate.objects.all().order_by("-created_at")
        return Response(CandidateSerializer(candidates, many=True).data)


class CandidateStatusUpdateView(APIView):
    def patch(self, request, pk):
        new_status = request.data.get("status")

        # Allow fixed stages and any existing custom stage key
        valid_custom_keys = set(
            PipelineStage.objects.values_list("key", flat=True)
        )
        if new_status not in FIXED_STAGES and new_status not in valid_custom_keys:
            return Response({"error": f"Invalid status '{new_status}'"}, status=400)

        try:
            candidate = Candidate.objects.get(pk=pk)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        candidate.status = new_status
        candidate.save()
        return Response({"success": True})


class CandidateUpdateView(APIView):
    def patch(self, request, pk):
        try:
            candidate = Candidate.objects.get(pk=pk)
            fields = ["name", "email", "phone", "location", "role", "experience", "education", "skills"]
            for field in fields:
                if field in request.data:
                    setattr(candidate, field, request.data[field])
            candidate.save()
            return Response(CandidateSerializer(candidate).data)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)


class CandidateRatingView(APIView):
    def get(self, request, candidate_id):
        rating = CandidateRating.objects.filter(candidate_id=candidate_id).first()
        if not rating:
            return Response({})
        return Response(CandidateRatingSerializer(rating).data)

    def post(self, request, candidate_id):
        rating, _ = CandidateRating.objects.get_or_create(candidate_id=candidate_id)
        serializer = CandidateRatingSerializer(rating, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# ─────────────────────────────────────────────────────────────
#  Offer Letter Views (unchanged)
# ─────────────────────────────────────────────────────────────

class OfferLetterView(APIView):
    def get(self, request, candidate_id):
        try:
            offer = OfferLetter.objects.get(candidate_id=candidate_id)
            return Response(OfferLetterSerializer(offer).data)
        except OfferLetter.DoesNotExist:
            return Response({})

    def post(self, request, candidate_id):
        try:
            candidate = Candidate.objects.get(pk=candidate_id)
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
    def get(self, request, candidate_id):
        try:
            candidate = Candidate.objects.get(pk=candidate_id)
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
    def post(self, request, candidate_id):
        try:
            candidate = Candidate.objects.get(pk=candidate_id)
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