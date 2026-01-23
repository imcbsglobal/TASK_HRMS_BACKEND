from rest_framework import serializers
from .models import Candidate, CandidateRating


class CandidateRatingSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateRating
        fields = "__all__"


class CandidateSerializer(serializers.ModelSerializer):
    rating = CandidateRatingSerializer(read_only=True)

    class Meta:
        model = Candidate
        fields = "__all__"
