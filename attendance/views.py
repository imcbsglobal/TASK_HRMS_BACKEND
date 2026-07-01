from django.shortcuts import render
from django.contrib.auth import get_user_model
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum, Count, Q, Avg
from datetime import datetime, timedelta
from calendar import monthrange
import pytz

# ── JWT helpers for auto-login after face recognition ────────────────────────
try:
    from rest_framework_simplejwt.tokens import RefreshToken as _RefreshToken
    def _generate_tokens_for_user(user):
        """Return {'access': str, 'refresh': str} for the given user."""
        refresh = _RefreshToken.for_user(user)
        return {
            'refresh': str(refresh),
            'access':  str(refresh.access_token),
        }
except ImportError:
    # Fallback: return empty tokens if simplejwt is not installed
    def _generate_tokens_for_user(user):
        return {'access': '', 'refresh': ''}
# ─────────────────────────────────────────────────────────────────────────────

import tempfile
import os
import base64
import json
import requests
import numpy as np
import io
from django.core.files.base import ContentFile
from deepface import DeepFace

# ── Pillow import (used for EXIF-rotation normalisation) ─────────────────────
try:
    from PIL import Image as _PilImage, ImageOps as _PilImageOps
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
# ─────────────────────────────────────────────────────────────────────────────

# ── Fix 1: Pre-load Facenet512 model at server startup ────────────────────────
# DeepFace loads the neural network from disk on the first call (~10-25s).
# Building it here means that cost is paid once when Django starts, not on
# every employee punch.  Subsequent calls reuse the in-memory model instantly.
try:
    DeepFace.build_model('Facenet512')
except Exception:
    pass  # Non-fatal — model will lazy-load on first use if this fails
# ─────────────────────────────────────────────────────────────────────────────


from .models import Attendance, AttendanceSettings, LeaveRequest, LateArrivalRequest, EarlyDepartureRequest, EmployeeFaceData, BreakRecord, SalaryAdvanceRequest, WFHRequest
from .geofence import validate_geofence
from activitylog.utils import log_activity

# ── WhatsApp notifications (fire-and-forget, never raises) ───────────────────
try:
    from watsapp_config.notify import send_notification as _wa_notify
except ImportError:
    def _wa_notify(*args, **kwargs): pass  # graceful fallback if app not installed
# ─────────────────────────────────────────────────────────────────────────────
from .serializers import (
    AttendanceSerializer, CheckInSerializer, CheckOutSerializer,
    MonthlyStatsSerializer, TodayAttendanceSerializer,
    AttendanceSettingsSerializer, LateRequestSerializer, LateApprovalSerializer,
    VerifyAttendanceSerializer, LeaveRequestSerializer, CreateLeaveRequestSerializer,
    LeaveApprovalSerializer,
    LateArrivalRequestSerializer, CreateLateArrivalRequestSerializer,
    LateArrivalApprovalSerializer,
    EarlyDepartureRequestSerializer, CreateEarlyDepartureRequestSerializer,
    EarlyDepartureApprovalSerializer,
    BreakRecordSerializer,
    SalaryAdvanceRequestSerializer, CreateSalaryAdvanceRequestSerializer,
    SalaryAdvanceApprovalSerializer,
    WFHRequestSerializer, CreateWFHRequestSerializer, WFHApprovalSerializer,
)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _is_admin(user):
    return (
        user.is_staff or user.is_superuser or
        getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin'] or
        getattr(user, 'is_admin_user', False)
    )


def _is_sunday_working(admin_owner):
    """
    Return True if the PayrollPolicy for this tenant has
    salaryCalculation.sundayWorking = True, meaning Sunday is a regular
    working day and should be included in all day-count calculations.
    """
    try:
        from master.models import PayrollPolicy
        policy_obj = PayrollPolicy.objects.filter(admin_owner=admin_owner).first()
        if policy_obj and policy_obj.policy_data:
            return bool(
                policy_obj.policy_data
                .get('salaryCalculation', {})
                .get('sundayWorking', False)
            )
    except Exception:
        pass
    return False


def _is_working_day(date_obj, sunday_working=False):
    """
    Return True if the given date should count as a working day.
    Normally Mon–Sat (weekday 0–5); also includes Sunday when sunday_working=True.
    """
    wd = date_obj.weekday()
    if sunday_working:
        return True   # all 7 days are working days
    return wd < 6     # Mon–Sat (exclude Sunday = weekday 6)


def _get_admin_owner(user):
    """
    Return the admin_owner for tenant-scoped writes.
    - Only true tenant admins (role=ADMIN/SUPER_ADMIN, is_staff, or is_superuser)
      are the tenant owner — they ARE the owner.
    - is_admin_user accounts have role='USER' and belong to a real admin_owner,
      so they must use their admin_owner field (not themselves).
    - Regular employees also resolve through their admin_owner field.
    """
    role = getattr(user, 'role', None)
    if user.is_staff or user.is_superuser or role in ('ADMIN', 'SUPER_ADMIN', 'admin', 'super_admin'):
        return user
    return getattr(user, 'admin_owner', None)


def _get_full_name(user):
    """
    Safely get a user's full name.
    Works with both Django's default User (get_full_name()) and custom User
    models that expose a direct `full_name` field.
    """
    if callable(getattr(user, 'get_full_name', None)):
        name = user.get_full_name()
        if name and name.strip():
            return name.strip()
    # Fallback: direct full_name field (custom User models)
    name = getattr(user, 'full_name', None)
    if name and str(name).strip():
        return str(name).strip()
    # Last resort: first_name + last_name
    parts = [getattr(user, 'first_name', ''), getattr(user, 'last_name', '')]
    joined = ' '.join(p for p in parts if p)
    return joined or user.username


# ─────────────────────────────────────────────────────────────────────────────
# AUTO-CREATE LATE / EARLY REQUESTS ON CHECK-IN / CHECK-OUT
# ─────────────────────────────────────────────────────────────────────────────

def _get_employee_duty_times(user, admin_owner):
    """
    Return (duty_start, duty_end) for the employee linked to *user*.
    Priority: Employee.duty_start_time / duty_end_time → AttendanceSettings.
    Either value may be None if not configured.
    """
    start = end = None
    try:
        from employee_management.models import Employee
        emp = Employee.objects.filter(email=user.email, admin_owner=admin_owner).first()
        if emp:
            start = emp.duty_start_time
            end   = emp.duty_end_time
    except Exception:
        pass
    settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
    if settings_obj:
        start = start or settings_obj.office_start_time
        end   = end   or settings_obj.office_end_time
    return start, end


def _get_payroll_policy_late_early(admin_owner):
    """
    Read PayrollPolicy for this tenant and return (late_config, early_config).
    Each is a dict (or empty dict if not configured).
    """
    try:
        from master.models import PayrollPolicy
        policy_obj = PayrollPolicy.objects.filter(admin_owner=admin_owner).first()
        if policy_obj and policy_obj.policy_data:
            att = policy_obj.policy_data.get('attendance', {})
            return att.get('lateArrival', {}), att.get('earlyDeparture', {})
    except Exception:
        pass
    return {}, {}


def _auto_create_late_request(user, admin_owner, attendance):
    """
    After a successful check-in, determine if the employee is late
    according to the configured rules and auto-create a LateArrivalRequest.
    Silently succeeds – never raises.
    """
    try:
        if not attendance or not attendance.check_in_time:
            return

        duty_start, _ = _get_employee_duty_times(user, admin_owner)
        if not duty_start:
            return

        late_policy, _ = _get_payroll_policy_late_early(admin_owner)
        if not late_policy.get('enabled', True):
            return

        grace_min = int(late_policy.get('gracePeriodMin', 0))
        ci_local  = attendance.check_in_time.astimezone(pytz.timezone('Asia/Kolkata'))
        ci_time   = ci_local.time()

        # Build the expected start-of-day datetime (same date as check-in)
        from datetime import datetime as _dt, timedelta as _td
        base = _dt(ci_local.year, ci_local.month, ci_local.day)
        limit_dt = base.replace(hour=duty_start.hour, minute=duty_start.minute, second=0)
        limit_dt += _td(minutes=grace_min)

        if ci_local <= limit_dt:
            return  # within grace period – not late

        minutes_late = int((ci_local - limit_dt).total_seconds() / 60)

        LateArrivalRequest.objects.get_or_create(
            user=user,
            date=attendance.date,
            admin_owner=admin_owner,
            defaults={
                'expected_arrival_time': ci_time,
                'reason': f'Auto-detected late check-in ({minutes_late} min late)',
                'status': 'pending',
            },
        )
    except Exception:
        pass


