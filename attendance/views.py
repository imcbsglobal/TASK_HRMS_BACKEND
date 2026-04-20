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

import tempfile
import os
import base64
import requests
from django.core.files.base import ContentFile
from deepface import DeepFace


from .models import Attendance, AttendanceSettings, LeaveRequest, LateArrivalRequest, EarlyDepartureRequest, EmployeeFaceData
from .geofence import validate_geofence
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
)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _is_admin(user):
    return (
        user.is_staff or user.is_superuser or
        getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']
    )


def _get_admin_owner(user):
    """
    Return the admin_owner for tenant-scoped writes.
    - If the user is an admin themselves, they ARE the owner.
    - If the user is a regular employee, their admin_owner is stored on their
      profile as `user.admin_owner` (adjust the field name to match your User model).
    """
    if _is_admin(user):
        return user
    return getattr(user, 'admin_owner', None)


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
                attendance.check_in_time = check_in_dt
            if check_out_dt is not None:
                attendance.check_out_time = check_out_dt
            attendance.save(update_fields=[
                'status', 'is_verified', 'verified_by', 'verified_at',
                'notes', 'check_in_time', 'check_out_time', 'updated_at'
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

        # ── Geofence enforcement ──────────────────────────────────────────────
        admin_owner = _get_admin_owner(user)
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        allowed, geo_error, _ = validate_geofence(user, latitude, longitude, settings_obj)
        if not allowed:
            return Response({'error': geo_error}, status=status.HTTP_403_FORBIDDEN)
        # ─────────────────────────────────────────────────────────────────────

        attendance, created = Attendance.objects.get_or_create(
            user=user,
            date=today,
            admin_owner=admin_owner,
            defaults={
                'check_in_time': current_time,
                'notes': serializer.validated_data.get('notes', ''),
                'status': 'present',
                'check_in_latitude': latitude,
                'check_in_longitude': longitude,
                'check_in_address': address,
            }
        )

        if not created:
            if not attendance.check_in_time:
                attendance.check_in_time = current_time
                attendance.notes = serializer.validated_data.get('notes', '')
                attendance.check_in_latitude = latitude
                attendance.check_in_longitude = longitude
                attendance.check_in_address = address
                attendance.save()
            else:
                return Response({'error': 'Already checked in today'}, status=status.HTTP_400_BAD_REQUEST)

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

        # ── Geofence enforcement ──────────────────────────────────────────────
        admin_owner = _get_admin_owner(user)
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).order_by('-id').first()
        allowed, geo_error, _ = validate_geofence(user, latitude, longitude, settings_obj)
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
        attendance.check_out_latitude = latitude
        attendance.check_out_longitude = longitude
        attendance.check_out_address = address
        if serializer.validated_data.get('notes'):
            attendance.notes = serializer.validated_data['notes']
        attendance.save()

        return Response({
            'message': 'Successfully checked out',
            'attendance': AttendanceSerializer(attendance).data
        }, status=status.HTTP_200_OK)

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
        if not request.user.is_staff:
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
            current_date = first_day
            total_days = 0
            while current_date <= last_day:
                if current_date.weekday() < 5:
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
        Returns employees whose email matches a user that has at least one
        attendance record within this tenant.  Admin only.
        """
        user = request.user
        if not _is_admin(user):
            return Response({'error': 'Only admins can access this.'}, status=status.HTTP_403_FORBIDDEN)

        from employee_management.models import Employee

        admin_owner = _get_admin_owner(user)
        user_emails = (
            Attendance.objects
            .filter(admin_owner=admin_owner)
            .values_list('user__email', flat=True)
            .distinct()
        )

        employees = Employee.objects.filter(
            admin_owner=admin_owner,
            email__in=user_emails,
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
            if (first_day + timedelta(days=d)).weekday() < 5
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
            user_filter = self.request.query_params.get('user_id')
            if status_filter:
                qs = qs.filter(status=status_filter)
            if user_filter:
                qs = qs.filter(user_id=user_filter)
            return qs

        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        serializer = CreateLateArrivalRequestSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(user=request.user, admin_owner=_get_admin_owner(request.user))
        return Response(
            LateArrivalRequestSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.user != request.user and not _is_admin(request.user):
            return Response({'error': 'You cannot cancel this request.'}, status=status.HTTP_403_FORBIDDEN)
        if instance.status != 'pending':
            return Response({'error': 'Only pending requests can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
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

        if late_req.status != 'pending':
            return Response(
                {'error': f'Cannot review a request that is already "{late_req.status}".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action_type = serializer.validated_data['action']
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
                    'status': 'late',
                    'notes': f'Late arrival approved – {late_req.reason}',
                    'is_verified': True,
                    'verified_by': request.user,
                    'verified_at': timezone.now(),
                },
            )
            if not attendance.is_verified:
                attendance.status = 'late'
                attendance.notes = f'Late arrival approved – {late_req.reason}'
                attendance.is_verified = True
                attendance.verified_by = request.user
                attendance.verified_at = timezone.now()
                attendance.save(update_fields=[
                    'status', 'notes', 'is_verified', 'verified_by', 'verified_at', 'updated_at',
                ])
        else:
            late_req.status = 'rejected'
            message = 'Late arrival request rejected.'

        late_req.reviewed_by = request.user
        late_req.reviewed_at = timezone.now()
        late_req.admin_notes = admin_notes
        late_req.save()

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
            user_filter = self.request.query_params.get('user_id')
            if status_filter:
                qs = qs.filter(status=status_filter)
            if user_filter:
                qs = qs.filter(user_id=user_filter)
            return qs

        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        serializer = CreateLeaveRequestSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        leave_request = serializer.save(user=request.user, admin_owner=_get_admin_owner(request.user))
        return Response(
            LeaveRequestSerializer(leave_request).data,
            status=status.HTTP_201_CREATED
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.user != request.user and not _is_admin(request.user):
            return Response({'error': 'You cannot cancel this leave request.'}, status=status.HTTP_403_FORBIDDEN)
        if instance.status != 'pending':
            return Response({'error': 'Only pending leave requests can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
        return Response({'message': 'Leave request cancelled successfully.'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='review')
    def review_leave(self, request, pk=None):
        """Admin approves or rejects a leave request."""
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can review leave requests.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = LeaveApprovalSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        leave_request = self.get_object()

        if leave_request.status not in ['pending']:
            return Response(
                {'error': f'Cannot review a leave request that is already "{leave_request.status}".'},
                status=status.HTTP_400_BAD_REQUEST
            )

        action_type = serializer.validated_data['action']
        admin_notes = serializer.validated_data.get('admin_notes', '')
        admin_owner = _get_admin_owner(request.user)

        if action_type == 'approve':
            leave_request.status = 'approved'
            message = 'Leave request approved successfully.'

            current_date = leave_request.start_date
            while current_date <= leave_request.end_date:
                if current_date.weekday() < 5:
                    attendance, created = Attendance.objects.get_or_create(
                        user=leave_request.user,
                        date=current_date,
                        admin_owner=admin_owner,
                        defaults={
                            'status': 'leave',
                            'notes': f'Approved leave: {leave_request.get_leave_type_display()}',
                            'is_verified': True,
                            'verified_by': request.user,
                            'verified_at': timezone.now(),
                        }
                    )
                    if not created and not attendance.check_in_time:
                        attendance.status = 'leave'
                        attendance.notes = f'Approved leave: {leave_request.get_leave_type_display()}'
                        attendance.is_verified = True
                        attendance.verified_by = request.user
                        attendance.verified_at = timezone.now()
                        attendance.save(update_fields=['status', 'notes', 'is_verified', 'verified_by', 'verified_at', 'updated_at'])
                current_date += timedelta(days=1)
        else:
            leave_request.status = 'rejected'
            message = 'Leave request rejected.'

        leave_request.reviewed_by = request.user
        leave_request.reviewed_at = timezone.now()
        leave_request.admin_notes = admin_notes
        leave_request.save()

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
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    @action(detail=False, methods=['get'], url_path='current')
    def current_settings(self, request):
        admin_owner = _get_admin_owner(request.user)
        settings_obj = AttendanceSettings.objects.filter(admin_owner=admin_owner).first()
        if not settings_obj:
            settings_obj = AttendanceSettings.objects.create(admin_owner=admin_owner)
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
            user_filter = self.request.query_params.get('user_id')
            if status_filter:
                qs = qs.filter(status=status_filter)
            if user_filter:
                qs = qs.filter(user_id=user_filter)
            return qs

        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        serializer = CreateEarlyDepartureRequestSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(user=request.user, admin_owner=_get_admin_owner(request.user))
        return Response(
            EarlyDepartureRequestSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.user != request.user and not _is_admin(request.user):
            return Response({'error': 'You cannot cancel this request.'}, status=status.HTTP_403_FORBIDDEN)
        if instance.status != 'pending':
            return Response({'error': 'Only pending requests can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
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

        if early_req.status != 'pending':
            return Response(
                {'error': f'Cannot review a request that is already "{early_req.status}".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action_type = serializer.validated_data['action']
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
                    'status': 'half_day',
                    'notes': f'Early departure approved – {early_req.reason}',
                    'is_verified': True,
                    'verified_by': request.user,
                    'verified_at': timezone.now(),
                },
            )
            if not created and not attendance.is_verified:
                attendance.status = 'half_day'
                attendance.notes = f'Early departure approved – {early_req.reason}'
                attendance.is_verified = True
                attendance.verified_by = request.user
                attendance.verified_at = timezone.now()
                attendance.save(update_fields=[
                    'status', 'notes', 'is_verified', 'verified_by', 'verified_at', 'updated_at',
                ])
        else:
            early_req.status = 'rejected'
            message = 'Early departure request rejected.'

        early_req.reviewed_by = request.user
        early_req.reviewed_at = timezone.now()
        early_req.admin_notes = admin_notes
        early_req.save()

        return Response({
            'message': message,
            'early_departure_request': EarlyDepartureRequestSerializer(early_req).data,
        }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
# FACE RECOGNITION VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class FaceRecognitionViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='register-face')
    def register_face(self, request):
        if not _is_admin(request.user):
            return Response({'error': 'Only admins can register face.'}, status=status.HTTP_403_FORBIDDEN)
        
        user_id = request.data.get('user_id')
        image_data = request.FILES.get('image') or request.data.get('image') # Support file upload or base64
        
        if not user_id or not image_data:
            return Response({'error': 'user_id and image are required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            target_user = get_user_model().objects.get(pk=user_id, admin_owner=_get_admin_owner(request.user))
        except get_user_model().DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)
            
        face_data, _ = EmployeeFaceData.objects.get_or_create(
            user=target_user,
            admin_owner=_get_admin_owner(request.user)
        )
        
        if isinstance(image_data, str) and image_data.startswith('data:image'):
            # Base64 string
            format, imgstr = image_data.split(';base64,')
            ext = format.split('/')[-1]
            face_data.reference_image.save(f"face_{user_id}.{ext}", ContentFile(base64.b64decode(imgstr)), save=True)
        else:
            # InMemoryUploadedFile
            face_data.reference_image = image_data
            face_data.save()
            
        return Response({'message': 'Face registered successfully!'})
        
    def _verify_face(self, user, incoming_image_data):
        try:
            face_data = EmployeeFaceData.objects.get(user=user)
        except EmployeeFaceData.DoesNotExist:
            return False, 'Face not registered. Please contact Admin.'
            
        if not face_data.reference_image:
            return False, 'Face data is invalid.'
            
        try:
            # Use Django's storage abstraction to read the file
            with face_data.reference_image.open('rb') as f:
                ref_img_content = f.read()
        except Exception as e:
            return False, f'Failed to load reference face: {str(e)}'
            
        try:
            # Handle incoming image (can be base64 or file upload)
            if isinstance(incoming_image_data, str):
                if ',' in incoming_image_data:
                    incoming_image_data = incoming_image_data.split(',')[1]
                incoming_img_content = base64.b64decode(incoming_image_data)
            else:
                incoming_img_content = incoming_image_data.read()
        except Exception as e:
            return False, f'Invalid incoming image format: {str(e)}'
            
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as ref_tmp, \
             tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as inc_tmp:
            ref_tmp.write(ref_img_content)
            inc_tmp.write(incoming_img_content)
            ref_path = ref_tmp.name
            inc_path = inc_tmp.name
            
        verified = False
        message = 'Face doesnt matches'
        try:
            # model_name 'VGG-Face' is a common and reliable choice
            result = DeepFace.verify(
                img1_path=inc_path, 
                img2_path=ref_path, 
                model_name='VGG-Face', 
                enforce_detection=False
            )
            verified = result.get('verified', False)
        except Exception as e:
            verified = False
            # Differentiate between a mismatch and a crash (e.g. missing weights)
            message = f'Face verification error: {str(e)}'
        finally:
            try:
                if os.path.exists(ref_path): os.remove(ref_path)
                if os.path.exists(inc_path): os.remove(inc_path)
            except:
                pass
            
        return verified, message if not verified else 'Verified'

    @action(detail=False, methods=['post'], url_path='check-in')
    def check_in(self, request):
        user = request.user
        image_data = request.data.get('image') or request.FILES.get('image')
        notes = request.data.get('notes', '')
        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')
        address = request.data.get('address', '')
        
        if not image_data:
            return Response({'error': 'Image is required for face check-in.'}, status=status.HTTP_400_BAD_REQUEST)
            
        verified, msg = self._verify_face(user, image_data)
        if not verified:
            return Response({'error': msg}, status=status.HTTP_403_FORBIDDEN)
            
        # Call existing check-in logic
        attendance_view = AttendanceViewSet()
        attendance_view.request = request
        attendance_view.format_kwarg = None
        # We need to recreate the request data to match check in serializer expectations
        mutable_data = {
            'notes': notes,
            'latitude': latitude,
            'longitude': longitude,
            'address': address
        }
        request._full_data = mutable_data
        
        return attendance_view.check_in(request)

    @action(detail=False, methods=['post'], url_path='check-out')
    def check_out(self, request):
        user = request.user
        image_data = request.data.get('image') or request.FILES.get('image')
        notes = request.data.get('notes', '')
        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')
        address = request.data.get('address', '')
        
        if not image_data:
            return Response({'error': 'Image is required for face check-out.'}, status=status.HTTP_400_BAD_REQUEST)
            
        verified, msg = self._verify_face(user, image_data)
        if not verified:
            return Response({'error': msg}, status=status.HTTP_403_FORBIDDEN)
            
        attendance_view = AttendanceViewSet()
        attendance_view.request = request
        attendance_view.format_kwarg = None
        mutable_data = {
            'notes': notes,
            'latitude': latitude,
            'longitude': longitude,
            'address': address
        }
        request._full_data = mutable_data
        
        return attendance_view.check_out(request)
