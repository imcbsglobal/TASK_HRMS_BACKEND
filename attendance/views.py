from django.shortcuts import render
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum, Count, Q, Avg
from datetime import datetime, timedelta
from calendar import monthrange
import pytz

from .models import Attendance, AttendanceSettings, LeaveRequest, LateArrivalRequest
from .serializers import (
    AttendanceSerializer, CheckInSerializer, CheckOutSerializer,
    MonthlyStatsSerializer, TodayAttendanceSerializer,
    AttendanceSettingsSerializer, LateRequestSerializer, LateApprovalSerializer,
    VerifyAttendanceSerializer, LeaveRequestSerializer, CreateLeaveRequestSerializer,
    LeaveApprovalSerializer,
    LateArrivalRequestSerializer, CreateLateArrivalRequestSerializer,
    LateArrivalApprovalSerializer,
)


class AttendanceViewSet(viewsets.ModelViewSet):
    serializer_class = AttendanceSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return Attendance.objects.all()
        return Attendance.objects.filter(user=user)
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
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
        user = request.user
        is_admin = (
            user.is_staff or user.is_superuser or
            getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']
        )
        if not is_admin:
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
        
        attendance, created = Attendance.objects.get_or_create(
            user=user,
            date=today,
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
        
        response_serializer = AttendanceSerializer(attendance)
        return Response({
            'message': 'Successfully checked in',
            'attendance': response_serializer.data
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
        
        try:
            attendance = Attendance.objects.get(user=user, date=today)
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
        
        response_serializer = AttendanceSerializer(attendance)
        return Response({
            'message': 'Successfully checked out',
            'attendance': response_serializer.data
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='today')
    def today_status(self, request):
        user = request.user
        today = timezone.now().date()
        
        try:
            attendance = Attendance.objects.get(user=user, date=today)
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
        request_date = serializer.validated_data.get('request_date')
        reason = serializer.validated_data.get('reason')
        
        attendance, created = Attendance.objects.get_or_create(
            user=user,
            date=request_date,
            defaults={'status': 'absent'}
        )
        attendance.late_request = True
        attendance.late_request_reason = reason
        attendance.late_request_status = 'pending'
        attendance.save()
        
        response_serializer = AttendanceSerializer(attendance)
        return Response({
            'message': 'Late request submitted successfully',
            'attendance': response_serializer.data
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
        
        response_serializer = AttendanceSerializer(attendance)
        return Response({'message': message, 'attendance': response_serializer.data}, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='pending-late-requests')
    def pending_late_requests(self, request):
        if not request.user.is_staff:
            return Response({'error': 'Only admins can view pending late requests'}, status=status.HTTP_403_FORBIDDEN)
        
        pending_requests = Attendance.objects.filter(
            late_request=True,
            late_request_status='pending'
        ).order_by('-date')
        
        serializer = AttendanceSerializer(pending_requests, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='monthly-stats')
    def monthly_stats(self, request):
        user = request.user
        year = int(request.query_params.get('year', timezone.now().year))
        month = int(request.query_params.get('month', timezone.now().month))
        
        first_day = datetime(year, month, 1).date()
        last_day = datetime(year, month, monthrange(year, month)[1]).date()
        
        attendances = Attendance.objects.filter(user=user, date__gte=first_day, date__lte=last_day)
        
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
        days = int(request.query_params.get('days', 30))
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days)
        
        attendances = Attendance.objects.filter(
            user=user,
            date__gte=start_date,
            date__lte=end_date
        ).order_by('-date')
        
        serializer = AttendanceSerializer(attendances, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
# LATE ARRIVAL REQUEST VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class LateArrivalRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for LateArrivalRequest.

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

    def _is_admin(self, user):
        return (
            user.is_staff or user.is_superuser or
            getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']
        )

    def get_serializer_class(self):
        if self.action == 'create':
            return CreateLateArrivalRequestSerializer
        if self.action == 'review':
            return LateArrivalApprovalSerializer
        return LateArrivalRequestSerializer

    def get_queryset(self):
        user = self.request.user
        qs = LateArrivalRequest.objects.select_related('user', 'reviewed_by')

        if self._is_admin(user):
            status_filter = self.request.query_params.get('status')
            user_filter   = self.request.query_params.get('user_id')
            if status_filter:
                qs = qs.filter(status=status_filter)
            if user_filter:
                qs = qs.filter(user_id=user_filter)
            return qs

        # Regular users see only their own requests
        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        """User submits a late arrival request."""
        serializer = CreateLateArrivalRequestSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(user=request.user)
        return Response(
            LateArrivalRequestSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        """Users can cancel their own pending requests."""
        instance = self.get_object()
        if instance.user != request.user and not self._is_admin(request.user):
            return Response(
                {'error': 'You cannot cancel this request.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if instance.status != 'pending':
            return Response(
                {'error': 'Only pending requests can be cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
        return Response({'message': 'Late arrival request cancelled.'}, status=status.HTTP_200_OK)

    # ── Custom actions ──────────────────────────────────────────────────────

    @action(detail=False, methods=['get'], url_path='my-requests')
    def my_requests(self, request):
        """Current user's own late arrival requests."""
        qs = LateArrivalRequest.objects.filter(user=request.user).order_by('-created_at')
        return Response(LateArrivalRequestSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='pending')
    def pending_requests(self, request):
        """Admin-only: all pending late arrival requests."""
        if not self._is_admin(request.user):
            return Response(
                {'error': 'Only admins can view all pending requests.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = LateArrivalRequest.objects.filter(status='pending').select_related('user').order_by('-created_at')
        return Response(LateArrivalRequestSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """Admin-only: counts by status."""
        if not self._is_admin(request.user):
            return Response({'error': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            'total':    LateArrivalRequest.objects.count(),
            'pending':  LateArrivalRequest.objects.filter(status='pending').count(),
            'approved': LateArrivalRequest.objects.filter(status='approved').count(),
            'rejected': LateArrivalRequest.objects.filter(status='rejected').count(),
            'cancelled':LateArrivalRequest.objects.filter(status='cancelled').count(),
        })

    @action(detail=True, methods=['post'], url_path='review')
    def review(self, request, pk=None):
        """
        Admin approves or rejects a late arrival request.
        On approval the corresponding Attendance record is marked 'late' + verified.

        Body: { "action": "approve"|"reject", "admin_notes": "..." }
        """
        if not self._is_admin(request.user):
            return Response(
                {'error': 'Only admins can review late arrival requests.'},
                status=status.HTTP_403_FORBIDDEN,
            )

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

        if action_type == 'approve':
            late_req.status = 'approved'
            message = 'Late arrival request approved successfully.'

            # Mark the corresponding Attendance record as 'late' and verified
            attendance, _ = Attendance.objects.get_or_create(
                user=late_req.user,
                date=late_req.date,
                defaults={
                    'status': 'late',
                    'notes': f'Late arrival approved – {late_req.reason}',
                    'is_verified': True,
                    'verified_by': request.user,
                    'verified_at': timezone.now(),
                },
            )
            # Update existing record if it wasn't just created
            if not attendance.is_verified:
                attendance.status = 'late'
                attendance.notes = f'Late arrival approved – {late_req.reason}'
                attendance.is_verified = True
                attendance.verified_by = request.user
                attendance.verified_at = timezone.now()
                attendance.save(update_fields=[
                    'status', 'notes', 'is_verified',
                    'verified_by', 'verified_at', 'updated_at',
                ])
        else:
            late_req.status = 'rejected'
            message = 'Late arrival request rejected.'

        late_req.reviewed_by  = request.user
        late_req.reviewed_at  = timezone.now()
        late_req.admin_notes  = admin_notes
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
    ViewSet for managing leave requests.
    Users can create/view their own requests.
    Admins can view all requests and approve/reject them.
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'create':
            return CreateLeaveRequestSerializer
        if self.action in ['approve_leave', 'reject_leave']:
            return LeaveApprovalSerializer
        return LeaveRequestSerializer
    
    def get_queryset(self):
        user = self.request.user
        is_admin = (
            user.is_staff or user.is_superuser or
            getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']
        )
        qs = LeaveRequest.objects.select_related('user', 'reviewed_by')
        
        if is_admin:
            status_filter = self.request.query_params.get('status')
            user_filter = self.request.query_params.get('user_id')
            if status_filter:
                qs = qs.filter(status=status_filter)
            if user_filter:
                qs = qs.filter(user_id=user_filter)
            return qs
        
        return qs.filter(user=user)
    
    def create(self, request, *args, **kwargs):
        """Create a new leave request"""
        serializer = CreateLeaveRequestSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        leave_request = serializer.save(user=request.user)
        return Response(
            LeaveRequestSerializer(leave_request).data,
            status=status.HTTP_201_CREATED
        )
    
    def destroy(self, request, *args, **kwargs):
        """Users can cancel their own pending leave requests"""
        instance = self.get_object()
        if instance.user != request.user and not request.user.is_staff:
            return Response({'error': 'You cannot cancel this leave request.'}, status=status.HTTP_403_FORBIDDEN)
        if instance.status != 'pending':
            return Response({'error': 'Only pending leave requests can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        instance.status = 'cancelled'
        instance.save(update_fields=['status', 'updated_at'])
        return Response({'message': 'Leave request cancelled successfully.'}, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'], url_path='review')
    def review_leave(self, request, pk=None):
        """Admin approves or rejects a leave request."""
        user = request.user
        is_admin = (
            user.is_staff or user.is_superuser or
            getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']
        )
        if not is_admin:
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
        
        if action_type == 'approve':
            leave_request.status = 'approved'
            message = 'Leave request approved successfully.'
            
            current_date = leave_request.start_date
            while current_date <= leave_request.end_date:
                if current_date.weekday() < 5:
                    attendance, created = Attendance.objects.get_or_create(
                        user=leave_request.user,
                        date=current_date,
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
        """Get all pending leave requests (Admin only)"""
        user = request.user
        is_admin = (
            user.is_staff or user.is_superuser or
            getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']
        )
        if not is_admin:
            return Response({'error': 'Only admins can view all pending requests.'}, status=status.HTTP_403_FORBIDDEN)
        
        pending = LeaveRequest.objects.filter(status='pending').select_related('user').order_by('-created_at')
        serializer = LeaveRequestSerializer(pending, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='my-requests')
    def my_requests(self, request):
        """Get current user's leave requests"""
        requests_qs = LeaveRequest.objects.filter(user=request.user).order_by('-created_at')
        serializer = LeaveRequestSerializer(requests_qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='stats')
    def leave_stats(self, request):
        """Get leave request statistics (Admin only)"""
        user = request.user
        is_admin = (
            user.is_staff or user.is_superuser or
            getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']
        )
        if not is_admin:
            return Response({'error': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)
        
        total = LeaveRequest.objects.count()
        pending = LeaveRequest.objects.filter(status='pending').count()
        approved = LeaveRequest.objects.filter(status='approved').count()
        rejected = LeaveRequest.objects.filter(status='rejected').count()
        
        return Response({
            'total': total,
            'pending': pending,
            'approved': approved,
            'rejected': rejected,
        }, status=status.HTTP_200_OK)


class AttendanceSettingsViewSet(viewsets.ModelViewSet):
    queryset = AttendanceSettings.objects.all()
    serializer_class = AttendanceSettingsSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]
    
    @action(detail=False, methods=['get'], url_path='current')
    def current_settings(self, request):
        settings = AttendanceSettings.objects.first()
        if not settings:
            settings = AttendanceSettings.objects.create()
        serializer = self.get_serializer(settings)
        return Response(serializer.data, status=status.HTTP_200_OK)