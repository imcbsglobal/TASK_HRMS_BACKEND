from rest_framework import serializers
from .models import Candidate, CandidateRating, OfferLetter


class CandidateRatingSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateRating
        fields = "__all__"


class OfferLetterSerializer(serializers.ModelSerializer):
    # Make model-required fields optional at API level so partial drafts can be saved
    position = serializers.CharField(required=False, allow_blank=True, default="")
    salary = serializers.CharField(required=False, allow_blank=True, default="")
    joining_date = serializers.DateField(required=False, allow_null=True, default=None)

    class Meta:
        model = OfferLetter
        fields = "__all__"
        read_only_fields = ["candidate", "offer_date", "created_at", "updated_at"]


class CandidateSerializer(serializers.ModelSerializer):
    rating = CandidateRatingSerializer(read_only=True)
    offer_letter = OfferLetterSerializer(read_only=True)

    class Meta:
        model = Candidate
        fields = "__all__"