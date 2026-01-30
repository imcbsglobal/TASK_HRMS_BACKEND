from django.urls import path
from .views import (
    CandidateToEmployeeView,
    EmployeeListCreateView,
    EmployeeDetailView,
    DepartmentListCreateView,
    DepartmentDetailView,
    CustomFieldDefinitionListCreateView,
    CustomFieldDefinitionDetailView,
)

urlpatterns = [
    # Employee endpoints
    path("candidate-to-employee/<int:candidate_id>/", CandidateToEmployeeView.as_view()),
    path("employees/", EmployeeListCreateView.as_view()),
    path("employees/<int:pk>/", EmployeeDetailView.as_view()),
    
    # Department endpoints - Full CRUD
    path("departments/", DepartmentListCreateView.as_view(), name="department-list-create"),
    path("departments/<int:pk>/", DepartmentDetailView.as_view(), name="department-detail"),
    
    # Custom Field Definition endpoints
    path("custom-fields/", CustomFieldDefinitionListCreateView.as_view(), name="custom-field-list-create"),
    path("custom-fields/<int:pk>/", CustomFieldDefinitionDetailView.as_view(), name="custom-field-detail"),
]