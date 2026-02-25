# master/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LeaveTypeViewSet, AllowanceViewSet, DeductionViewSet

# Create a router and register viewsets
router = DefaultRouter()
router.register(r'leave-types', LeaveTypeViewSet, basename='leave-type')
router.register(r'allowances', AllowanceViewSet, basename='allowance')
router.register(r'deductions', DeductionViewSet, basename='deduction')

urlpatterns = [
    path('', include(router.urls)),
]

"""
This creates the following URLs:

Leave Types:
- GET    /api/master/leave-types/              - List all leave types
- POST   /api/master/leave-types/              - Create new leave type
- GET    /api/master/leave-types/{id}/         - Get specific leave type
- PUT    /api/master/leave-types/{id}/         - Update leave type (full)
- PATCH  /api/master/leave-types/{id}/         - Update leave type (partial)
- DELETE /api/master/leave-types/{id}/         - Delete leave type
- GET    /api/master/leave-types/active/       - Get only active leave types

Query Parameters (for list endpoint):
- ?is_active=true/false    - Filter by active status
- ?search=keyword          - Search by name or description
"""