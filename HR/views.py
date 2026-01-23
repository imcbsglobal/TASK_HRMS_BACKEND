from django.shortcuts import render

# Create your views here.
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Candidate
from .models import CandidateRating
from .serializers import CandidateSerializer,CandidateRatingSerializer
from .utils import extract_text, extract_fields

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
            
            # Update fields if provided
            if 'name' in request.data:
                candidate.name = request.data['name']
            if 'email' in request.data:
                candidate.email = request.data['email']
            if 'phone' in request.data:
                candidate.phone = request.data['phone']
            if 'location' in request.data:
                candidate.location = request.data['location']
            if 'role' in request.data:
                candidate.role = request.data['role']
            if 'experience' in request.data:
                candidate.experience = request.data['experience']
            if 'education' in request.data:
                candidate.education = request.data['education']
            if 'skills' in request.data:
                candidate.skills = request.data['skills']
            
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
        serializer = CandidateRatingSerializer(
            rating, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
