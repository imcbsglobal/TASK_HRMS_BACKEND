from django.urls import path
from .views import (
    CandidateToEmployeeView,
    EmployeeListCreateView,
    EmployeeDetailView,
    CompleteOffboardingView,
    DepartmentListCreateView,
    DepartmentDetailView,
    CustomFieldDefinitionListCreateView,
    CustomFieldDefinitionDetailView,
    EmployeeAssetListCreateView,
    EmployeeAssetDetailView,
)

urlpatterns = [
    # Employee endpoints
    path("candidate-to-employee/<int:candidate_id>/", CandidateToEmployeeView.as_view()),
    path("employees/", EmployeeListCreateView.as_view()),
    path("employees/<int:pk>/", EmployeeDetailView.as_view()),

    # ── Offboarding ────────────────────────────────────────────────────────────
    # POST: marks employee terminated + deactivates linked user account
    path("employees/<int:pk>/complete-offboarding/", CompleteOffboardingView.as_view(), name="complete-offboarding"),

    # Department endpoints - Full CRUD
    path("departments/", DepartmentListCreateView.as_view(), name="department-list-create"),
    path("departments/<int:pk>/", DepartmentDetailView.as_view(), name="department-detail"),

    # Custom Field Definition endpoints
    path("custom-fields/", CustomFieldDefinitionListCreateView.as_view(), name="custom-field-list-create"),
    path("custom-fields/<int:pk>/", CustomFieldDefinitionDetailView.as_view(), name="custom-field-detail"),

    # Asset endpoints
    path("employees/<int:employee_id>/assets/", EmployeeAssetListCreateView.as_view()),
    path("employees/<int:employee_id>/assets/<int:pk>/", EmployeeAssetDetailView.as_view()),
]