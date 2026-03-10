# attendance/geofence.py
"""
Geofence helpers for attendance check-in / check-out.

Usage inside your AttendanceViewSet:

    from .geofence import validate_geofence
    from .models   import AttendanceSettings

    settings_obj = AttendanceSettings.objects.order_by('-id').first()
    allowed, error_msg, distance_m = validate_geofence(
        request.user,
        request.data.get('latitude'),
        request.data.get('longitude'),
        settings_obj,
    )
    if not allowed:
        return Response({'error': error_msg}, status=403)
"""

import math


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres between two GPS coordinates."""
    R = 6_371_000  # Earth radius, metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def validate_geofence(user, latitude, longitude, settings_obj):
    """
    Validate whether the user is allowed to check in/out from their current location.

    Returns:
        (allowed: bool, error_message: str | None, distance_metres: float | None)

    Rules
    -----
    OUT_OF_OFFICE users  → always allowed, no distance check.
    IN_OFFICE users      → must be within settings_obj.office_radius_meters of the
                           configured office coordinates.
                           If office coords are not configured, check is skipped
                           (fail-open) so the system keeps working until an admin
                           sets the office location.
    """
    work_location = getattr(user, 'work_location', 'IN_OFFICE')

    # ── OUT_OF_OFFICE: no restriction ────────────────────────────────────────
    if work_location == 'OUT_OF_OFFICE':
        return True, None, None

    # ── IN_OFFICE: check office coords are configured ────────────────────────
    office_lat = getattr(settings_obj, 'office_latitude',  None) if settings_obj else None
    office_lon = getattr(settings_obj, 'office_longitude', None) if settings_obj else None

    if office_lat is None or office_lon is None:
        # Fail-open: office location not set yet, allow punch
        return True, None, None

    # ── User must supply their GPS coords ────────────────────────────────────
    if latitude is None or longitude is None:
        return (
            False,
            "Your location is required for in-office check-in/out. "
            "Please enable location access in your browser and try again.",
            None,
        )

    # ── Distance calculation ──────────────────────────────────────────────────
    try:
        distance = haversine_distance(
            float(latitude), float(longitude),
            float(office_lat), float(office_lon),
        )
    except (TypeError, ValueError):
        return (
            False,
            "Invalid location data. Please try again.",
            None,
        )

    radius = getattr(settings_obj, 'office_radius_meters', 100) or 100

    if distance > radius:
        return (
            False,
            (
                f"You are {distance:.0f} m away from the office "
                f"(allowed radius: {radius} m). "
                "In-office employees must be within the office premises to check in/out. "
                "If you are working remotely, please contact your admin to update "
                "your work location to 'Out of Office'."
            ),
            distance,
        )

    return True, None, distance