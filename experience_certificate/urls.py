from django.urls import path

from .views import (
    ExperienceCertificateDetailView,
    ExperienceCertificateDownloadView,
    ExperienceCertificateIssueView,
    ExperienceCertificateListCreateView,
    ExperienceCertificateRevokeView,
)


urlpatterns = [
    path('', ExperienceCertificateListCreateView.as_view(), name='experience-certificate-list-create'),
    path('<int:pk>/', ExperienceCertificateDetailView.as_view(), name='experience-certificate-detail'),
    path('<int:pk>/issue/', ExperienceCertificateIssueView.as_view(), name='experience-certificate-issue'),
    path('<int:pk>/revoke/', ExperienceCertificateRevokeView.as_view(), name='experience-certificate-revoke'),
    path('<int:pk>/download/', ExperienceCertificateDownloadView.as_view(), name='experience-certificate-download'),
]

