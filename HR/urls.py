from django.urls import path
from .views import *

urlpatterns = [
    # Candidates
    path("candidates/", CandidateListView.as_view()),
    path("candidates/upload/", CandidateUploadView.as_view()),
    path("candidates/<int:pk>/status/", CandidateStatusUpdateView.as_view()),
    path("candidates/<int:pk>/", CandidateUpdateView.as_view()),
    path("candidates/<int:candidate_id>/rating/", CandidateRatingView.as_view()),

    # Offer Letter
    path("candidates/<int:candidate_id>/offer/", OfferLetterView.as_view()),
    path("candidates/<int:candidate_id>/offer/send/", SendOfferLetterView.as_view()),
    path("candidates/<int:candidate_id>/offer/pdf/", DownloadOfferLetterView.as_view()),

    # Pipeline Stages (company-customisable)
    path("pipeline-stages/", PipelineStageListView.as_view()),
    path("pipeline-stages/<int:pk>/", PipelineStageDetailView.as_view()),
]