def _auto_create_early_request(user, admin_owner, attendance):
    """
    After a successful check-out, determine if the employee left early
    according to the configured rules and auto-create an EarlyDepartureRequest.
    Silently succeeds – never raises.
    """
    try:
        if not attendance or not attendance.check_out_time:
            return

        _, duty_end = _get_employee_duty_times(user, admin_owner)
        if not duty_end:
            return

        _, early_policy = _get_payroll_policy_late_early(admin_owner)
        if not early_policy.get('enabled', True):
            return

        buffer_min = int(early_policy.get('earlyBufferMin', 0))
        co_local   = attendance.check_out_time.astimezone(pytz.timezone('Asia/Kolkata'))
        co_time    = co_local.time()

        from datetime import datetime as _dt, timedelta as _td
        base = _dt(co_local.year, co_local.month, co_local.day)
        limit_dt = base.replace(hour=duty_end.hour, minute=duty_end.minute, second=0)
        limit_dt -= _td(minutes=buffer_min)

        if co_local >= limit_dt:
            return  # within buffer – not early

        minutes_early = int((limit_dt - co_local).total_seconds() / 60)

        EarlyDepartureRequest.objects.get_or_create(
            user=user,
            date=attendance.date,
            admin_owner=admin_owner,
            defaults={
                'expected_departure_time': co_time,
                'reason': f'Auto-detected early check-out ({minutes_early} min early)',
                'status': 'pending',
            },
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# ATTENDANCE VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class AttendanceViewSet(viewsets.ModelViewSet):
    serializer_class = AttendanceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        admin_owner = _get_admin_owner(user)

        # Base queryset scoped to this tenant
        qs = Attendance.objects.filter(admin_owner=admin_owner)

        if _is_admin(user):
            return qs
        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user, admin_owner=_get_admin_owner(request.user))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        allowed_fields = {'status', 'notes', 'is_verified'}
        data = {k: v for k, v in request.data.items() if k in allowed_fields}

        if 'status' in data or data.get('is_verified'):
            data['is_verified'] = True

        serializer = self.get_serializer(instance, data=data, partial=True)
        serializer.is_valid(raise_exception=True)

        if data.get('is_verified'):
            instance.is_verified = True
            instance.verified_by = request.user
            instance.verified_at = timezone.now()
        if 'status' in data:
            instance.status = data['status']
        if 'notes' in data:
            instance.notes = data['notes']

        update_fields = ['updated_at']
        if 'status' in data:
            update_fields.append('status')
        if 'notes' in data:
            update_fields.append('notes')
        if data.get('is_verified'):
            update_fields += ['is_verified', 'verified_by', 'verified_at']
        instance.save(update_fields=update_fields)

        return Response(AttendanceSerializer(instance).data)

    @action(detail=True, methods=['post'], url_path='verify')
    def verify_attendance(self, request, pk=None):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can verify attendance.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = VerifyAttendanceSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        attendance = self.get_object()
        attendance.status = serializer.validated_data['status']
        attendance.is_verified = True
        attendance.verified_by = request.user
        attendance.verified_at = timezone.now()
        if serializer.validated_data.get('notes'):
            attendance.notes = serializer.validated_data['notes']

        attendance.save(update_fields=['status', 'is_verified', 'verified_by', 'verified_at', 'notes', 'updated_at'])

        return Response({
            'message': f'Attendance verified. Status set to "{attendance.get_status_display()}".',
            'attendance': AttendanceSerializer(attendance).data
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='waive-missed-punch')
    def waive_missed_punch(self, request, pk=None):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can waive missed punches.'}, status=status.HTTP_403_FORBIDDEN)
        attendance = self.get_object()
        attendance.check_out_waived = True
        attendance.save(update_fields=['check_out_waived', 'updated_at'])
        return Response({
            'message': 'Missed punch waived successfully.',
            'attendance': AttendanceSerializer(attendance).data
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='manual-mark')
    def manual_mark(self, request):
        """
        Admin-only: manually create or update an attendance record for any user on any date.
        POST /attendance/manual-mark/
        Body: { user_id, date (YYYY-MM-DD), status, check_in_time (HH:MM, optional),
                check_out_time (HH:MM, optional), notes (optional) }
        """
        admin = request.user
        if not _is_admin(admin):
            return Response({'error': 'Only admins can manually mark attendance.'}, status=status.HTTP_403_FORBIDDEN)

        user_id = request.data.get('user_id')
        date_str = request.data.get('date')
        att_status = request.data.get('status')
        check_in_str = request.data.get('check_in_time')
        check_out_str = request.data.get('check_out_time')
        notes = request.data.get('notes', '')

        if not user_id or not date_str or not att_status:
            return Response({'error': 'user_id, date and status are required.'}, status=status.HTTP_400_BAD_REQUEST)

        if att_status not in ['present', 'absent', 'half_day', 'late', 'leave']:
            return Response({'error': 'Invalid status.'}, status=status.HTTP_400_BAD_REQUEST)

        User = get_user_model()
        admin_owner = _get_admin_owner(admin)

        # Ensure the target user belongs to this tenant
        try:
            target_user = User.objects.get(pk=user_id, admin_owner=admin_owner)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        ist = pytz.timezone('Asia/Kolkata')
        check_in_dt = check_out_dt = None
        if check_in_str:
            try:
                h, m = map(int, check_in_str.split(':'))
                check_in_dt = ist.localize(datetime(att_date.year, att_date.month, att_date.day, h, m))
            except Exception:
                return Response({'error': 'Invalid check_in_time. Use HH:MM.'}, status=status.HTTP_400_BAD_REQUEST)
        if check_out_str:
            try:
                h, m = map(int, check_out_str.split(':'))
                check_out_dt = ist.localize(datetime(att_date.year, att_date.month, att_date.day, h, m))
            except Exception:
                return Response({'error': 'Invalid check_out_time. Use HH:MM.'}, status=status.HTTP_400_BAD_REQUEST)

        attendance, created = Attendance.objects.get_or_create(
            user=target_user,
            date=att_date,
            admin_owner=admin_owner,
            defaults={
                'status': att_status,
                'check_in_time': check_in_dt,
                'check_out_time': check_out_dt,
                'check_in_method': 'manual' if check_in_dt else None,
                'check_out_method': 'manual' if check_out_dt else None,
                'notes': notes,
                'is_verified': True,
                'verified_by': admin,
                'verified_at': timezone.now(),
            }
        )
        if not created:
            attendance.status = att_status
            attendance.is_verified = True
            attendance.verified_by = admin
            attendance.verified_at = timezone.now()
            attendance.notes = notes
            if check_in_dt is not None:
                # Only overwrite check_in_method when the check-in time is actually
                # being changed (admin explicitly set a new time).  If the submitted
                # time matches what is already stored, the original method (phone /
                # face / normal) must be preserved so a manual checkout does not
                # silently flip the check-in badge to "manual".
                existing_ci = attendance.check_in_time
                ci_changed = (
                    existing_ci is None or
                    # Compare at-minute precision to ignore sub-second drift
                    existing_ci.astimezone(pytz.timezone('Asia/Kolkata')).replace(second=0, microsecond=0)
                    != check_in_dt.replace(second=0, microsecond=0)
                )
                attendance.check_in_time = check_in_dt
                if ci_changed:
                    attendance.check_in_method = 'manual'
                # else: leave check_in_method untouched (phone / face / normal)
            if check_out_dt is not None:
                attendance.check_out_time = check_out_dt
                attendance.check_out_method = 'manual'
            attendance.save(update_fields=[
                'status', 'is_verified', 'verified_by', 'verified_at',
                'notes', 'check_in_time', 'check_out_time',
                'check_in_method', 'check_out_method', 'updated_at'
            ])

        if attendance.check_in_time and attendance.check_out_time:
            attendance.calculate_hours()
            attendance.save(update_fields=['total_hours'])

        return Response({
            'message': f'Attendance {"created" if created else "updated"} successfully.',
            'attendance': AttendanceSerializer(attendance).data,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='check-in')
    def check_in(self, request):
        serializer = CheckInSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        today = timezone.now().date()
        current_time = timezone.now()

        latitude = serializer.validated_data.get('latitude')
        longitude = serializer.validated_data.get('longitude')
        address = serializer.validated_data.get('address', '')

        # ── Punch-method toggle gate ──────────────────────────────────────────
        admin_owner = _get_admin_owner(user)
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        if settings_obj and not settings_obj.normal_checkin_enabled:
            return Response(
                {'error': 'Normal check-in is currently disabled. Please use the face-recognition kiosk to punch in.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        # ─────────────────────────────────────────────────────────────────────

        # ── Geofence enforcement ──────────────────────────────────────────────
        allowed, geo_error, _ = validate_geofence(user, latitude, longitude, settings_obj, today)
        if not allowed:
            return Response({'error': geo_error}, status=status.HTTP_403_FORBIDDEN)
        # ─────────────────────────────────────────────────────────────────────

        attendance, created = Attendance.objects.get_or_create(
            user=user,
            date=today,
            admin_owner=admin_owner,
            defaults={
                'check_in_time': current_time,
                'check_in_method': 'normal',
                'notes': serializer.validated_data.get('notes', ''),
                'check_in_latitude': latitude,
                'check_in_longitude': longitude,
                'check_in_address': address,
            }
        )

        if not created:
            if not attendance.check_in_time:
                attendance.check_in_time = current_time
                attendance.check_in_method = 'normal'
                attendance.notes = serializer.validated_data.get('notes', '')
                attendance.check_in_latitude = latitude
                attendance.check_in_longitude = longitude
                attendance.check_in_address = address
                attendance.save()
            else:
                return Response({'error': 'Already checked in today'}, status=status.HTTP_400_BAD_REQUEST)

        # ── WhatsApp: punch_in notification ───────────────────────────────────
        ist_tz = pytz.timezone('Asia/Kolkata')
        ci_local = attendance.check_in_time.astimezone(ist_tz)
        _wa_notify(
            admin_owner   = admin_owner,
            purpose_key   = 'punch_in',
            employee_user = user,
            context       = {
                'name': _get_full_name(user),
                'time': ci_local.strftime('%I:%M %p'),
                'date': str(ci_local.date()),
            },
        )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=user,
            action_type='CREATE',
            module='Attendance',
            description=f"Checked in at {ci_local.strftime('%I:%M %p')}",
            request=request,
        )

        # ── Auto-detect late check-in ──────────────────────────────────────────
        _auto_create_late_request(user, admin_owner, attendance)
        # ─────────────────────────────────────────────────────────────────────

        return Response({
            'message': 'Successfully checked in',
            'attendance': AttendanceSerializer(attendance).data
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='check-out')
    def check_out(self, request):
        serializer = CheckOutSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        today = timezone.now().date()
        current_time = timezone.now()

        latitude = serializer.validated_data.get('latitude')
        longitude = serializer.validated_data.get('longitude')
        address = serializer.validated_data.get('address', '')

        # ── Punch-method toggle gate ──────────────────────────────────────────
        admin_owner = _get_admin_owner(user)
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        if settings_obj and not settings_obj.normal_checkin_enabled:
            return Response(
                {'error': 'Normal check-out is currently disabled. Please use the face-recognition kiosk to punch out.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        # ─────────────────────────────────────────────────────────────────────

        # ── Geofence enforcement ──────────────────────────────────────────────
        allowed, geo_error, _ = validate_geofence(user, latitude, longitude, settings_obj, today)
        if not allowed:
            return Response({'error': geo_error}, status=status.HTTP_403_FORBIDDEN)
        # ─────────────────────────────────────────────────────────────────────

        try:
            attendance = Attendance.objects.get(user=user, date=today, admin_owner=admin_owner)
        except Attendance.DoesNotExist:
            return Response({'error': 'No check-in record found for today'}, status=status.HTTP_400_BAD_REQUEST)

        if attendance.check_out_time:
            return Response({'error': 'Already checked out today'}, status=status.HTTP_400_BAD_REQUEST)

        attendance.check_out_time = current_time
        attendance.check_out_method = 'normal'
        attendance.check_out_latitude = latitude
        attendance.check_out_longitude = longitude
        attendance.check_out_address = address
        if serializer.validated_data.get('notes'):
            attendance.notes = serializer.validated_data['notes']
        attendance.save()

        # ── WhatsApp: punch_out notification ──────────────────────────────────
        ist_tz = pytz.timezone('Asia/Kolkata')
        co_local = attendance.check_out_time.astimezone(ist_tz) if attendance.check_out_time else timezone.now().astimezone(ist_tz)
        attendance.calculate_hours()
        _wa_notify(
            admin_owner   = admin_owner,
            purpose_key   = 'punch_out',
            employee_user = user,
            context       = {
                'name':        _get_full_name(user),
                'time':        co_local.strftime('%I:%M %p'),
                'date':        str(co_local.date()),
                'total_hours': str(attendance.total_hours),
            },
        )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=user,
            action_type='UPDATE',
            module='Attendance',
            description=f"Checked out at {co_local.strftime('%I:%M %p')}",
            request=request,
        )

        # ── Auto-detect early check-out ────────────────────────────────────────
        _auto_create_early_request(user, admin_owner, attendance)
        # ─────────────────────────────────────────────────────────────────────

        return Response({
            'message': 'Successfully checked out',
            'attendance': AttendanceSerializer(attendance).data
        }, status=status.HTTP_200_OK)

    def _sync_break_total(self, attendance):
        total = attendance.breaks.filter(break_end__isnull=False).aggregate(
            total=Sum('duration_minutes')
        )['total'] or 0
        attendance.total_break_minutes = total
        attendance.save(update_fields=['total_break_minutes', 'updated_at'])
        return total

    @action(detail=False, methods=['post'], url_path='breaks/start')
    def start_break(self, request):
        user = request.user
        today = timezone.now().date()
        admin_owner = _get_admin_owner(user)

        try:
            attendance = Attendance.objects.get(user=user, date=today, admin_owner=admin_owner)
        except Attendance.DoesNotExist:
            return Response({'error': 'You must check in before starting a break.'}, status=status.HTTP_400_BAD_REQUEST)

        if not attendance.check_in_time:
            return Response({'error': 'You must check in before starting a break.'}, status=status.HTTP_400_BAD_REQUEST)
        if attendance.check_out_time:
            return Response({'error': 'You cannot start a break after check-out.'}, status=status.HTTP_400_BAD_REQUEST)
        if BreakRecord.objects.filter(user=user, attendance=attendance, break_end__isnull=True).exists():
            return Response({'error': 'You already have an active break.'}, status=status.HTTP_400_BAD_REQUEST)

        break_record = BreakRecord.objects.create(
            user=user,
            attendance=attendance,
            admin_owner=admin_owner,
            break_start=timezone.now(),
        )

        return Response({
            'message': 'Break started successfully',
            'break': BreakRecordSerializer(break_record).data,
            'attendance': AttendanceSerializer(attendance).data,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='breaks/end')
    def end_break(self, request):
        user = request.user
        today = timezone.now().date()
        admin_owner = _get_admin_owner(user)

        try:
            attendance = Attendance.objects.get(user=user, date=today, admin_owner=admin_owner)
        except Attendance.DoesNotExist:
            return Response({'error': 'No check-in record found for today.'}, status=status.HTTP_400_BAD_REQUEST)

        break_record = BreakRecord.objects.filter(
            user=user,
            attendance=attendance,
            break_end__isnull=True,
        ).order_by('-break_start').first()
        if not break_record:
            return Response({'error': 'No active break found.'}, status=status.HTTP_400_BAD_REQUEST)

        break_record.break_end = timezone.now()
        break_record.save()
        total_break_minutes = self._sync_break_total(attendance)

        return Response({
            'message': 'Break ended successfully',
            'break': BreakRecordSerializer(break_record).data,
            'total_break_minutes': total_break_minutes,
            'attendance': AttendanceSerializer(attendance).data,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='breaks/today')
    def today_breaks(self, request):
        user = request.user
        today = timezone.now().date()
        admin_owner = _get_admin_owner(user)

        attendance = Attendance.objects.filter(user=user, date=today, admin_owner=admin_owner).first()
        breaks = BreakRecord.objects.none()
        if attendance:
            breaks = attendance.breaks.select_related('user', 'attendance')

        active_break = breaks.filter(break_end__isnull=True).order_by('-break_start').first()
        total_break_minutes = attendance.total_break_minutes if attendance else 0

        return Response({
            'has_active_break': bool(active_break),
            'active_break': BreakRecordSerializer(active_break).data if active_break else None,
            'total_break_minutes': total_break_minutes,
            'breaks': BreakRecordSerializer(breaks, many=True).data,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='breaks/list')
    def breaks_list(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can view break records.'}, status=status.HTTP_403_FORBIDDEN)

        admin_owner = _get_admin_owner(request.user)
        selected_date = request.query_params.get('date')
        user_id = request.query_params.get('user_id')

        breaks = BreakRecord.objects.select_related('user', 'attendance').filter(admin_owner=admin_owner)
        if selected_date:
            breaks = breaks.filter(attendance__date=selected_date)
        if user_id:
            breaks = breaks.filter(user_id=user_id)

        return Response(BreakRecordSerializer(breaks.order_by('-break_start'), many=True).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='today')
    def today_status(self, request):
        user = request.user
        today = timezone.now().date()
        admin_owner = _get_admin_owner(user)

        try:
            attendance = Attendance.objects.get(user=user, date=today, admin_owner=admin_owner)
            data = {
                'has_checked_in': bool(attendance.check_in_time),
                'has_checked_out': bool(attendance.check_out_time),
                'check_in_time': attendance.check_in_time,
                'check_out_time': attendance.check_out_time,
                'total_hours': attendance.total_hours,
                'status': attendance.status,
                'date': attendance.date,
                'late_request': attendance.late_request,
                'late_request_status': attendance.late_request_status,
                'check_in_latitude': attendance.check_in_latitude,
                'check_in_longitude': attendance.check_in_longitude,
                'check_in_address': attendance.check_in_address,
                'check_out_latitude': attendance.check_out_latitude,
                'check_out_longitude': attendance.check_out_longitude,
                'check_out_address': attendance.check_out_address,
                'total_break_minutes': attendance.total_break_minutes,
                'breaks': BreakRecordSerializer(attendance.breaks.all(), many=True).data,
                'has_active_break': attendance.breaks.filter(break_end__isnull=True).exists(),
            }
        except Attendance.DoesNotExist:
            data = {
                'has_checked_in': False,
                'has_checked_out': False,
                'check_in_time': None,
                'check_out_time': None,
                'total_hours': 0,
                'status': 'absent',
                'date': today,
                'late_request': False,
                'late_request_status': None,
                'check_in_latitude': None,
                'check_in_longitude': None,
                'check_in_address': None,
                'check_out_latitude': None,
                'check_out_longitude': None,
                'check_out_address': None,
                'total_break_minutes': 0,
                'breaks': [],
                'has_active_break': False,
            }

        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='request-late')
    def request_late(self, request):
        serializer = LateRequestSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        admin_owner = _get_admin_owner(user)
        request_date = serializer.validated_data.get('request_date')
        reason = serializer.validated_data.get('reason')

        attendance, created = Attendance.objects.get_or_create(
            user=user,
            date=request_date,
            admin_owner=admin_owner,
            defaults={'status': 'absent'}
        )
        attendance.late_request = True
        attendance.late_request_reason = reason
        attendance.late_request_status = 'pending'
        attendance.save()

        return Response({
            'message': 'Late request submitted successfully',
            'attendance': AttendanceSerializer(attendance).data
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='approve-late')
    def approve_late(self, request, pk=None):
        serializer = LateApprovalSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        attendance = self.get_object()
        action_type = serializer.validated_data.get('action')

        if not attendance.late_request:
            return Response({'error': 'No late request found for this attendance record'}, status=status.HTTP_400_BAD_REQUEST)

        if action_type == 'approve':
            attendance.late_request_status = 'approved'
            attendance.late_approved_by = request.user
            attendance.late_approved_at = timezone.now()
            attendance.save()
            message = 'Late request approved successfully'
        else:
            attendance.late_request_status = 'rejected'
            attendance.save()
            message = 'Late request rejected'

        return Response({'message': message, 'attendance': AttendanceSerializer(attendance).data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='pending-late-requests')
    def pending_late_requests(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can view pending late requests'}, status=status.HTTP_403_FORBIDDEN)

        admin_owner = _get_admin_owner(request.user)
        pending_requests = Attendance.objects.filter(
            admin_owner=admin_owner,
            late_request=True,
            late_request_status='pending'
        ).order_by('-date')

        return Response(AttendanceSerializer(pending_requests, many=True).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='monthly-stats')
    def monthly_stats(self, request):
        user = request.user
        year = int(request.query_params.get('year', timezone.now().year))
        month = int(request.query_params.get('month', timezone.now().month))

        first_day = datetime(year, month, 1).date()
        last_day = datetime(year, month, monthrange(year, month)[1]).date()

        admin_owner = _get_admin_owner(user)
        attendances = Attendance.objects.filter(
            user=user,
            admin_owner=admin_owner,
            date__gte=first_day,
            date__lte=last_day,
        )

        present_count = attendances.filter(status='present').count()
        absent_count = attendances.filter(status='absent').count()
        late_count = attendances.filter(status='late').count()
        half_day_count = attendances.filter(status='half_day').count()
        leave_count = attendances.filter(status='leave').count()

        total_hours = attendances.aggregate(Sum('total_hours'))['total_hours__sum'] or 0
        avg_hours = attendances.aggregate(Avg('total_hours'))['total_hours__avg'] or 0

        total_days = attendances.count()
        if total_days == 0:
            admin_owner = _get_admin_owner(user)
            sunday_working = _is_sunday_working(admin_owner)
            current_date = first_day
            total_days = 0
            while current_date <= last_day:
                if _is_working_day(current_date, sunday_working):
                    total_days += 1
                current_date += timedelta(days=1)

        data = {
            'present': present_count,
            'absent': absent_count,
            'late': late_count,
            'half_day': half_day_count,
            'leave': leave_count,
            'total_days': total_days,
            'total_hours': round(total_hours, 2),
            'average_hours': round(avg_hours, 2),
        }

        serializer = MonthlyStatsSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='history')
    def attendance_history(self, request):
        user = request.user
        admin_owner = _get_admin_owner(user)
        days = int(request.query_params.get('days', 30))
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days)

        attendances = Attendance.objects.filter(
            user=user,
            admin_owner=admin_owner,
            date__gte=start_date,
            date__lte=end_date,
        ).order_by('-date')

        return Response(AttendanceSerializer(attendances, many=True).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='employees-with-attendance')
    def employees_with_attendance(self, request):
        """
        Returns all active employees for this tenant.  Admin only.
        """
        user = request.user
        if not _is_admin(user):
            return Response({'error': 'Only admins can access this.'}, status=status.HTTP_403_FORBIDDEN)

        from employee_management.models import Employee

        admin_owner = _get_admin_owner(user)

        employees = Employee.objects.filter(
            admin_owner=admin_owner,
            status__in=['active']
        ).select_related('department').order_by('first_name', 'last_name')

        data = [
            {
                'id': emp.id,
                'employee_id': emp.employee_id,
                'first_name': emp.first_name,
                'last_name': emp.last_name,
                'email': emp.email,
                'position': emp.position,
                'department': emp.department.name if emp.department else '',
                'duty_start_time': str(emp.duty_start_time) if emp.duty_start_time else None,
                'duty_end_time': str(emp.duty_end_time) if emp.duty_end_time else None,
            }
            for emp in employees
        ]
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='avg-attendance-stats')
    def avg_attendance_stats(self, request):
        """
        Admin endpoint: average attendance percentage across ALL users in this
        tenant for the current month (or ?year=&month= params).
        """
        user = request.user
        if not _is_admin(user):
            return Response({'error': 'Only admins can access this.'}, status=status.HTTP_403_FORBIDDEN)

        today = timezone.now().date()
        year = int(request.query_params.get('year', today.year))
        month = int(request.query_params.get('month', today.month))

        admin_owner = _get_admin_owner(user)
        first_day = today.replace(year=year, month=month, day=1)
        last_day_of_month = today.replace(year=year, month=month, day=monthrange(year, month)[1])
        count_until = min(today, last_day_of_month)

        working_days = sum(
            1 for d in range((count_until - first_day).days + 1)
            if _is_working_day(first_day + timedelta(days=d), _is_sunday_working(admin_owner))
        )

        if working_days == 0:
            return Response({
                'year': year, 'month': month,
                'total_working_days': 0,
                'total_users': 0,
                'avg_attendance_percentage': 0.0,
            })

        records = Attendance.objects.filter(
            admin_owner=admin_owner,
            date__gte=first_day,
            date__lte=count_until,
        )

        User = get_user_model()
        all_user_ids = list(
            User.objects.filter(is_active=True, admin_owner=admin_owner)
            .values_list('id', flat=True)
        )
        total_users = len(all_user_ids)

        if total_users == 0:
            return Response({
                'year': year, 'month': month,
                'total_working_days': working_days,
                'total_users': 0,
                'avg_attendance_percentage': 0.0,
            })

        user_present_counts = (
            records
            .filter(status__in=['present', 'late'])
            .values('user_id')
            .annotate(present_days=Count('id'))
        )
        present_map = {row['user_id']: row['present_days'] for row in user_present_counts}

        total_pct = sum(
            min(present_map.get(uid, 0) / working_days * 100, 100)
            for uid in all_user_ids
        )
        avg_pct = round(total_pct / total_users, 1)

        return Response({
            'year': year,
            'month': month,
            'total_working_days': working_days,
            'total_users': total_users,
            'avg_attendance_percentage': avg_pct,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='attendance-trend')
    def attendance_trend(self, request):
        """
        Admin endpoint: daily attendance percentage for the last N days
        (default 14) scoped to this tenant.
        """
        user = request.user
        if not _is_admin(user):
            return Response({'error': 'Only admins can access this.'}, status=status.HTTP_403_FORBIDDEN)

        admin_owner = _get_admin_owner(user)
        days = min(int(request.query_params.get('days', 14)), 60)
        today = timezone.now().date()
        start_date = today - timedelta(days=days - 1)

        records = Attendance.objects.filter(
            admin_owner=admin_owner,
            date__gte=start_date,
            date__lte=today,
        ).values('date', 'status')

        from collections import defaultdict
        day_map = defaultdict(lambda: {'present': 0, 'total': 0})
        for rec in records:
            d = rec['date']
            day_map[d]['total'] += 1
            if rec['status'] in ('present', 'late'):
                day_map[d]['present'] += 1

        result = []
        for i in range(days):
            d = start_date + timedelta(days=i)
            entry = day_map.get(d, {'present': 0, 'total': 0})
            pct = round(entry['present'] / entry['total'] * 100, 1) if entry['total'] > 0 else 0
            result.append({
                'day': str(d.day),
                'date': str(d),
                'pct': pct,
                'present': entry['present'],
                'total': entry['total'],
            })

        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='today-summary')
    def today_summary(self, request):
        """
        Admin: returns today's attendance counts across all employees.
        GET /api/attendance/today-summary/
        """
        if not _is_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        today = timezone.now().date()
        admin_owner = _get_admin_owner(request.user)

        qs = Attendance.objects.filter(
            admin_owner=admin_owner,
            date=today,
        )

        data = {
            'date': str(today),
            'present':  qs.filter(status='present').count(),
            'absent':   qs.filter(status='absent').count(),
            'late':     qs.filter(status='late').count(),
            'half_day': qs.filter(status='half_day').count(),
            'leave':    qs.filter(status='leave').count(),
            'total':    qs.count(),
        }

        return Response(data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='total-requests')
    def total_requests(self, request):
        """
        Admin: returns total and pending count of all request types.
        GET /api/attendance/total-requests/
        """
        if not _is_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        admin_owner = _get_admin_owner(request.user)

        leave_qs           = LeaveRequest.objects.filter(admin_owner=admin_owner)
        late_arrival_qs    = LateArrivalRequest.objects.filter(admin_owner=admin_owner)
        early_departure_qs = EarlyDepartureRequest.objects.filter(admin_owner=admin_owner)
        wfh_qs             = WFHRequest.objects.filter(admin_owner=admin_owner)
        salary_advance_qs  = SalaryAdvanceRequest.objects.filter(admin_owner=admin_owner)

        return Response({
            'leave_requests':            {'total': leave_qs.count(),           'pending': leave_qs.filter(status='pending').count()},
            'late_arrival_requests':     {'total': late_arrival_qs.count(),    'pending': late_arrival_qs.filter(status='pending').count()},
            'early_departure_requests':  {'total': early_departure_qs.count(), 'pending': early_departure_qs.filter(status='pending').count()},
            'wfh_requests':              {'total': wfh_qs.count(),             'pending': wfh_qs.filter(status='pending').count()},
            'salary_advance_requests':   {'total': salary_advance_qs.count(),  'pending': salary_advance_qs.filter(status='pending').count()},
            'overall': {
                'total':   leave_qs.count() + late_arrival_qs.count() + early_departure_qs.count() + wfh_qs.count() + salary_advance_qs.count(),
                'pending': leave_qs.filter(status='pending').count() + late_arrival_qs.filter(status='pending').count() + early_departure_qs.filter(status='pending').count() + wfh_qs.filter(status='pending').count() + salary_advance_qs.filter(status='pending').count(),
            }
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='total-requests')
    def total_requests(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        admin_owner = _get_admin_owner(request.user)
        leave_qs            = LeaveRequest.objects.filter(admin_owner=admin_owner)
        late_arrival_qs     = LateArrivalRequest.objects.filter(admin_owner=admin_owner)
        early_departure_qs  = EarlyDepartureRequest.objects.filter(admin_owner=admin_owner)
        wfh_qs              = WFHRequest.objects.filter(admin_owner=admin_owner)
        salary_advance_qs   = SalaryAdvanceRequest.objects.filter(admin_owner=admin_owner)
        return Response({
            'leave_requests':           {'total': leave_qs.count(),           'pending': leave_qs.filter(status='pending').count()},
            'late_arrival_requests':    {'total': late_arrival_qs.count(),    'pending': late_arrival_qs.filter(status='pending').count()},
            'early_departure_requests': {'total': early_departure_qs.count(), 'pending': early_departure_qs.filter(status='pending').count()},
            'wfh_requests':             {'total': wfh_qs.count(),             'pending': wfh_qs.filter(status='pending').count()},
            'salary_advance_requests':  {'total': salary_advance_qs.count(),  'pending': salary_advance_qs.filter(status='pending').count()},
            'overall': {
                'total':   leave_qs.count() + late_arrival_qs.count() + early_departure_qs.count() + wfh_qs.count() + salary_advance_qs.count(),
                'pending': leave_qs.filter(status='pending').count() + late_arrival_qs.filter(status='pending').count() + early_departure_qs.filter(status='pending').count() + wfh_qs.filter(status='pending').count() + salary_advance_qs.filter(status='pending').count(),
            }
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='attendance-percentage')
    def attendance_percentage(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        admin_owner = _get_admin_owner(request.user)
        year  = int(request.query_params.get('year',  timezone.now().year))
        month = int(request.query_params.get('month', timezone.now().month))
        first_day = datetime(year, month, 1).date()
        last_day  = datetime(year, month, monthrange(year, month)[1]).date()
        today     = timezone.now().date()
        effective_end = min(last_day, today)
        sunday_working = _is_sunday_working(admin_owner)
        working_days = 0
        current = first_day
        while current <= effective_end:
            if _is_working_day(current, sunday_working):
                working_days += 1
            current += timedelta(days=1)
        qs = Attendance.objects.filter(admin_owner=admin_owner, date__gte=first_day, date__lte=effective_end)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        total_employees = User.objects.filter(admin_owner=admin_owner, is_active=True).count()
        expected = total_employees * working_days
        present_count = qs.filter(status__in=['present', 'late', 'half_day']).count()
        percentage = round((present_count / expected) * 100, 2) if expected > 0 else 0.0
        return Response({
            'year':                  year,
            'month':                 month,
            'total_employees':       total_employees,
            'working_days':          working_days,
            'expected':              expected,
            'present':               present_count,
            'absent':                qs.filter(status='absent').count(),
            'leave':                 qs.filter(status='leave').count(),
            'attendance_percentage': percentage,
        }, status=status.HTTP_200_OK)
    # ─────────────────────────────────────────────────────────────────────────────
# LATE ARRIVAL REQUEST VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class LateArrivalRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for LateArrivalRequest – tenant-isolated.

    Users:
      POST   /late-arrival-requests/              – create a request
      GET    /late-arrival-requests/my-requests/  – list own requests
      DELETE /late-arrival-requests/{id}/         – cancel own pending request

    Admins:
      GET    /late-arrival-requests/              – list all (filter ?status=pending)
      GET    /late-arrival-requests/pending/      – pending only
      GET    /late-arrival-requests/stats/        – stats
      POST   /late-arrival-requests/{id}/review/  – approve or reject
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return CreateLateArrivalRequestSerializer
        if self.action == 'review':
            return LateArrivalApprovalSerializer
        return LateArrivalRequestSerializer

    def get_queryset(self):
        user = self.request.user
        admin_owner = _get_admin_owner(user)
        qs = LateArrivalRequest.objects.filter(
            admin_owner=admin_owner
        ).select_related('user', 'reviewed_by')

        if _is_admin(user):
            status_filter = self.request.query_params.get('status')
            user_filter   = self.request.query_params.get('user_id')
            date_filter   = self.request.query_params.get('date')    # YYYY-MM-DD
            year_filter   = self.request.query_params.get('year')
            month_filter  = self.request.query_params.get('month')

            if status_filter:
                qs = qs.filter(status=status_filter)
            if user_filter:
                qs = qs.filter(user_id=user_filter)
            if date_filter:
                qs = qs.filter(date=date_filter)
            elif year_filter and month_filter:
                qs = qs.filter(date__year=int(year_filter), date__month=int(month_filter))
            elif year_filter:
                qs = qs.filter(date__year=int(year_filter))
            return qs

        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        serializer = CreateLateArrivalRequestSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(user=request.user, admin_owner=_get_admin_owner(request.user))

        # ── WhatsApp: late_request notification ───────────────────────────────
        _wa_notify(
            admin_owner   = _get_admin_owner(request.user),
            purpose_key   = 'late_request',
            employee_user = request.user,
            context       = {
                'name':          _get_full_name(request.user),
                'expected_time': str(instance.expected_arrival_time),
                'date':          str(instance.date),
                'reason':        instance.reason or '',
            },
        )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=request.user,
            action_type='CREATE',
            module='Attendance',
            description=f"Submitted late arrival request for {instance.date}",
            request=request,
        )

        return Response(
            LateArrivalRequestSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if _is_admin(request.user):
            # Admin: hard-delete any request regardless of status
            instance.delete()
            log_activity(
                user=request.user,
                action_type='DELETE',
                module='Attendance',
                description=f"Admin deleted late arrival request for {instance.date}",
                request=request,
            )
            return Response({'message': 'Late arrival request deleted.'}, status=status.HTTP_200_OK)
        if instance.user != request.user:
            return Response({'error': 'You cannot cancel this request.'}, status=status.HTTP_403_FORBIDDEN)
        if instance.status != 'pending':
            return Response({'error': 'Only pending requests can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"Cancelled late arrival request for {instance.date}",
            request=request,
        )
        return Response({'message': 'Late arrival request cancelled.'}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='my-requests')
    def my_requests(self, request):
        admin_owner = _get_admin_owner(request.user)
        qs = LateArrivalRequest.objects.filter(
            user=request.user, admin_owner=admin_owner
        ).order_by('-created_at')
        return Response(LateArrivalRequestSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='pending')
    def pending_requests(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can view all pending requests.'}, status=status.HTTP_403_FORBIDDEN)
        admin_owner = _get_admin_owner(request.user)
        qs = LateArrivalRequest.objects.filter(
            admin_owner=admin_owner, status='pending'
        ).select_related('user').order_by('-created_at')
        return Response(LateArrivalRequestSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)
        admin_owner = _get_admin_owner(request.user)
        qs = LateArrivalRequest.objects.filter(admin_owner=admin_owner)
        return Response({
            'total':     qs.count(),
            'pending':   qs.filter(status='pending').count(),
            'approved':  qs.filter(status='approved').count(),
            'rejected':  qs.filter(status='rejected').count(),
            'cancelled': qs.filter(status='cancelled').count(),
        })

    @action(detail=True, methods=['post'], url_path='review')
    def review(self, request, pk=None):
        """Admin approves or rejects a late arrival request."""
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can review late arrival requests.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = LateArrivalApprovalSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        late_req = self.get_object()

        action_type = serializer.validated_data['action']

        # Allow waiving an already-approved request; block everything else on non-pending
        if late_req.status == 'pending':
            pass  # all actions allowed
        elif late_req.status == 'approved' and action_type == 'waive':
            pass  # waiving an approved request is explicitly allowed
        else:
            return Response(
                {'error': f'Cannot review a request that is already "{late_req.status}".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        admin_notes = serializer.validated_data.get('admin_notes', '')
        admin_owner = _get_admin_owner(request.user)

        if action_type == 'approve':
            late_req.status = 'approved'
            message = 'Late arrival request approved successfully.'

            attendance, _ = Attendance.objects.get_or_create(
                user=late_req.user,
                date=late_req.date,
                admin_owner=admin_owner,
                defaults={
                    'notes': f'Late arrival approved – {late_req.reason}',
                    'is_verified': True,
                    'verified_by': request.user,
                    'verified_at': timezone.now(),
                    'status': 'late',
                },
            )
            if not attendance.is_verified:
                attendance.notes = f'Late arrival approved – {late_req.reason}'
                attendance.is_verified = True
                attendance.verified_by = request.user
                attendance.verified_at = timezone.now()
                attendance.status = 'late'
                attendance.save(update_fields=[
                    'notes', 'is_verified', 'verified_by', 'verified_at', 'status', 'updated_at',
                ])
        elif action_type == 'waive':
            late_req.status = 'waived'
            message = 'Late arrival request waived.'

            attendance, _ = Attendance.objects.get_or_create(
                user=late_req.user,
                date=late_req.date,
                admin_owner=admin_owner,
                defaults={
                    'notes': f'Late arrival waived – {late_req.reason}',
                    'is_verified': True,
                    'verified_by': request.user,
                    'verified_at': timezone.now(),
                    'status': 'late',
                },
            )
            if not attendance.is_verified:
                attendance.notes = f'Late arrival waived – {late_req.reason}'
                attendance.is_verified = True
                attendance.verified_by = request.user
                attendance.verified_at = timezone.now()
                attendance.status = 'late'
                attendance.save(update_fields=[
                    'notes', 'is_verified', 'verified_by', 'verified_at', 'status', 'updated_at',
                ])
        else:
            late_req.status = 'rejected'
            message = 'Late arrival request rejected.'

        late_req.reviewed_by = request.user
        late_req.reviewed_at = timezone.now()
        late_req.admin_notes = admin_notes
        late_req.save()

        # ── WhatsApp: late_approved / late_rejected notification ──────────────
        if action_type in ('approve', 'waive'):
            _wa_notify(
                admin_owner   = admin_owner,
                purpose_key   = 'late_approved',
                employee_user = late_req.user,
                context       = {
                    'name': _get_full_name(late_req.user),
                    'date': str(late_req.date),
                },
            )
        elif action_type == 'reject':
            _wa_notify(
                admin_owner   = admin_owner,
                purpose_key   = 'late_rejected',
                employee_user = late_req.user,
                context       = {
                    'name':        _get_full_name(late_req.user),
                    'date':        str(late_req.date),
                    'admin_notes': admin_notes or 'No notes provided.',
                },
            )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"{action_type.capitalize()}d late arrival request for {late_req.user.username} on {late_req.date}",
            request=request,
        )

        return Response({
            'message': message,
            'late_arrival_request': LateArrivalRequestSerializer(late_req).data,
        }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
# LEAVE REQUEST VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class LeaveRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing leave requests – tenant-isolated.
    Users can create/view their own requests.
    Admins can view all requests and approve/reject them.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return CreateLeaveRequestSerializer
        if self.action in ['review_leave']:
            return LeaveApprovalSerializer
        return LeaveRequestSerializer

    def get_queryset(self):
        user = self.request.user
        admin_owner = _get_admin_owner(user)
        qs = LeaveRequest.objects.filter(
            admin_owner=admin_owner
        ).select_related('user', 'reviewed_by')

        if _is_admin(user):
            status_filter = self.request.query_params.get('status')
            user_filter   = self.request.query_params.get('user_id')
            date_filter   = self.request.query_params.get('date')    # YYYY-MM-DD — matches start_date
            year_filter   = self.request.query_params.get('year')
            month_filter  = self.request.query_params.get('month')

            if status_filter:
                qs = qs.filter(status=status_filter)
            if user_filter:
                qs = qs.filter(user_id=user_filter)
            if date_filter:
                # Return leaves that overlap the given date
                qs = qs.filter(start_date__lte=date_filter, end_date__gte=date_filter)
            elif year_filter and month_filter:
                from datetime import date as _date
                import calendar as _cal
                y, m = int(year_filter), int(month_filter)
                first = _date(y, m, 1)
                last  = _date(y, m, _cal.monthrange(y, m)[1])
                qs = qs.filter(start_date__lte=last, end_date__gte=first)
            elif year_filter:
                qs = qs.filter(start_date__year=int(year_filter))
            return qs

        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        serializer = CreateLeaveRequestSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        leave_request = serializer.save(user=request.user, admin_owner=_get_admin_owner(request.user))

        # ── WhatsApp: leave_request notification ──────────────────────────────
        _wa_notify(
            admin_owner   = _get_admin_owner(request.user),
            purpose_key   = 'leave_request',
            employee_user = request.user,
            context       = {
                'name':       _get_full_name(request.user),
                'leave_type': (
                    leave_request.leave_type_obj.name
                    if leave_request.leave_type_obj
                    else leave_request.get_leave_type_display()
                ),
                'start_date': str(leave_request.start_date),
                'end_date':   str(leave_request.end_date),
                'reason':     leave_request.reason or '',
            },
        )
        # ─────────────────────────────────────────────────────────────────────

        leave_label = (
            leave_request.leave_type_obj.name
            if leave_request.leave_type_obj
            else leave_request.get_leave_type_display()
        )
        log_activity(
            user=request.user,
            action_type='CREATE',
            module='Attendance',
            description=f"Submitted leave request ({leave_label}) from {leave_request.start_date} to {leave_request.end_date}",
            request=request,
        )

        return Response(
            LeaveRequestSerializer(leave_request).data,
            status=status.HTTP_201_CREATED
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if _is_admin(request.user):
            # Admin: hard-delete any request regardless of status
            instance.delete()
            log_activity(
                user=request.user,
                action_type='DELETE',
                module='Attendance',
                description=f"Admin deleted leave request from {instance.start_date} to {instance.end_date}",
                request=request,
            )
            return Response({'message': 'Leave request deleted.'}, status=status.HTTP_200_OK)
        if instance.user != request.user:
            return Response({'error': 'You cannot cancel this leave request.'}, status=status.HTTP_403_FORBIDDEN)
        if instance.status != 'pending':
            return Response({'error': 'Only pending leave requests can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        leave_label = (
            instance.leave_type_obj.name if instance.leave_type_obj
            else instance.get_leave_type_display()
        )
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"Cancelled leave request ({leave_label}) from {instance.start_date} to {instance.end_date}",
            request=request,
        )
        return Response({'message': 'Leave request cancelled successfully.'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='review')
    def review_leave(self, request, pk=None):
        """Admin approves or rejects a leave request."""
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can review leave requests.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = LeaveApprovalSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        leave_request = self.get_object()

        action_type = serializer.validated_data['action']

        # Allow waiving an already-approved request; block everything else on non-pending
        if leave_request.status == 'pending':
            pass  # all actions allowed
        elif leave_request.status == 'approved' and action_type == 'waive':
            pass  # waiving an approved leave is explicitly allowed
        else:
            return Response(
                {'error': f'Cannot review a leave request that is already "{leave_request.status}".'},
                status=status.HTTP_400_BAD_REQUEST
            )

        admin_notes = serializer.validated_data.get('admin_notes', '')
        admin_owner = _get_admin_owner(request.user)
        sunday_working = _is_sunday_working(admin_owner)

        # ── Determine if this leave type is unpaid (triggers salary deduction) ─
        is_unpaid = False
        if leave_request.leave_type_obj:
            is_unpaid = leave_request.leave_type_obj.payment_status == 'unpaid'
        else:
            # Fall back to the legacy enum — 'unpaid' enum value means unpaid
            is_unpaid = leave_request.leave_type == 'unpaid'

        leave_label = (
            leave_request.leave_type_obj.name
            if leave_request.leave_type_obj
            else leave_request.get_leave_type_display()
        )

        def _apply_attendance_records(status_value, note_prefix):
            """Create/update attendance records for each working day in the leave range."""
            current_date = leave_request.start_date
            working_days = 0
            while current_date <= leave_request.end_date:
                if _is_working_day(current_date, sunday_working):
                    working_days += 1
                    attendance, created = Attendance.objects.get_or_create(
                        user=leave_request.user,
                        date=current_date,
                        admin_owner=admin_owner,
                        defaults={
                            'status': status_value,
                            'notes': f'{note_prefix}: {leave_label}',
                            'is_verified': True,
                            'verified_by': request.user,
                            'verified_at': timezone.now(),
                        }
                    )
                    if not created and not attendance.check_in_time:
                        attendance.status = status_value
                        attendance.notes = f'{note_prefix}: {leave_label}'
                        attendance.is_verified = True
                        attendance.verified_by = request.user
                        attendance.verified_at = timezone.now()
                        attendance.save(update_fields=[
                            'status', 'notes', 'is_verified',
                            'verified_by', 'verified_at', 'updated_at',
                        ])
                current_date += timedelta(days=1)
            return working_days

        def _apply_unpaid_deduction(working_days):
            """
            Create a Deduction record for unpaid leave days so it is
            picked up by payroll calculation.

            Per-day salary is computed using the SAME formula as payroll:
              - Standard mode : per_day = salary / (calendar_days − sunday_holidays)
              - Normalized mode: per_day = salary / normalizedMonthDays (default 30)

            One Deduction row is created per calendar month the leave spans
            (upsert so repeated approvals don't double-count).
            """
            from master.models import Deduction as MasterDeduction, PayrollPolicy as _PP
            from employee_management.models import Employee
            from decimal import Decimal, ROUND_HALF_UP
            import calendar as _cal
            from collections import defaultdict

            # ── Locate employee ───────────────────────────────────────────────
            employee = Employee.objects.filter(
                email=leave_request.user.email,
                admin_owner=admin_owner,
            ).first()
            if not employee or not employee.salary:
                return

            basic_salary = Decimal(str(employee.salary))

            # ── Payroll policy (normalized mode?) ─────────────────────────────
            policy_obj  = _PP.objects.filter(admin_owner=admin_owner).first()
            policy_data = policy_obj.policy_data if policy_obj else {}
            sal_calc    = policy_data.get('salaryCalculation', {})
            normalized_mode       = sal_calc.get('enabled', False)
            normalized_month_days = int(sal_calc.get('normalizedMonthDays', 30))

            # ── Count leave days per (year, month) ────────────────────────────
            days_per_month = defaultdict(int)
            cur = leave_request.start_date
            while cur <= leave_request.end_date:
                if _is_working_day(cur, sunday_working):
                    days_per_month[(cur.year, cur.month)] += 1
                cur += timedelta(days=1)

            # ── Build one Deduction row per affected month ────────────────────
            for (yr, mo), days in days_per_month.items():

                if normalized_mode:
                    # Every month treated as normalizedMonthDays (e.g. 30)
                    total_days_divisor = max(1, normalized_month_days)
                else:
                    # Standard: calendar days minus Sunday-holidays for that month
                    from payroll.views import _get_holiday_breakdown
                    cal_days = _cal.monthrange(yr, mo)[1]
                    hol_bk   = _get_holiday_breakdown(yr, mo, admin_owner)
                    total_days_divisor = max(1, cal_days - hol_bk['sunday_count'])

                per_day    = (basic_salary / Decimal(str(total_days_divisor))).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )

                # Half-day leave → deduct only half a day's salary
                is_half_day = leave_request.duration_type == 'half_day'
                day_deduct = (per_day * Decimal(str(days))).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
                if is_half_day:
                    day_deduct = (day_deduct / Decimal('2')).quantize(
                        Decimal('0.01'), rounding=ROUND_HALF_UP
                    )

                deduction_name = f'Unpaid Leave – {leave_label}'
                employee_name  = _get_full_name(leave_request.user) or leave_request.user.username
                duration_label = 'half-day' if is_half_day else 'full-day'
                description    = (
                    f'Auto-generated: {days} unpaid {duration_label} leave day(s) '
                    f'({"0.5" if is_half_day else "1"}/{total_days_divisor} working days × ₹{per_day}/day) '
                    f'approved for {employee_name}'
                )

                existing = MasterDeduction.objects.filter(
                    employee=employee,
                    deduction_name=deduction_name,
                    year=yr,
                    month=mo,
                    admin_owner=admin_owner,
                ).first()
                if existing:
                    existing.amount      = day_deduct
                    existing.description = description
                    existing.save(update_fields=['amount', 'description', 'updated_at'])
                else:
                    MasterDeduction.objects.create(
                        employee=employee,
                        deduction_name=deduction_name,
                        year=yr,
                        month=mo,
                        amount=day_deduct,
                        description=description,
                        is_active=True,
                        admin_owner=admin_owner,
                    )

        if action_type == 'approve':
            leave_request.status = 'approved'
            message = 'Leave request approved successfully.'

            if is_unpaid:
                # Unpaid leave → mark attendance as absent (full-day) or half_day.
                # Payroll already deducts salary based on absent_days/half_days count,
                # so NO separate Deduction record is created — that would double-deduct.
                if leave_request.duration_type == 'half_day':
                    _apply_attendance_records('half_day', 'Unpaid half-day leave')
                else:
                    _apply_attendance_records('absent', 'Unpaid leave')
                message = 'Unpaid leave approved. Attendance marked as absent.'
            else:
                # Paid leave → mark as leave (no salary hit)
                _apply_attendance_records('leave', 'Approved leave')

        elif action_type == 'waive':
            leave_request.status = 'waived'
            message = 'Leave request waived successfully.'
            _apply_attendance_records('leave', 'Waived leave')

        else:
            leave_request.status = 'rejected'
            message = 'Leave request rejected.'

        leave_request.reviewed_by = request.user
        leave_request.reviewed_at = timezone.now()
        leave_request.admin_notes = admin_notes
        leave_request.save()

        # ── WhatsApp: leave_approved / leave_rejected notification ────────────
        if action_type in ('approve', 'waive'):
            _wa_notify(
                admin_owner   = admin_owner,
                purpose_key   = 'leave_approved',
                employee_user = leave_request.user,
                context       = {
                    'name':       _get_full_name(leave_request.user),
                    'leave_type': leave_label,
                    'start_date': str(leave_request.start_date),
                    'end_date':   str(leave_request.end_date),
                },
            )
        elif action_type == 'reject':
            _wa_notify(
                admin_owner   = admin_owner,
                purpose_key   = 'leave_rejected',
                employee_user = leave_request.user,
                context       = {
                    'name':        _get_full_name(leave_request.user),
                    'leave_type':  leave_label,
                    'start_date':  str(leave_request.start_date),
                    'end_date':    str(leave_request.end_date),
                    'admin_notes': admin_notes or 'No notes provided.',
                },
            )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"{action_type.capitalize()}d leave request ({leave_label}) for {leave_request.user.username} from {leave_request.start_date} to {leave_request.end_date}",
            request=request,
        )

        return Response({
            'message': message,
            'leave_request': LeaveRequestSerializer(leave_request).data
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='pending')
    def pending_requests(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can view all pending requests.'}, status=status.HTTP_403_FORBIDDEN)
        admin_owner = _get_admin_owner(request.user)
        pending = LeaveRequest.objects.filter(
            admin_owner=admin_owner, status='pending'
        ).select_related('user').order_by('-created_at')
        return Response(LeaveRequestSerializer(pending, many=True).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='my-requests')
    def my_requests(self, request):
        admin_owner = _get_admin_owner(request.user)
        requests_qs = LeaveRequest.objects.filter(
            user=request.user, admin_owner=admin_owner
        ).order_by('-created_at')
        return Response(LeaveRequestSerializer(requests_qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='stats')
    def leave_stats(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)
        admin_owner = _get_admin_owner(request.user)
        qs = LeaveRequest.objects.filter(admin_owner=admin_owner)
        return Response({
            'total':    qs.count(),
            'pending':  qs.filter(status='pending').count(),
            'approved': qs.filter(status='approved').count(),
            'rejected': qs.filter(status='rejected').count(),
        }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
# ATTENDANCE SETTINGS VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class AttendanceSettingsViewSet(viewsets.ModelViewSet):
    serializer_class = AttendanceSettingsSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        admin_owner = _get_admin_owner(self.request.user)
        return AttendanceSettings.objects.filter(admin_owner=admin_owner)

    def get_permissions(self):
        # For all create/update/partial_update/destroy actions, check via _is_admin in the action itself
        return [permissions.IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can create attendance settings.'}, status=status.HTTP_403_FORBIDDEN)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can update attendance settings.'}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can update attendance settings.'}, status=status.HTTP_403_FORBIDDEN)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can delete attendance settings.'}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get', 'post'], url_path='current')
    def current_settings(self, request):
        admin_owner = _get_admin_owner(request.user)
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).first()
        if not settings_obj:
            settings_obj = AttendanceSettings.objects.create(admin_owner=admin_owner)

        if request.method == 'POST':
            if not _is_admin(request.user):
                return Response({'error': 'Only admins can update settings.'}, status=status.HTTP_403_FORBIDDEN)
            serializer = self.get_serializer(settings_obj, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            log_activity(
                user=request.user,
                action_type='UPDATE',
                module='Attendance',
                description='Updated Attendance Settings',
                request=request,
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        # GET
        serializer = self.get_serializer(settings_obj)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['patch'], url_path='update-current')
    def update_current(self, request):
        """
        Admin-only: PATCH /attendance/settings/update-current/
        Updates (or creates) the single AttendanceSettings record for this tenant.
        """
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can update attendance settings.'}, status=status.HTTP_403_FORBIDDEN)

        admin_owner = _get_admin_owner(request.user)
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).first()
        if not settings_obj:
            settings_obj = AttendanceSettings.objects.create(admin_owner=admin_owner)

        serializer = self.get_serializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description='Updated Attendance Settings',
            request=request,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
# EARLY DEPARTURE REQUEST VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class EarlyDepartureRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing early departure requests – tenant-isolated.

    Employees:
      POST   /attendance/early-departure-requests/              – submit a request
      GET    /attendance/early-departure-requests/my-requests/  – own history
      DELETE /attendance/early-departure-requests/{id}/         – cancel pending

    Admins:
      GET    /attendance/early-departure-requests/              – all requests
      GET    /attendance/early-departure-requests/pending/      – pending only
      POST   /attendance/early-departure-requests/{id}/review/  – approve/reject
      GET    /attendance/early-departure-requests/stats/        – counts by status
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return CreateEarlyDepartureRequestSerializer
        if self.action == 'review':
            return EarlyDepartureApprovalSerializer
        return EarlyDepartureRequestSerializer

    def get_queryset(self):
        user = self.request.user
        admin_owner = _get_admin_owner(user)
        qs = EarlyDepartureRequest.objects.filter(
            admin_owner=admin_owner
        ).select_related('user', 'reviewed_by')

        if _is_admin(user):
            status_filter = self.request.query_params.get('status')
            user_filter   = self.request.query_params.get('user_id')
            date_filter   = self.request.query_params.get('date')    # YYYY-MM-DD
            year_filter   = self.request.query_params.get('year')
            month_filter  = self.request.query_params.get('month')

            if status_filter:
                qs = qs.filter(status=status_filter)
            if user_filter:
                qs = qs.filter(user_id=user_filter)
            if date_filter:
                qs = qs.filter(date=date_filter)
            elif year_filter and month_filter:
                qs = qs.filter(date__year=int(year_filter), date__month=int(month_filter))
            elif year_filter:
                qs = qs.filter(date__year=int(year_filter))
            return qs

        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        serializer = CreateEarlyDepartureRequestSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(user=request.user, admin_owner=_get_admin_owner(request.user))

        # ── WhatsApp: early_departure_request notification ────────────────────
        _wa_notify(
            admin_owner   = _get_admin_owner(request.user),
            purpose_key   = 'early_departure_request',
            employee_user = request.user,
            context       = {
                'name':          _get_full_name(request.user),
                'expected_time': str(instance.expected_departure_time),
                'date':          str(instance.date),
                'reason':        instance.reason or '',
            },
        )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=request.user,
            action_type='CREATE',
            module='Attendance',
            description=f"Submitted early departure request for {instance.date}",
            request=request,
        )

        return Response(
            EarlyDepartureRequestSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if _is_admin(request.user):
            # Admin: hard-delete any request regardless of status
            instance.delete()
            log_activity(
                user=request.user,
                action_type='DELETE',
                module='Attendance',
                description=f"Admin deleted early departure request for {instance.date}",
                request=request,
            )
            return Response({'message': 'Early departure request deleted.'}, status=status.HTTP_200_OK)
        if instance.user != request.user:
            return Response({'error': 'You cannot cancel this request.'}, status=status.HTTP_403_FORBIDDEN)
        if instance.status != 'pending':
            return Response({'error': 'Only pending requests can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"Cancelled early departure request for {instance.date}",
            request=request,
        )
        return Response({'message': 'Early departure request cancelled.'}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='my-requests')
    def my_requests(self, request):
        admin_owner = _get_admin_owner(request.user)
        qs = EarlyDepartureRequest.objects.filter(
            user=request.user, admin_owner=admin_owner
        ).order_by('-created_at')
        return Response(EarlyDepartureRequestSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='pending')
    def pending_requests(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can view all pending requests.'}, status=status.HTTP_403_FORBIDDEN)
        admin_owner = _get_admin_owner(request.user)
        qs = EarlyDepartureRequest.objects.filter(
            admin_owner=admin_owner, status='pending'
        ).select_related('user').order_by('-created_at')
        return Response(EarlyDepartureRequestSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)
        admin_owner = _get_admin_owner(request.user)
        qs = EarlyDepartureRequest.objects.filter(admin_owner=admin_owner)
        return Response({
            'total':     qs.count(),
            'pending':   qs.filter(status='pending').count(),
            'approved':  qs.filter(status='approved').count(),
            'rejected':  qs.filter(status='rejected').count(),
            'cancelled': qs.filter(status='cancelled').count(),
        })

    @action(detail=True, methods=['post'], url_path='review')
    def review(self, request, pk=None):
        """
        Admin approves or rejects an early departure request.
        On approval the corresponding Attendance record is marked 'half_day' and verified.

        Body: { "action": "approve"|"reject", "admin_notes": "..." }
        """
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can review early departure requests.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = EarlyDepartureApprovalSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        early_req = self.get_object()

        action_type = serializer.validated_data['action']

        # Allow waiving an already-approved request; block everything else on non-pending
        if early_req.status == 'pending':
            pass  # all actions allowed
        elif early_req.status == 'approved' and action_type == 'waive':
            pass  # waiving an approved request is explicitly allowed
        else:
            return Response(
                {'error': f'Cannot review a request that is already "{early_req.status}".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        admin_notes = serializer.validated_data.get('admin_notes', '')
        admin_owner = _get_admin_owner(request.user)

        if action_type == 'approve':
            early_req.status = 'approved'
            message = 'Early departure request approved successfully.'

            attendance, created = Attendance.objects.get_or_create(
                user=early_req.user,
                date=early_req.date,
                admin_owner=admin_owner,
                defaults={
                    'notes': f'Early departure approved – {early_req.reason}',
                    'is_verified': True,
                    'verified_by': request.user,
                    'verified_at': timezone.now(),
                    'status': 'present',
                },
            )
            if not created and not attendance.is_verified:
                attendance.notes = f'Early departure approved – {early_req.reason}'
                attendance.is_verified = True
                attendance.verified_by = request.user
                attendance.verified_at = timezone.now()
                attendance.status = 'present'
                attendance.save(update_fields=[
                    'notes', 'is_verified', 'verified_by', 'verified_at', 'status', 'updated_at',
                ])
        elif action_type == 'waive':
            early_req.status = 'waived'
            message = 'Early departure request waived successfully.'

            attendance, created = Attendance.objects.get_or_create(
                user=early_req.user,
                date=early_req.date,
                admin_owner=admin_owner,
                defaults={
                    'notes': f'Early departure waived – {early_req.reason}',
                    'is_verified': True,
                    'verified_by': request.user,
                    'verified_at': timezone.now(),
                    'status': 'present',
                },
            )
            if not created and not attendance.is_verified:
                attendance.notes = f'Early departure waived – {early_req.reason}'
                attendance.is_verified = True
                attendance.verified_by = request.user
                attendance.verified_at = timezone.now()
                attendance.status = 'present'
                attendance.save(update_fields=[
                    'notes', 'is_verified', 'verified_by', 'verified_at', 'status', 'updated_at',
                ])
        else:
            early_req.status = 'rejected'
            message = 'Early departure request rejected.'

        early_req.reviewed_by = request.user
        early_req.reviewed_at = timezone.now()
        early_req.admin_notes = admin_notes
        early_req.save()

        # ── WhatsApp: early_departure_approved / rejected notification ────────
        if action_type in ('approve', 'waive'):
            _wa_notify(
                admin_owner   = admin_owner,
                purpose_key   = 'early_departure_approved',
                employee_user = early_req.user,
                context       = {
                    'name': _get_full_name(early_req.user),
                    'date': str(early_req.date),
                },
            )
        elif action_type == 'reject':
            _wa_notify(
                admin_owner   = admin_owner,
                purpose_key   = 'early_departure_rejected',
                employee_user = early_req.user,
                context       = {
                    'name':        _get_full_name(early_req.user),
                    'date':        str(early_req.date),
                    'admin_notes': admin_notes or 'No notes provided.',
                },
            )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"{action_type.capitalize()}d early departure request for {early_req.user.username} on {early_req.date}",
            request=request,
        )

        return Response({
            'message': message,
            'early_departure_request': EarlyDepartureRequestSerializer(early_req).data,
        }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
# FACE RECOGNITION VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class FaceRecognitionViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='face-status')
    def face_status(self, request):
        """
        Returns a mapping of user_id -> face_registered (bool) for all users
        belonging to this admin's tenant.
        GET /attendance/face/face-status/
        """
        if not _is_admin(request.user):
            return Response({'error': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)

        admin_owner = _get_admin_owner(request.user)
        registered_ids = set(
            EmployeeFaceData.objects.filter(
                admin_owner=admin_owner,
                reference_image__isnull=False,
            ).exclude(reference_image='').values_list('user_id', flat=True)
        )
        return Response({'face_registered_user_ids': list(registered_ids)})

    @action(detail=False, methods=['post'], url_path='register-face')
    def register_face(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can register face.'}, status=status.HTTP_403_FORBIDDEN)

        user_id   = request.data.get('user_id')
        image_data = request.FILES.get('image') or request.data.get('image')

        if not user_id or not image_data:
            return Response({'error': 'user_id and image are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target_user = get_user_model().objects.get(pk=user_id, admin_owner=_get_admin_owner(request.user))
        except get_user_model().DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        # ── Decode image bytes (base64 or uploaded file) ──────────────────────
        try:
            img_bytes = self._load_image_bytes(image_data)
        except Exception as e:
            return Response({'error': f'Invalid image data: {e}'}, status=status.HTTP_400_BAD_REQUEST)

        # ── Quality gate: ensure exactly ONE face is detectable before saving ─
        # This prevents registering blurry, multi-person, or obstructed photos
        # which are the #1 cause of downstream false positives.
        tmp_path = self._write_temp(img_bytes, suffix='.jpg')
        try:
            faces = DeepFace.extract_faces(
                img_path=tmp_path,
                detector_backend='retinaface',   # most accurate detector
                enforce_detection=True,
                align=True,
            )
            if len(faces) == 0:
                return Response(
                    {'error': 'No face detected in the image. Please use a clear, well-lit photo with the face centred.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if len(faces) > 1:
                return Response(
                    {'error': f'{len(faces)} faces detected. Please upload a photo with only one person.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Confidence guard: retinaface returns a 'confidence' key (0–1)
            confidence = faces[0].get('confidence', 1.0)
            if confidence < 0.90:
                return Response(
                    {'error': f'Face confidence too low ({confidence:.0%}). Please use a clearer, better-lit photo.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Exception as e:
            err_str = str(e)
            if 'Face could not be detected' in err_str or 'cannot be detected' in err_str.lower():
                return Response(
                    {'error': 'Face could not be detected. Ensure the photo is well-lit, faces the camera directly, and has no obstructions.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({'error': f'Face validation error: {err_str}'}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

        # ── Save the validated reference image ────────────────────────────────
        face_obj, _ = EmployeeFaceData.objects.get_or_create(
            user=target_user,
            defaults={'admin_owner': _get_admin_owner(request.user)},
        )
        face_obj.reference_image.save(
            f'face_{user_id}.jpg',
            ContentFile(img_bytes),
            save=False,   # don't hit DB yet — we compute embedding first
        )

        # ── Fix 3: Pre-compute and store the Facenet512 embedding ─────────────
        # This means punch time = one cosine distance calculation (~1ms)
        # instead of running DeepFace.verify() which reloads the model each time.
        emb_tmp = self._write_temp(img_bytes, suffix='.jpg')
        try:
            representations = DeepFace.represent(
                img_path=emb_tmp,
                model_name='Facenet512',
                detector_backend='retinaface',
                enforce_detection=True,
                align=True,
            )
            embedding = representations[0]['embedding']  # list of 512 floats
            face_obj.face_embedding = json.dumps(embedding)
        except Exception:
            # Embedding computation failed — still save the image so the
            # record isn't lost.  Punch will fall back to DeepFace.verify().
            face_obj.face_embedding = None
        finally:
            try:
                if os.path.exists(emb_tmp):
                    os.remove(emb_tmp)
            except Exception:
                pass

        face_obj.save()

        return Response({'message': 'Face registered successfully!', 'user_id': int(user_id), 'face_registered': True})

    @action(detail=False, methods=['post'], url_path='kiosk-punch')
    def kiosk_punch(self, request):
        """
        Kiosk / shared-device punch endpoint.

        Unlike check-in / check-out (which require the employee's own token),
        this endpoint accepts a DEVICE token (any authenticated user — typically
        an admin or a dedicated kiosk service account) and identifies the
        employee by comparing the submitted face image against every registered
        face in the tenant.

        POST /api/attendance/face/kiosk-punch/
        Content-Type: multipart/form-data

        Required fields:
            image       — camera frame (multipart file or base64 string)

        Optional fields:
            action      — "check_in" | "check_out" | "auto" (default: "auto")
            latitude    — GPS latitude  (float, optional)
            longitude   — GPS longitude (float, optional)
            address     — human-readable address string (optional)
            notes       — free-text note (optional)

        Responses:
            200  — punch recorded; body contains employee info + attendance record
            400  — missing image / already checked in+out / no face registered
            403  — face not recognised / geofence blocked
            404  — no registered face matches the submitted image
        """
        # ── 0. Pull request fields ────────────────────────────────────────────
        image_data = request.FILES.get('image') or request.data.get('image')
        action_req = request.data.get('action', 'auto')   # "check_in" | "check_out" | "auto"
        latitude   = request.data.get('latitude')
        longitude  = request.data.get('longitude')
        address    = request.data.get('address', '') or ''
        notes      = request.data.get('notes', '') or ''

        if not image_data:
            return Response({'error': 'Image is required.'}, status=status.HTTP_400_BAD_REQUEST)

        if action_req not in ('check_in', 'check_out', 'auto'):
            return Response({'error': 'action must be "check_in", "check_out", or "auto".'}, status=status.HTTP_400_BAD_REQUEST)

        # ── 1. Decode incoming image bytes once (reused for each candidate) ───
        try:
            inc_bytes = self._load_image_bytes(image_data)
        except Exception as e:
            return Response({'error': f'Invalid image data: {e}'}, status=status.HTTP_400_BAD_REQUEST)

        if not inc_bytes:
            return Response({'error': 'Empty image received.'}, status=status.HTTP_400_BAD_REQUEST)

        # ── 2. Determine tenant scope from the device token ───────────────────
        device_user  = request.user
        admin_owner  = _get_admin_owner(device_user)

        # ── Face punch toggle gate ────────────────────────────────────────────
        _kiosk_settings = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        if _kiosk_settings and not _kiosk_settings.face_punch_enabled:
            return Response(
                {'error': 'Face punch-in/out is currently disabled for this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        # ─────────────────────────────────────────────────────────────────────

        # ── 3. Load all registered faces for this tenant ──────────────────────
        all_faces = (
            EmployeeFaceData.objects
            .filter(admin_owner=admin_owner, reference_image__isnull=False)
            .exclude(reference_image='')
            .select_related('user')
        )

        if not all_faces.exists():
            return Response(
                {'error': 'No faces registered for this organisation.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 4. Write incoming image to a temp file (deleted in finally) ───────
        inc_path = self._write_temp(inc_bytes, suffix='.jpg')

        matched_user  = None
        best_distance = 1.0   # lower = better match
        threshold     = self._FACE_MODELS[0]['threshold']  # 0.30

        # ── Fix 3: Compute live embedding once, then compare against all stored
        # embeddings in DB — pure numpy vector math, no model reloads per user.
        try:
            live_representations = DeepFace.represent(
                img_path=inc_path,
                model_name='Facenet512',
                detector_backend='retinaface',  # matches registration detector; handles mobile selfies correctly
                enforce_detection=True,
                align=True,
            )
            live_embedding = np.array(live_representations[0]['embedding'])
        except Exception as e:
            err_str = str(e)
            if 'Face could not be detected' in err_str or 'cannot be detected' in err_str.lower():
                return Response(
                    {'error': 'Face not clearly detected. Please ensure good lighting, face the camera directly, and remove obstructions.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response({'error': f'Face processing error: {err_str}'}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            try:
                if os.path.exists(inc_path):
                    os.remove(inc_path)
            except Exception:
                pass

        # ── Compare live embedding against each registered employee ───────────
        for face_obj in all_faces:
            if face_obj.face_embedding:
                # Fast path — stored embedding available (~1ms per candidate)
                try:
                    stored_embedding = np.array(json.loads(face_obj.face_embedding))
                    dot      = np.dot(live_embedding, stored_embedding)
                    norm     = np.linalg.norm(live_embedding) * np.linalg.norm(stored_embedding)
                    distance = 1.0 - (dot / norm) if norm > 0 else 1.0

                    if distance <= threshold and distance < best_distance:
                        best_distance = distance
                        matched_user  = face_obj.user
                except Exception:
                    continue  # skip corrupt embedding silently
            else:
                # Slow fallback — no embedding yet (pre-update employee)
                try:
                    with face_obj.reference_image.open('rb') as f:
                        ref_bytes = f.read()
                except Exception:
                    continue

                ref_path = self._write_temp(ref_bytes, suffix='.jpg')
                inc_path2 = self._write_temp(inc_bytes, suffix='.jpg')
                try:
                    ref_representations = DeepFace.represent(
                        img_path=ref_path,
                        model_name='Facenet512',
                        detector_backend='retinaface',
                        enforce_detection=True,
                        align=True,
                    )
                    stored_embedding = np.array(ref_representations[0]['embedding'])
                    dot      = np.dot(live_embedding, stored_embedding)
                    norm     = np.linalg.norm(live_embedding) * np.linalg.norm(stored_embedding)
                    distance = 1.0 - (dot / norm) if norm > 0 else 1.0

                    if distance <= threshold and distance < best_distance:
                        best_distance = distance
                        matched_user  = face_obj.user

                    # Opportunistically save embedding for next time
                    try:
                        face_obj.face_embedding = json.dumps(ref_representations[0]['embedding'])
                        face_obj.save(update_fields=['face_embedding', 'updated_at'])
                    except Exception:
                        pass
                except Exception:
                    continue
                finally:
                    for p in (ref_path, inc_path2):
                        try:
                            if os.path.exists(p):
                                os.remove(p)
                        except Exception:
                            pass

        # ── 5. No match found ─────────────────────────────────────────────────
        if matched_user is None:
            return Response(
                {'error': 'Face not recognised. Please try again or contact admin.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── 6. Geofence check (using the matched employee's profile) ──────────
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        allowed, geo_error, _ = validate_geofence(matched_user, latitude, longitude, settings_obj)
        if not allowed:
            return Response({'error': geo_error}, status=status.HTTP_403_FORBIDDEN)

        # ── 7. Determine punch direction ──────────────────────────────────────
        today        = timezone.now().date()
        current_time = timezone.now()

        def _safe_decimal(val):
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        lat_val  = _safe_decimal(latitude)
        lon_val  = _safe_decimal(longitude)

        try:
            attendance = Attendance.objects.get(
                user=matched_user,
                date=today,
                admin_owner=admin_owner,
            )
            created = False
        except Attendance.DoesNotExist:
            try:
                attendance = Attendance.objects.create(
                    user=matched_user,
                    date=today,
                    admin_owner=admin_owner,
                    check_in_time=current_time,
                    check_in_method='face',
                    notes=notes or '',
                    check_in_latitude=lat_val,
                    check_in_longitude=lon_val,
                    check_in_address=address or '',
                )
                # determine_status() sets status='half_day' (checked-in, no checkout) — correct.
                created = True
            except Exception:
                attendance = Attendance.objects.get(
                    user=matched_user,
                    date=today,
                    admin_owner=admin_owner,
                )
                created = False

        if created:
            # Brand-new record — always a check-in → status is already 'half_day'
            punch_result = 'checked_in'
        else:
            # Record already exists — decide based on action or auto-detect
            if action_req == 'check_in' or (action_req == 'auto' and not attendance.check_in_time):
                if attendance.check_in_time:
                    return Response(
                        {'error': f'{_get_full_name(matched_user) or matched_user.username} has already checked in today.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                attendance.check_in_time      = current_time
                attendance.check_in_method    = 'face'
                attendance.check_in_latitude  = lat_val
                attendance.check_in_longitude = lon_val
                attendance.check_in_address   = address or ''
                if notes:
                    attendance.notes = notes
                # determine_status() will set 'half_day' (no checkout yet)
                attendance.save(update_fields=[
                    'check_in_time', 'check_in_method',
                    'check_in_latitude', 'check_in_longitude',
                    'check_in_address', 'notes', 'status', 'updated_at',
                ])
                punch_result = 'checked_in'

            elif action_req == 'check_out' or (action_req == 'auto' and attendance.check_in_time and not attendance.check_out_time):
                if not attendance.check_in_time:
                    return Response(
                        {'error': f'{_get_full_name(matched_user) or matched_user.username} has not checked in yet today.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if attendance.check_out_time:
                    return Response(
                        {'error': f'{_get_full_name(matched_user) or matched_user.username} has already checked out today.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                attendance.check_out_time      = current_time
                attendance.check_out_method    = 'face'
                attendance.check_out_latitude  = lat_val
                attendance.check_out_longitude = lon_val
                attendance.check_out_address   = address or ''
                if notes:
                    attendance.notes = notes
                attendance.calculate_hours()
                # determine_status() will set 'present' (both times exist)
                attendance.save(update_fields=[
                    'check_out_time', 'check_out_method',
                    'check_out_latitude', 'check_out_longitude',
                    'check_out_address', 'notes', 'status', 'total_hours', 'updated_at',
                ])
                punch_result = 'checked_out'

            else:
                # auto + both already filled
                return Response(
                    {'error': f'{_get_full_name(matched_user) or matched_user.username} has already completed attendance for today.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # ── Auto-detect late check-in / early check-out ────────────────────────
        if punch_result == 'checked_in':
            _auto_create_late_request(matched_user, admin_owner, attendance)
        elif punch_result == 'checked_out':
            _auto_create_early_request(matched_user, admin_owner, attendance)
        # ─────────────────────────────────────────────────────────────────────

        # Serialize safely so a serializer crash never kills a successful punch
        try:
            att_fresh = (
                Attendance.objects
                .select_related('user', 'verified_by', 'late_approved_by')
                .get(pk=attendance.pk)
            )
            att_data = AttendanceSerializer(att_fresh, context={'request': request}).data
        except Exception:
            import pytz as _pytz
            _ist = _pytz.timezone('Asia/Kolkata')
            def _fmt(dt):
                return dt.astimezone(_ist).strftime('%I:%M %p') if dt else None
            att_data = {
                'id': attendance.pk,
                'date': str(attendance.date),
                'status': attendance.status,
                'total_hours': str(attendance.total_hours),
                'check_in_time': attendance.check_in_time.isoformat() if attendance.check_in_time else None,
                'check_in_time_formatted': _fmt(attendance.check_in_time),
                'check_out_time': attendance.check_out_time.isoformat() if attendance.check_out_time else None,
                'check_out_time_formatted': _fmt(attendance.check_out_time),
            }

        return Response(
            {
                'message':       f'Successfully {punch_result.replace("_", " ")}.',
                'punch':         punch_result,           # "checked_in" | "checked_out"
                'matched_user':  {
                    'id':         matched_user.id,
                    'username':   matched_user.username,
                    'full_name':  _get_full_name(matched_user),
                    'email':      matched_user.email,
                },
                'face_distance': round(best_distance, 4),
                'attendance':    att_data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'], url_path='auto-punch')
    def auto_punch(self, request):
        """
        Face-Recognition Auto-Punch (no employee login required).

        This endpoint is the single entry-point for a "login-free" attendance
        terminal.  The device authenticates once with a shared device / admin
        token.  Each employee simply walks up, scans their face, and this view:

          1. Identifies which employee the face belongs to (tenant-scoped).
          2. Decides whether to check-in or check-out (same logic as kiosk-punch).
          3. Records the attendance record.
          4. Generates a fresh JWT pair FOR THAT EMPLOYEE and returns it.
             The frontend can store this token and call authenticated endpoints
             (e.g. /attendance/today/, /attendance/history/) on that employee's
             behalf — no password ever needed.

        POST /api/attendance/face/auto-punch/
        Auth : Bearer <device_or_admin_token>

        Body (JSON or multipart):
            image      — base64 data-URI OR multipart file (required)
            action     — "check_in" | "check_out" | "auto"  (default: "auto")
            latitude   — float, optional
            longitude  — float, optional
            address    — string, optional
            notes      — string, optional

        Response 200:
            {
              "punch":        "checked_in" | "checked_out",
              "message":      "...",
              "matched_user": { id, username, full_name, email },
              "face_distance": 0.1234,
              "attendance":   { ...AttendanceSerializer fields... },
              "tokens": {
                "access":  "<JWT access token for matched employee>",
                "refresh": "<JWT refresh token for matched employee>"
              }
            }

        Errors:
            400 — image missing / already punched / no faces registered
            403 — face not detected clearly / geofence violation
            404 — no registered face matches the submitted image
        """
        # ── 0. Parse request fields ───────────────────────────────────────────
        image_data = request.FILES.get('image') or request.data.get('image')
        action_req = request.data.get('action', 'auto')
        latitude   = request.data.get('latitude')
        longitude  = request.data.get('longitude')
        address    = request.data.get('address', '') or ''
        notes      = request.data.get('notes', '') or ''

        if not image_data:
            return Response({'error': 'Image is required.'}, status=status.HTTP_400_BAD_REQUEST)

        if action_req not in ('check_in', 'check_out', 'auto'):
            return Response(
                {'error': 'action must be "check_in", "check_out", or "auto".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 1. Decode incoming image bytes ────────────────────────────────────
        try:
            inc_bytes = self._load_image_bytes(image_data)
        except Exception as e:
            return Response({'error': f'Invalid image data: {e}'}, status=status.HTTP_400_BAD_REQUEST)

        if not inc_bytes:
            return Response({'error': 'Empty image received.'}, status=status.HTTP_400_BAD_REQUEST)

        # ── 2. Tenant scope from the device token ─────────────────────────────
        device_user = request.user
        admin_owner = _get_admin_owner(device_user)

        # ── Face punch toggle gate ────────────────────────────────────────────
        _auto_settings = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        if _auto_settings and not _auto_settings.face_punch_enabled:
            return Response(
                {'error': 'Face punch-in/out is currently disabled for this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        # ─────────────────────────────────────────────────────────────────────

        # ── 3. Load all registered faces for this tenant ──────────────────────
        all_faces = (
            EmployeeFaceData.objects
            .filter(admin_owner=admin_owner, reference_image__isnull=False)
            .exclude(reference_image='')
            .select_related('user')
        )
        if not all_faces.exists():
            return Response(
                {'error': 'No faces registered for this organisation.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 4. Compute live face embedding once ───────────────────────────────
        inc_path = self._write_temp(inc_bytes, suffix='.jpg')
        matched_user  = None
        best_distance = 1.0
        threshold     = self._FACE_MODELS[0]['threshold']  # 0.30

        try:
            live_reps = DeepFace.represent(
                img_path=inc_path,
                model_name='Facenet512',
                detector_backend='retinaface',  # matches registration detector; handles mobile selfies correctly
                enforce_detection=True,
                align=True,
            )
            live_embedding = np.array(live_reps[0]['embedding'])
        except Exception as e:
            err_str = str(e)
            if 'Face could not be detected' in err_str or 'cannot be detected' in err_str.lower():
                return Response(
                    {'error': 'Face not clearly detected. Ensure good lighting, face the camera directly, and remove obstructions.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response({'error': f'Face processing error: {err_str}'}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            try:
                if os.path.exists(inc_path):
                    os.remove(inc_path)
            except Exception:
                pass

        # ── 5. Compare against every registered employee ──────────────────────
        for face_obj in all_faces:
            if face_obj.face_embedding:
                try:
                    stored_emb = np.array(json.loads(face_obj.face_embedding))
                    dot      = np.dot(live_embedding, stored_emb)
                    norm     = np.linalg.norm(live_embedding) * np.linalg.norm(stored_emb)
                    distance = 1.0 - (dot / norm) if norm > 0 else 1.0
                    if distance <= threshold and distance < best_distance:
                        best_distance = distance
                        matched_user  = face_obj.user
                except Exception:
                    continue
            else:
                # Slow fallback for employees without pre-computed embeddings
                try:
                    with face_obj.reference_image.open('rb') as f:
                        ref_bytes = f.read()
                except Exception:
                    continue
                ref_path  = self._write_temp(ref_bytes, suffix='.jpg')
                inc_path2 = self._write_temp(inc_bytes, suffix='.jpg')
                try:
                    ref_reps = DeepFace.represent(
                        img_path=ref_path,
                        model_name='Facenet512',
                        detector_backend='retinaface',
                        enforce_detection=True,
                        align=True,
                    )
                    stored_emb = np.array(ref_reps[0]['embedding'])
                    dot      = np.dot(live_embedding, stored_emb)
                    norm     = np.linalg.norm(live_embedding) * np.linalg.norm(stored_emb)
                    distance = 1.0 - (dot / norm) if norm > 0 else 1.0
                    if distance <= threshold and distance < best_distance:
                        best_distance = distance
                        matched_user  = face_obj.user
                    # Cache embedding for next time
                    try:
                        face_obj.face_embedding = json.dumps(ref_reps[0]['embedding'])
                        face_obj.save(update_fields=['face_embedding', 'updated_at'])
                    except Exception:
                        pass
                except Exception:
                    continue
                finally:
                    for p in (ref_path, inc_path2):
                        try:
                            if os.path.exists(p):
                                os.remove(p)
                        except Exception:
                            pass

        # ── 6. No match found ─────────────────────────────────────────────────
        if matched_user is None:
            return Response(
                {'error': 'Face not recognised. Please try again or contact your admin.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── 7. Geofence check for the matched employee ────────────────────────
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        allowed, geo_error, _ = validate_geofence(matched_user, latitude, longitude, settings_obj)
        if not allowed:
            return Response({'error': geo_error}, status=status.HTTP_403_FORBIDDEN)

        # ── 8. Punch attendance ───────────────────────────────────────────────
        today        = timezone.now().date()
        current_time = timezone.now()

        # Coerce lat/lon to float (request.data returns strings from JSON/form)
        def _safe_decimal(val):
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        lat_val  = _safe_decimal(latitude)
        lon_val  = _safe_decimal(longitude)

        # Use select_for_update-style: try to fetch first, create only if missing
        # This avoids the IntegrityError race on unique_together(user, date)
        try:
            attendance = Attendance.objects.get(
                user=matched_user,
                date=today,
                admin_owner=admin_owner,
            )
            created = False
        except Attendance.DoesNotExist:
            try:
                attendance = Attendance.objects.create(
                    user=matched_user,
                    date=today,
                    admin_owner=admin_owner,
                    check_in_time=current_time,
                    check_in_method='face',
                    notes=notes or '',
                    check_in_latitude=lat_val,
                    check_in_longitude=lon_val,
                    check_in_address=address or '',
                )
                # determine_status() sets status='half_day' (checked-in, no checkout) — correct.
                created = True
            except Exception:
                # Race: another request created it between our GET and CREATE
                attendance = Attendance.objects.get(
                    user=matched_user,
                    date=today,
                    admin_owner=admin_owner,
                )
                created = False

        if created:
            punch_result = 'checked_in'
        else:
            if action_req == 'check_in' or (action_req == 'auto' and not attendance.check_in_time):
                if attendance.check_in_time:
                    return Response(
                        {'error': f'{_get_full_name(matched_user) or matched_user.username} has already checked in today.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                attendance.check_in_time      = current_time
                attendance.check_in_method    = 'face'
                attendance.check_in_latitude  = lat_val
                attendance.check_in_longitude = lon_val
                attendance.check_in_address   = address or ''
                if notes:
                    attendance.notes = notes
                attendance.save(update_fields=[
                    'check_in_time', 'check_in_method',
                    'check_in_latitude', 'check_in_longitude',
                    'check_in_address', 'notes', 'status', 'updated_at',
                ])
                punch_result = 'checked_in'

            elif action_req == 'check_out' or (action_req == 'auto' and attendance.check_in_time and not attendance.check_out_time):
                if not attendance.check_in_time:
                    return Response(
                        {'error': f'{_get_full_name(matched_user) or matched_user.username} has not checked in yet today.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if attendance.check_out_time:
                    return Response(
                        {'error': f'{_get_full_name(matched_user) or matched_user.username} has already checked out today.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                attendance.check_out_time      = current_time
                attendance.check_out_method    = 'face'
                attendance.check_out_latitude  = lat_val
                attendance.check_out_longitude = lon_val
                attendance.check_out_address   = address or ''
                if notes:
                    attendance.notes = notes
                # Recalculate hours and set status to present now both times exist
                attendance.calculate_hours()
                attendance.status = 'present'
                attendance.save(update_fields=[
                    'check_out_time', 'check_out_method',
                    'check_out_latitude', 'check_out_longitude',
                    'check_out_address', 'notes', 'status', 'total_hours', 'updated_at',
                ])
                punch_result = 'checked_out'

            else:
                return Response(
                    {'error': f'{_get_full_name(matched_user) or matched_user.username} has already completed attendance for today.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # ── Auto-detect late check-in / early check-out ────────────────────────
        if punch_result == 'checked_in':
            _auto_create_late_request(matched_user, admin_owner, attendance)
        elif punch_result == 'checked_out':
            _auto_create_early_request(matched_user, admin_owner, attendance)
        # ─────────────────────────────────────────────────────────────────────

        # ── 9. Generate JWT tokens (wrapped so a token error never kills the response) ──
        try:
            employee_tokens = _generate_tokens_for_user(matched_user)
        except Exception:
            employee_tokens = {'access': '', 'refresh': ''}

        # ── 10. Serialize attendance safely (re-fetch with relations to avoid stale data) ──
        try:
            attendance_fresh = (
                Attendance.objects
                .select_related('user', 'verified_by', 'late_approved_by')
                .get(pk=attendance.pk)
            )
            attendance_data = AttendanceSerializer(
                attendance_fresh,
                context={'request': request},
            ).data
        except Exception:
            # Fallback: minimal dict so a serializer crash never causes a 500
            try:
                import pytz as _pytz
                _ist = _pytz.timezone('Asia/Kolkata')
                def _fmt(dt):
                    return dt.astimezone(_ist).strftime('%I:%M %p') if dt else None
            except Exception:
                def _fmt(dt):
                    return str(dt) if dt else None
            attendance_data = {
                'id':                       attendance.pk,
                'date':                     str(attendance.date),
                'status':                   attendance.status,
                'total_hours':              str(attendance.total_hours),
                'check_in_time':            attendance.check_in_time.isoformat() if attendance.check_in_time else None,
                'check_in_time_formatted':  _fmt(attendance.check_in_time),
                'check_out_time':           attendance.check_out_time.isoformat() if attendance.check_out_time else None,
                'check_out_time_formatted': _fmt(attendance.check_out_time),
            }

        return Response(
            {
                'message':       f'Successfully {punch_result.replace("_", " ")}.',
                'punch':         punch_result,
                'matched_user':  {
                    'id':        matched_user.id,
                    'username':  matched_user.username,
                    'full_name': _get_full_name(matched_user),
                    'email':     matched_user.email,
                },
                'face_distance': round(best_distance, 4),
                'attendance':    attendance_data,
                'tokens':        employee_tokens,
            },
            status=status.HTTP_200_OK,
        )


    # ── Single-model config: Facenet512 only ─────────────────────────────────
    # VGG-Face was dropped because:
    #   • It's slower (runs sequentially, ~2× total latency).
    #   • Its cosine threshold is poorly calibrated for diverse skin tones,
    #     causing false rejections even when Facenet512 passes cleanly.
    # Facenet512 cosine threshold: 0.35 (slightly above the DeepFace default 0.30).
    # The 0.30 default works well in controlled settings, but real-world webcam
    # images vary in lighting, angle, and camera quality — raising to 0.35
    # reduces false rejections while keeping false positives acceptably low.
    _FACE_MODELS = [
        {'model': 'Facenet512',  'metric': 'cosine',    'threshold': 0.35},
    ]

    @staticmethod
    def _normalize_image_orientation(raw_bytes: bytes) -> bytes:
        """
        Bake any EXIF orientation tag into the pixel data and return a clean
        RGB JPEG.

        Mobile phones (iOS + Android) write the image sensor output in landscape
        but store a rotation flag in EXIF.  OpenCV (used by DeepFace as the fast
        detector) ignores that flag, so it receives a rotated/flipped face and
        fails to detect it.  RetinaFace handles it better but not perfectly.

        By stripping EXIF and physically rotating the pixels here — before any
        DeepFace call — we guarantee the image is always upright regardless of
        which detector is used or how the photo was taken.

        Falls back to returning the original bytes if Pillow is not installed or
        if the image cannot be opened (non-fatal).
        """
        if not _PIL_AVAILABLE or not raw_bytes:
            return raw_bytes
        try:
            img = _PilImage.open(io.BytesIO(raw_bytes))
            # ImageOps.exif_transpose rotates the image so that the pixel data
            # matches what you see on screen, then removes the EXIF orientation tag.
            img = _PilImageOps.exif_transpose(img)
            # Ensure RGB (some phones save RGBA / CMYK / palette PNGs)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=95)
            return buf.getvalue()
        except Exception:
            # Non-fatal — return original bytes unchanged
            return raw_bytes

    def _load_image_bytes(self, image_data):
        """
        Accept a base64 string (with or without data-URI prefix) or a Django
        UploadedFile / any file-like object.  Returns raw JPEG bytes with EXIF
        orientation baked in (so mobile selfies are always upright for DeepFace).
        """
        if isinstance(image_data, str):
            if ',' in image_data:
                image_data = image_data.split(',', 1)[1]
            raw = base64.b64decode(image_data)
        else:
            if hasattr(image_data, 'seek'):
                image_data.seek(0)
            raw = image_data.read()
        # ── Normalise EXIF orientation so DeepFace always sees an upright face ──
        return self._normalize_image_orientation(raw)

    def _write_temp(self, content: bytes, suffix='.jpg') -> str:
        """Write bytes to a named temp file and return the path (caller must delete)."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            tmp.write(content)
        finally:
            tmp.close()
        return tmp.name

    def _verify_face(self, user, incoming_image_data):
        """
        Fast face verification using pre-computed Facenet512 embeddings.

        Flow:
          1. Load the stored embedding from DB (computed at registration time).
          2. Run DeepFace.represent() on the incoming image — just ONE forward
             pass through the already-warm model (~300ms vs 20s cold load).
          3. Compute cosine distance between the two 512-dim vectors (~1ms).

        Falls back to DeepFace.verify() (slower) if no embedding is stored yet
        (e.g. employees registered before this update).
        """
        # ── 1. Load face record ───────────────────────────────────────────────
        try:
            face_data = EmployeeFaceData.objects.get(user=user)
        except EmployeeFaceData.DoesNotExist:
            return False, 'Face not registered. Please contact Admin.'

        # ── 2. Decode incoming image ──────────────────────────────────────────
        try:
            inc_bytes = self._load_image_bytes(incoming_image_data)
        except Exception as e:
            return False, f'Invalid incoming image: {e}'

        if not inc_bytes:
            return False, 'Empty image received.'

        threshold = self._FACE_MODELS[0]['threshold']  # 0.30

        # ── 3a. Fast path — embedding already stored in DB ────────────────────
        if face_data.face_embedding:
            inc_path = self._write_temp(inc_bytes, suffix='.jpg')
            try:
                representations = DeepFace.represent(
                    img_path=inc_path,
                    model_name='Facenet512',
                    detector_backend='retinaface',  # matches registration detector; handles mobile selfies correctly
                    enforce_detection=True,
                    align=True,
                )
                live_embedding = np.array(representations[0]['embedding'])
                stored_embedding = np.array(json.loads(face_data.face_embedding))

                # Cosine distance = 1 - cosine_similarity
                dot   = np.dot(live_embedding, stored_embedding)
                norm  = np.linalg.norm(live_embedding) * np.linalg.norm(stored_embedding)
                distance = 1.0 - (dot / norm) if norm > 0 else 1.0

                if distance <= threshold:
                    return True, 'Verified'
                return False, f'Face does not match. (Facenet512={distance:.3f}/FAIL, threshold={threshold})'

            except Exception as e:
                err_str = str(e)
                if 'Face could not be detected' in err_str or 'cannot be detected' in err_str.lower():
                    return False, (
                        'Face not clearly detected. Please ensure good lighting, '
                        'face the camera directly, and remove obstructions.'
                    )
                # Unexpected error — fall through to slow path below
            finally:
                try:
                    if os.path.exists(inc_path):
                        os.remove(inc_path)
                except Exception:
                    pass

        # ── 3b. Slow fallback — no embedding stored yet (pre-update employees) ─
        # Also recomputes and saves the embedding so next time is fast.
        if not face_data.reference_image:
            return False, 'Face data is invalid or missing.'

        try:
            with face_data.reference_image.open('rb') as f:
                ref_bytes = f.read()
        except Exception as e:
            return False, f'Failed to load reference face: {e}'

        ref_path = self._write_temp(ref_bytes, suffix='.jpg')
        inc_path = self._write_temp(inc_bytes, suffix='.jpg')
        try:
            result = DeepFace.verify(
                img1_path=inc_path,
                img2_path=ref_path,
                model_name='Facenet512',
                distance_metric='cosine',
                enforce_detection=True,
                align=True,
            )
            distance = result.get('distance', 1.0)
            passed   = result.get('verified', False) and (distance <= threshold)

            if passed:
                # ── Opportunistically save embedding so next punch is fast ────
                try:
                    representations = DeepFace.represent(
                        img_path=ref_path,
                        model_name='Facenet512',
                        detector_backend='retinaface',
                        enforce_detection=True,
                        align=True,
                    )
                    face_data.face_embedding = json.dumps(representations[0]['embedding'])
                    face_data.save(update_fields=['face_embedding', 'updated_at'])
                except Exception:
                    pass   # Non-fatal — will retry on next successful punch
                return True, 'Verified'

            return False, f'Face does not match. (Facenet512={distance:.3f}/FAIL, threshold={threshold})'

        except Exception as e:
            err_str = str(e)
            if 'Face could not be detected' in err_str or 'cannot be detected' in err_str.lower():
                return False, (
                    'Face not clearly detected. Please ensure good lighting, '
                    'face the camera directly, and remove obstructions.'
                )
            return False, f'Face verification error: {err_str}'
        finally:
            for p in (ref_path, inc_path):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

    @action(detail=False, methods=['post'], url_path='check-in')
    def check_in(self, request):
        user = request.user
        image_data = request.FILES.get('image') or request.data.get('image')
        notes = request.data.get('notes', '')
        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')
        address = request.data.get('address', '')

        if not image_data:
            return Response({'error': 'Image is required for face check-in.'}, status=status.HTTP_400_BAD_REQUEST)

        # ── Face punch toggle gate ────────────────────────────────────────────
        admin_owner = _get_admin_owner(user)
        _face_settings = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        if _face_settings and not _face_settings.face_punch_enabled:
            return Response(
                {'error': 'Face punch-in/out is currently disabled for this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        # ─────────────────────────────────────────────────────────────────────

        verified, msg = self._verify_face(user, image_data)
        if not verified:
            return Response({'error': msg}, status=status.HTTP_403_FORBIDDEN)

        # Build the clean data dict and validate directly — avoids the broken
        # request._full_data mutation which does not affect the already-parsed
        # request.data cache in DRF.
        clean_data = {
            'notes': notes,
            'latitude': latitude,
            'longitude': longitude,
            'address': address,
        }

        today = timezone.now().date()
        current_time = timezone.now()

        # Geofence enforcement runs first — no point hitting the DB for
        # serializer validation if the location is already blocked.
        settings_obj = _face_settings
        allowed, geo_error, _ = validate_geofence(user, latitude, longitude, settings_obj, today)
        if not allowed:
            return Response({'error': geo_error}, status=status.HTTP_403_FORBIDDEN)

        serializer = CheckInSerializer(data=clean_data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        attendance, created = Attendance.objects.get_or_create(
            user=user,
            date=today,
            admin_owner=admin_owner,
            defaults={
                'check_in_time': current_time,
                'check_in_method': 'face',
                'notes': serializer.validated_data.get('notes', ''),
                'check_in_latitude': serializer.validated_data.get('latitude'),
                'check_in_longitude': serializer.validated_data.get('longitude'),
                'check_in_address': serializer.validated_data.get('address', ''),
            }
        )

        if not created:
            if not attendance.check_in_time:
                attendance.check_in_time = current_time
                attendance.check_in_method = 'face'
                attendance.notes = serializer.validated_data.get('notes', '')
                attendance.check_in_latitude = serializer.validated_data.get('latitude')
                attendance.check_in_longitude = serializer.validated_data.get('longitude')
                attendance.check_in_address = serializer.validated_data.get('address', '')
                attendance.save()
            else:
                return Response({'error': 'Already checked in today'}, status=status.HTTP_400_BAD_REQUEST)

        # ── Activity log ──────────────────────────────────────────────────────
        ist_tz = pytz.timezone('Asia/Kolkata')
        ci_local = attendance.check_in_time.astimezone(ist_tz)
        log_activity(
            user=user,
            action_type='CREATE',
            module='Attendance',
            description=f"Face check-in at {ci_local.strftime('%I:%M %p')}",
            request=request,
        )
        # ─────────────────────────────────────────────────────────────────────

        # ── Auto-detect late check-in ──────────────────────────────────────────
        _auto_create_late_request(user, admin_owner, attendance)
        # ─────────────────────────────────────────────────────────────────────

        return Response({
            'message': 'Successfully checked in',
            'attendance': AttendanceSerializer(attendance).data
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='check-out')
    def check_out(self, request):
        user = request.user
        image_data = request.FILES.get('image') or request.data.get('image')
        notes = request.data.get('notes', '')
        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')
        address = request.data.get('address', '')

        if not image_data:
            return Response({'error': 'Image is required for face check-out.'}, status=status.HTTP_400_BAD_REQUEST)

        # ── Face punch toggle gate ────────────────────────────────────────────
        admin_owner = _get_admin_owner(user)
        _face_co_settings = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        if _face_co_settings and not _face_co_settings.face_punch_enabled:
            return Response(
                {'error': 'Face punch-in/out is currently disabled for this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        # ─────────────────────────────────────────────────────────────────────

        verified, msg = self._verify_face(user, image_data)
        if not verified:
            return Response({'error': msg}, status=status.HTTP_403_FORBIDDEN)

        clean_data = {
            'notes': notes,
            'latitude': latitude,
            'longitude': longitude,
            'address': address,
        }

        today = timezone.now().date()
        current_time = timezone.now()

        # Geofence enforcement runs first
        settings_obj = _face_co_settings
        allowed, geo_error, _ = validate_geofence(user, latitude, longitude, settings_obj, today)
        if not allowed:
            return Response({'error': geo_error}, status=status.HTTP_403_FORBIDDEN)

        serializer = CheckOutSerializer(data=clean_data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        try:
            attendance = Attendance.objects.get(user=user, date=today, admin_owner=admin_owner)
        except Attendance.DoesNotExist:
            return Response({'error': 'No check-in record found for today'}, status=status.HTTP_400_BAD_REQUEST)

        if attendance.check_out_time:
            return Response({'error': 'Already checked out today'}, status=status.HTTP_400_BAD_REQUEST)

        attendance.check_out_time = current_time
        attendance.check_out_method = 'face'
        attendance.check_out_latitude = serializer.validated_data.get('latitude')
        attendance.check_out_longitude = serializer.validated_data.get('longitude')
        attendance.check_out_address = serializer.validated_data.get('address', '')
        if serializer.validated_data.get('notes'):
            attendance.notes = serializer.validated_data['notes']
        attendance.save()

        # ── Activity log ──────────────────────────────────────────────────────
        ist_tz = pytz.timezone('Asia/Kolkata')
        co_local = attendance.check_out_time.astimezone(ist_tz)
        log_activity(
            user=user,
            action_type='UPDATE',
            module='Attendance',
            description=f"Face check-out at {co_local.strftime('%I:%M %p')}",
            request=request,
        )
        # ─────────────────────────────────────────────────────────────────────

        # ── Auto-detect early check-out ────────────────────────────────────────
        _auto_create_early_request(user, admin_owner, attendance)
        # ─────────────────────────────────────────────────────────────────────

        return Response({
            'message': 'Successfully checked out',
            'attendance': AttendanceSerializer(attendance).data
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='break-in')
    def break_in(self, request):
        """
        Face-recognition–based break start.
        Employee captures their face → face is verified → break is started.
        No location required.
        POST /api/attendance/face/break-in/
        """
        user = request.user
        image_data = request.FILES.get('image') or request.data.get('image')
        notes = request.data.get('notes', '')

        if not image_data:
            return Response({'error': 'Image is required for face break-in.'}, status=status.HTTP_400_BAD_REQUEST)

        admin_owner = _get_admin_owner(user)
        _face_break_settings = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        if _face_break_settings and not _face_break_settings.face_break_enabled:
            return Response(
                {'error': 'Face break-in/out is currently disabled for this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if _face_break_settings and not _face_break_settings.face_punch_enabled:
            return Response(
                {'error': 'Face punch-in/out is currently disabled for this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        verified, msg = self._verify_face(user, image_data)
        if not verified:
            return Response({'error': msg}, status=status.HTTP_403_FORBIDDEN)

        today = timezone.now().date()
        current_time = timezone.now()

        try:
            attendance = Attendance.objects.get(user=user, date=today, admin_owner=admin_owner)
        except Attendance.DoesNotExist:
            return Response({'error': 'No check-in record found for today. Please check in first.'}, status=status.HTTP_400_BAD_REQUEST)

        if not attendance.check_in_time:
            return Response({'error': 'No check-in record found for today. Please check in first.'}, status=status.HTTP_400_BAD_REQUEST)

        if attendance.check_out_time:
            return Response({'error': 'Already checked out today. Cannot start break.'}, status=status.HTTP_400_BAD_REQUEST)

        # Check for active break
        active_break = BreakRecord.objects.filter(
            user=user, attendance=attendance, break_end__isnull=True
        ).first()
        if active_break:
            return Response({'error': 'You already have an active break. Please end it first.'}, status=status.HTTP_400_BAD_REQUEST)

        break_record = BreakRecord.objects.create(
            admin_owner=admin_owner,
            attendance=attendance,
            user=user,
            break_start=current_time,
        )

        from .serializers import BreakRecordSerializer
        return Response({
            'message': 'Break started successfully.',
            'break': BreakRecordSerializer(break_record).data,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='break-out')
    def break_out(self, request):
        """
        Face-recognition–based break end.
        Employee captures their face → face is verified → active break is ended.
        No location required.
        POST /api/attendance/face/break-out/
        """
        user = request.user
        image_data = request.FILES.get('image') or request.data.get('image')

        if not image_data:
            return Response({'error': 'Image is required for face break-out.'}, status=status.HTTP_400_BAD_REQUEST)

        admin_owner = _get_admin_owner(user)
        _face_break_settings = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        if _face_break_settings and not _face_break_settings.face_break_enabled:
            return Response(
                {'error': 'Face break-in/out is currently disabled for this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if _face_break_settings and not _face_break_settings.face_punch_enabled:
            return Response(
                {'error': 'Face punch-in/out is currently disabled for this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        verified, msg = self._verify_face(user, image_data)
        if not verified:
            return Response({'error': msg}, status=status.HTTP_403_FORBIDDEN)

        today = timezone.now().date()
        current_time = timezone.now()

        try:
            attendance = Attendance.objects.get(user=user, date=today, admin_owner=admin_owner)
        except Attendance.DoesNotExist:
            return Response({'error': 'No check-in record found for today.'}, status=status.HTTP_400_BAD_REQUEST)

        if not attendance.check_in_time:
            return Response({'error': 'No check-in record found for today.'}, status=status.HTTP_400_BAD_REQUEST)

        active_break = BreakRecord.objects.filter(
            user=user, attendance=attendance, break_end__isnull=True
        ).first()
        if not active_break:
            return Response({'error': 'No active break found.'}, status=status.HTTP_400_BAD_REQUEST)

        active_break.break_end = current_time
        active_break.save()

        # Sync total break minutes on the attendance record
        self._sync_break_total(attendance)

        from .serializers import BreakRecordSerializer
        return Response({
            'message': 'Break ended successfully.',
            'break': BreakRecordSerializer(active_break).data,
        }, status=status.HTTP_200_OK)

    def _sync_break_total(self, attendance):
        """Recalculate and persist total_break_minutes for the given attendance."""
        total = BreakRecord.objects.filter(
            attendance=attendance,
            break_end__isnull=False,
        ).aggregate(total=Sum('duration_minutes'))['total'] or 0
        attendance.total_break_minutes = total
        attendance.save(update_fields=['total_break_minutes', 'updated_at'])

  
class SalaryAdvanceRequestViewSet(viewsets.ModelViewSet):
    """
    Salary Advance Request ViewSet
    ─────────────────────────────
    Users  : create, list (own), cancel (own pending)
    Admins : list (all), review (approve/reject)
 
    Endpoints (prefix: /attendance/salary-advance-requests/)
    ─────────────────────────────────────────────────────────
    GET    /                          list  – own or all (admin)
    POST   /                          create new request (user)
    GET    /my-requests/              user's own requests
    GET    /pending/                  admin: all pending
    GET    /stats/                    admin: summary counts
    POST   /{id}/review/             admin: approve or reject
    DELETE /{id}/                    user: cancel own pending request
    """
 
    serializer_class   = SalaryAdvanceRequestSerializer
    permission_classes = [permissions.IsAuthenticated]
 
    # ── Queryset ──────────────────────────────────────────────────────────────
 
    def get_queryset(self):
        user = self.request.user
        admin_owner = _get_admin_owner(user)
        qs = SalaryAdvanceRequest.objects.filter(admin_owner=admin_owner)
        if _is_admin(user):
            status_filter = self.request.query_params.get('status')
            if status_filter:
                qs = qs.filter(status=status_filter)
            return qs.order_by('-created_at')
        return qs.filter(user=user)
 
    # ── Create ────────────────────────────────────────────────────────────────
 
    def create(self, request, *args, **kwargs):
        serializer = CreateSalaryAdvanceRequestSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(
            user=request.user,
            admin_owner=_get_admin_owner(request.user),
        )

        # ── WhatsApp: salary_advance_request notification ─────────────────────
        _wa_notify(
            admin_owner   = _get_admin_owner(request.user),
            purpose_key   = 'salary_advance_request',
            employee_user = request.user,
            context       = {
                'name':              _get_full_name(request.user),
                'amount':            str(instance.amount),
                'repayment_months':  str(instance.repayment_months),
                'reason':            instance.reason or '',
            },
        )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=request.user,
            action_type='CREATE',
            module='Attendance',
            description=f"Submitted salary advance request for amount {instance.amount}",
            request=request,
        )

        return Response(
            SalaryAdvanceRequestSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )
 
    # ── Cancel (user deletes own pending request) ─────────────────────────────
 
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if _is_admin(request.user):
            # Admin: hard-delete any request regardless of status
            instance.delete()
            log_activity(
                user=request.user,
                action_type='DELETE',
                module='Attendance',
                description=f"Admin deleted salary advance request for amount {instance.amount}",
                request=request,
            )
            return Response({'message': 'Salary advance request deleted.'}, status=status.HTTP_200_OK)
        if instance.user != request.user:
            return Response(
                {'error': 'You can only cancel your own requests.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if instance.status != 'pending':
            return Response(
                {'error': 'Only pending requests can be cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"Cancelled salary advance request for amount {instance.amount}",
            request=request,
        )
        return Response(
            SalaryAdvanceRequestSerializer(instance).data,
            status=status.HTTP_200_OK,
        )
 
    # ── Admin: review (approve / reject) ─────────────────────────────────────
 
    @action(detail=True, methods=['post'], url_path='review')
    def review(self, request, pk=None):
        if not _is_admin(request.user):
            return Response(
                {'error': 'Only admins can review salary advance requests.'},
                status=status.HTTP_403_FORBIDDEN,
            )
 
        serializer = SalaryAdvanceApprovalSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
 
        instance = self.get_object()
        if instance.status != 'pending':
            return Response(
                {'error': f'This request is already {instance.status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
 
        action_val      = serializer.validated_data['action']
        admin_notes     = serializer.validated_data.get('admin_notes', '')
        approved_amount = serializer.validated_data.get('approved_amount')
 
        instance.status      = 'approved' if action_val == 'approve' else 'rejected'
        instance.reviewed_by = request.user
        instance.reviewed_at = timezone.now()
        instance.admin_notes = admin_notes
 
        if action_val == 'approve':
            instance.approved_amount = approved_amount or instance.amount
 
        instance.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at',
            'admin_notes', 'approved_amount', 'updated_at',
        ])
 
        # ── WhatsApp: salary_advance_approved / rejected notification ─────────
        if action_val == 'approve':
            _wa_notify(
                admin_owner   = _get_admin_owner(request.user),
                purpose_key   = 'salary_advance_approved',
                employee_user = instance.user,
                context       = {
                    'name':             _get_full_name(instance.user),
                    'approved_amount':  str(instance.approved_amount),
                    'repayment_months': str(instance.repayment_months),
                },
            )
        else:
            _wa_notify(
                admin_owner   = _get_admin_owner(request.user),
                purpose_key   = 'salary_advance_rejected',
                employee_user = instance.user,
                context       = {
                    'name':        _get_full_name(instance.user),
                    'admin_notes': admin_notes or 'No notes provided.',
                },
            )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"{action_val.capitalize()}d salary advance request for {instance.user.username} (amount: {instance.amount})",
            request=request,
        )

        return Response(
            SalaryAdvanceRequestSerializer(instance).data,
            status=status.HTTP_200_OK,
        )
 
    # ── User: own requests ────────────────────────────────────────────────────
 
    @action(detail=False, methods=['get'], url_path='my-requests')
    def my_requests(self, request):
        qs = SalaryAdvanceRequest.objects.filter(
            user=request.user,
            admin_owner=_get_admin_owner(request.user),
        ).order_by('-created_at')
        serializer = SalaryAdvanceRequestSerializer(qs, many=True)
        return Response(serializer.data)
 
    # ── Admin: pending list ───────────────────────────────────────────────────
 
    @action(detail=False, methods=['get'], url_path='pending')
    def pending(self, request):
        if not _is_admin(request.user):
            return Response(
                {'error': 'Only admins can view pending requests.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = self.get_queryset().filter(status='pending').order_by('-created_at')
        serializer = SalaryAdvanceRequestSerializer(qs, many=True)
        return Response(serializer.data)
 
    # ── Admin: stats ──────────────────────────────────────────────────────────
 
    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        if not _is_admin(request.user):
            return Response(
                {'error': 'Only admins can view stats.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = self.get_queryset()
        return Response({
            'total':    qs.count(),
            'pending':  qs.filter(status='pending').count(),
            'approved': qs.filter(status='approved').count(),
            'rejected': qs.filter(status='rejected').count(),
        })



# ─────────────────────────────────────────────────────────────────────────────
# WFH REQUEST VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class WFHRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for WFHRequest – tenant-isolated.

    Users:
      POST   /wfh-requests/              – submit a WFH request
      GET    /wfh-requests/my-requests/  – list own requests
      DELETE /wfh-requests/{id}/         – cancel own pending request

    Admins:
      GET    /wfh-requests/              – list all (filter ?status=pending)
      GET    /wfh-requests/pending/      – pending only
      GET    /wfh-requests/stats/        – stats
      POST   /wfh-requests/{id}/review/  – approve or reject
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return CreateWFHRequestSerializer
        if self.action == 'review':
            return WFHApprovalSerializer
        return WFHRequestSerializer

    def get_queryset(self):
        user = self.request.user
        admin_owner = _get_admin_owner(user)
        qs = WFHRequest.objects.filter(
            admin_owner=admin_owner
        ).select_related('user', 'reviewed_by')

        if _is_admin(user):
            status_filter = self.request.query_params.get('status')
            user_filter = self.request.query_params.get('user_id')
            if status_filter:
                qs = qs.filter(status=status_filter)
            if user_filter:
                qs = qs.filter(user_id=user_filter)
            return qs

        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        serializer = CreateWFHRequestSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(
            user=request.user,
            admin_owner=_get_admin_owner(request.user)
        )

        # ── WhatsApp: wfh_request notification ────────────────────────────────
        _wa_notify(
            admin_owner   = _get_admin_owner(request.user),
            purpose_key   = 'wfh_request',
            employee_user = request.user,
            context       = {
                'name':   _get_full_name(request.user),
                'date':   str(instance.date),
                'reason': instance.reason or '',
            },
        )
        # ─────────────────────────────────────────────────────────────────────

        log_activity(
            user=request.user,
            action_type='CREATE',
            module='Attendance',
            description=f"Submitted WFH request for {instance.date}",
            request=request,
        )

        return Response(
            WFHRequestSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if _is_admin(request.user):
            # Admin: hard-delete any request regardless of status
            instance.delete()
            log_activity(
                user=request.user,
                action_type='DELETE',
                module='Attendance',
                description=f"Admin deleted WFH request for {instance.date}",
                request=request,
            )
            return Response({'message': 'WFH request deleted.'}, status=status.HTTP_200_OK)
        if instance.user != request.user:
            return Response(
                {'error': 'You cannot cancel this request.'},
                status=status.HTTP_403_FORBIDDEN
            )
        if instance.status != 'pending':
            return Response(
                {'error': 'Only pending requests can be cancelled.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"Cancelled WFH request for {instance.date}",
            request=request,
        )
        return Response({'message': 'WFH request cancelled.'}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='my-requests')
    def my_requests(self, request):
        admin_owner = _get_admin_owner(request.user)
        qs = WFHRequest.objects.filter(
            user=request.user, admin_owner=admin_owner
        ).order_by('-created_at')
        return Response(WFHRequestSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='pending')
    def pending_requests(self, request):
        if not _is_admin(request.user):
            return Response(
                {'error': 'Only admins can view pending requests.'},
                status=status.HTTP_403_FORBIDDEN
            )
        admin_owner = _get_admin_owner(request.user)
        qs = WFHRequest.objects.filter(
            admin_owner=admin_owner, status='pending'
        ).select_related('user').order_by('-created_at')
        return Response(WFHRequestSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)
        admin_owner = _get_admin_owner(request.user)
        qs = WFHRequest.objects.filter(admin_owner=admin_owner)
        return Response({
            'total':     qs.count(),
            'pending':   qs.filter(status='pending').count(),
            'approved':  qs.filter(status='approved').count(),
            'rejected':  qs.filter(status='rejected').count(),
            'cancelled': qs.filter(status='cancelled').count(),
        })

    @action(detail=True, methods=['post'], url_path='review')
    def review(self, request, pk=None):
        """Admin approves or rejects a WFH request."""
        if not _is_admin(request.user):
            return Response(
                {'error': 'Only admins can review WFH requests.'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = WFHApprovalSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        wfh_req = self.get_object()
        action_type = serializer.validated_data['action']

        if wfh_req.status != 'pending':
            return Response(
                {'error': f'Cannot review a request that is already "{wfh_req.status}".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        admin_notes = serializer.validated_data.get('admin_notes', '')
        admin_owner = _get_admin_owner(request.user)

        if action_type == 'approve':
            wfh_req.status = 'approved'
            message = 'WFH request approved successfully.'

            # Mark the attendance record as WFH / present for that day
            att_obj, created = Attendance.objects.get_or_create(
                user=wfh_req.user,
                date=wfh_req.date,
                admin_owner=admin_owner,
                defaults={
                    'status': 'present',
                    'is_wfh': True,
                    'notes': f'Work from home approved – {wfh_req.reason}',
                    'is_verified': True,
                    'verified_by': request.user,
                    'verified_at': timezone.now(),
                },
            )
            if not created:
                # Record already exists (employee checked in from home) — just flag it
                att_obj.is_wfh = True
                att_obj.notes = (att_obj.notes or '') + f'\n[WFH approved – {wfh_req.reason}]'
                att_obj.save(update_fields=['is_wfh', 'notes', 'updated_at'])

            # ── WhatsApp: wfh_approved notification ───────────────────────────
            _wa_notify(
                admin_owner   = admin_owner,
                purpose_key   = 'wfh_approved',
                employee_user = wfh_req.user,
                context       = {
                    'name':        _get_full_name(wfh_req.user),
                    'date':        str(wfh_req.date),
                    'admin_notes': admin_notes or '',
                },
            )
        else:  # reject
            wfh_req.status = 'rejected'
            message = 'WFH request rejected.'

            # ── WhatsApp: wfh_rejected notification ───────────────────────────
            _wa_notify(
                admin_owner   = admin_owner,
                purpose_key   = 'wfh_rejected',
                employee_user = wfh_req.user,
                context       = {
                    'name':        _get_full_name(wfh_req.user),
                    'date':        str(wfh_req.date),
                    'admin_notes': admin_notes or '',
                },
            )

        wfh_req.reviewed_by = request.user
        wfh_req.reviewed_at = timezone.now()
        wfh_req.admin_notes = admin_notes
        wfh_req.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes', 'updated_at'])

        log_activity(
            user=request.user,
            action_type='UPDATE',
            module='Attendance',
            description=f"{action_type.capitalize()}d WFH request for {wfh_req.user.username} on {wfh_req.date}",
            request=request,
        )

        return Response({'message': message, 'request': WFHRequestSerializer(wfh_req).data})