from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AttendanceViewSet, AttendanceSettingsViewSet, LeaveRequestViewSet

# Single router — leave-requests and settings must be registered BEFORE
# the empty-prefix AttendanceViewSet, otherwise the catch-all attendance
# router swallows every request first.
router = DefaultRouter()
router.register(r'leave-requests', LeaveRequestViewSet, basename='leave-requests')
router.register(r'settings', AttendanceSettingsViewSet, basename='attendance-settings')
router.register(r'', AttendanceViewSet, basename='attendance')

urlpatterns = [
    path('', include(router.urls)),
]