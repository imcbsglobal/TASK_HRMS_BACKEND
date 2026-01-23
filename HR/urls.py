from django.urls import path
from .views import *

urlpatterns = [
    path("candidates/", CandidateListView.as_view()),
    path("candidates/upload/", CandidateUploadView.as_view()),
    path("candidates/<int:pk>/status/", CandidateStatusUpdateView.as_view()),
    path("candidates/<int:pk>/", CandidateUpdateView.as_view()),
    path("candidates/<int:candidate_id>/rating/", CandidateRatingView.as_view()),

]
