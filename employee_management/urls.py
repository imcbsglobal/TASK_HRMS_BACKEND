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
    EmployeeDocumentListCreateView,
    EmployeeDocumentDetailView,
    UpcomingIncrementsView,
    SalaryIncrementHistoryView,
    SalaryIncrementHistoryDetailView,
)

urlpatterns = [
    # Employee endpoints
    path("candidate-to-employee/<int:candidate_id>/", CandidateToEmployeeView.as_view()),
    path("employees/", EmployeeListCreateView.as_view()),
    path("employees/<int:pk>/", EmployeeDetailView.as_view()),

    # Salary Increment History — list/create
    path("employees/<int:pk>/salary-increments/", SalaryIncrementHistoryView.as_view(), name="salary-increment-history"),
    # Salary Increment History — edit/delete individual record
    path("employees/<int:pk>/salary-increments/<int:log_id>/", SalaryIncrementHistoryDetailView.as_view(), name="salary-increment-detail"),

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

    # Document endpoints
    path("employees/<int:employee_id>/documents/", EmployeeDocumentListCreateView.as_view(), name="employee-document-list-create"),
    path("employees/<int:employee_id>/documents/<int:pk>/", EmployeeDocumentDetailView.as_view(), name="employee-document-detail"),

    # Increment reminders (dashboard widget)
    path("upcoming-increments/", UpcomingIncrementsView.as_view(), name="upcoming-increments"),
]