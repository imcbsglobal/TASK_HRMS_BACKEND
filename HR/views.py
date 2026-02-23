from django.shortcuts import render
from django.http import HttpResponse
from django.core.mail import EmailMessage
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Candidate, CandidateRating, OfferLetter
from .serializers import CandidateSerializer, CandidateRatingSerializer, OfferLetterSerializer
from .utils import extract_text, extract_fields
from .offer_pdf import generate_offer_letter_pdf


# ─────────────────────────────────────────────────────────────
#  Existing views (unchanged)
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
        candidate = Candidate.objects.get(pk=pk)
        candidate.status = request.data.get("status")
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
#  Offer Letter Views
# ─────────────────────────────────────────────────────────────

class OfferLetterView(APIView):
    """
    GET  /candidates/<id>/offer/  → fetch existing offer letter (or empty {})
    POST /candidates/<id>/offer/  → create or update offer letter (save as draft)
    """

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
            # Try to get existing offer
            offer = OfferLetter.objects.get(candidate=candidate)
            created = False
        except OfferLetter.DoesNotExist:
            # Create a bare offer record first so the FK is satisfied
            offer = OfferLetter(candidate=candidate)
            created = True

        serializer = OfferLetterSerializer(offer, data=request.data, partial=True)
        if serializer.is_valid():
            # Pass candidate explicitly so FK is always set
            serializer.save(candidate=candidate)
            return Response(serializer.data, status=201 if created else 200)

        return Response(serializer.errors, status=400)


class DownloadOfferLetterView(APIView):
    """
    GET /candidates/<id>/offer/pdf/  → Download offer letter as PDF
    """

    def get(self, request, candidate_id):
        try:
            candidate = Candidate.objects.get(pk=candidate_id)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found"}, status=404)

        try:
            offer = OfferLetter.objects.get(candidate=candidate)
        except OfferLetter.DoesNotExist:
            return Response({"error": "Offer letter not created yet"}, status=404)

        # Guard: PDF generation needs at minimum position and joining_date
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
    """
    POST /candidates/<id>/offer/send/  → Generate PDF and email it to candidate
    """

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

        # Guard: must have required fields before sending
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

            # Update offer status to "sent"
            offer.status = "sent"
            offer.save()

            return Response({"success": True, "message": f"Offer letter sent to {candidate.email}"})

        except Exception as e:
            return Response({"error": str(e)}, status=500)