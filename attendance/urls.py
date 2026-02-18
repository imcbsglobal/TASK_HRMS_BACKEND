from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AttendanceViewSet, AttendanceSettingsViewSet

# Create a router and register our viewsets
router = DefaultRouter()
router.register(r'', AttendanceViewSet, basename='attendance')

# Create separate router for settings
settings_router = DefaultRouter()
settings_router.register(r'settings', AttendanceSettingsViewSet, basename='attendance-settings')

urlpatterns = [
    # Include the main attendance routes
    path('', include(router.urls)),
    
    # Include the settings routes
    path('', include(settings_router.urls)),
]

# Available endpoints:
# GET    /api/attendance/                        - List all attendance records
# POST   /api/attendance/                        - Create attendance record (admin)
# GET    /api/attendance/{id}/                   - Get specific attendance record
# PATCH  /api/attendance/{id}/                   - Update status/notes (admin) — sets is_verified=True
# DELETE /api/attendance/{id}/                   - Delete attendance record
# 
# POST   /api/attendance/check-in/               - Check in for today
# POST   /api/attendance/check-out/              - Check out for today
# GET    /api/attendance/today/                  - Get today's attendance status
# 
# POST   /api/attendance/{id}/verify/            - Admin verify & set status (Admin)
#        Body: {"status": "present|absent|half_day|late|leave", "notes": "optional note"}
# 
# POST   /api/attendance/request-late/           - Submit late request (User)
#        Body: {"reason": "Traffic jam", "date": "2024-02-13"} (date optional)
# 
# POST   /api/attendance/{id}/approve-late/      - Approve/Reject late request (Admin)
#        Body: {"action": "approve"} or {"action": "reject"}
# 
# GET    /api/attendance/pending-late-requests/  - Get all pending late requests (Admin)
# 
# GET    /api/attendance/monthly-stats/          - Get monthly statistics
#        Query params: ?month=2&year=2026
# 
# GET    /api/attendance/history/                - Get attendance history
#        Query params: ?days=30
# 
# GET    /api/attendance/settings/               - List settings
# GET    /api/attendance/settings/current/       - Get current settings