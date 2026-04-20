# ─────────────────────────────────────────────────────────────────────────────
# REPLACE your existing urls.py with this file
# ─────────────────────────────────────────────────────────────────────────────

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AttendanceViewSet,
    AttendanceSettingsViewSet,
    LeaveRequestViewSet,
    LateArrivalRequestViewSet,
    EarlyDepartureRequestViewSet,
    FaceRecognitionViewSet,
)

router = DefaultRouter()

# Register specific prefixes BEFORE the catch-all attendance router
router.register(r'leave-requests',           LeaveRequestViewSet,          basename='leave-requests')
router.register(r'late-arrival-requests',    LateArrivalRequestViewSet,    basename='late-arrival-requests')
router.register(r'early-departure-requests', EarlyDepartureRequestViewSet, basename='early-departure-requests')
router.register(r'settings',                 AttendanceSettingsViewSet,    basename='attendance-settings')
router.register(r'face',                     FaceRecognitionViewSet,       basename='face')
router.register(r'',                         AttendanceViewSet,            basename='attendance')

urlpatterns = [
    path('', include(router.urls)),
]