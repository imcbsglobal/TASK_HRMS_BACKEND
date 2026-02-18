from django.shortcuts import render
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum, Count, Q, Avg
from datetime import datetime, timedelta
from calendar import monthrange
import pytz

from .models import Attendance, AttendanceSettings
from .serializers import (
    AttendanceSerializer, CheckInSerializer, CheckOutSerializer,
    MonthlyStatsSerializer, TodayAttendanceSerializer,
    AttendanceSettingsSerializer, LateRequestSerializer, LateApprovalSerializer,
    VerifyAttendanceSerializer
)


class AttendanceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing attendance records
    """
    serializer_class = AttendanceSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """
        Return attendance records for the current user
        Admins can see all records
        """
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return Attendance.objects.all()
        return Attendance.objects.filter(user=user)
    
    def create(self, request, *args, **kwargs):
        """Override create to set user automatically"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    def partial_update(self, request, *args, **kwargs):
        """
        Allow admin to PATCH status and notes.
        If is_verified=True is included, stamp verified_by / verified_at.
        """
        instance = self.get_object()
        allowed_fields = {'status', 'notes', 'is_verified'}
        data = {k: v for k, v in request.data.items() if k in allowed_fields}

        # If admin is setting/changing status, mark as verified
        if 'status' in data or data.get('is_verified'):
            data['is_verified'] = True

        serializer = self.get_serializer(instance, data=data, partial=True)
        serializer.is_valid(raise_exception=True)

        # Manually stamp verified_by / verified_at when verifying
        if data.get('is_verified'):
            instance.is_verified = True
            instance.verified_by = request.user
            instance.verified_at = timezone.now()
        if 'status' in data:
            instance.status = data['status']
        if 'notes' in data:
            instance.notes = data['notes']
        # Use save with update_fields to skip determine_status re-run
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
        """
        Admin verifies attendance and optionally overrides the status.
        POST /api/attendance/{id}/verify/
        Body: { "status": "present"|"absent"|"half_day"|"late"|"leave", "notes": "..." }
        """
        # Permission check
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

        # Save only the specific fields (bypass auto determine_status)
        attendance.save(update_fields=['status', 'is_verified', 'verified_by', 'verified_at', 'notes', 'updated_at'])

        return Response({
            'message': f'Attendance verified. Status set to "{attendance.get_status_display()}".',
            'attendance': AttendanceSerializer(attendance).data
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['post'], url_path='check-in')
    def check_in(self, request):
        """
        Check in for the day - records current time and location
        """
        serializer = CheckInSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        user = request.user
        today = timezone.now().date()
        current_time = timezone.now()
        
        # Get location data from request
        latitude = serializer.validated_data.get('latitude')
        longitude = serializer.validated_data.get('longitude')
        address = serializer.validated_data.get('address', '')
        
        # Get or create attendance record for today
        attendance, created = Attendance.objects.get_or_create(
            user=user,
            date=today,
            defaults={
                'check_in_time': current_time,
                'notes': serializer.validated_data.get('notes', ''),
                'status': 'present',  # Set as present when checking in
                'check_in_latitude': latitude,
                'check_in_longitude': longitude,
                'check_in_address': address,
            }
        )
        
        if not created:
            # Update check-in time if not already set
            if not attendance.check_in_time:
                attendance.check_in_time = current_time
                attendance.notes = serializer.validated_data.get('notes', '')
                attendance.check_in_latitude = latitude
                attendance.check_in_longitude = longitude
                attendance.check_in_address = address
                attendance.save()
            else:
                return Response(
                    {'error': 'Already checked in today'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        response_serializer = AttendanceSerializer(attendance)
        return Response({
            'message': 'Successfully checked in',
            'attendance': response_serializer.data
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['post'], url_path='check-out')
    def check_out(self, request):
        """
        Check out for the day - records current time, location and calculates hours
        """
        serializer = CheckOutSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        user = request.user
        today = timezone.now().date()
        current_time = timezone.now()
        
        # Get location data from request
        latitude = serializer.validated_data.get('latitude')
        longitude = serializer.validated_data.get('longitude')
        address = serializer.validated_data.get('address', '')
        
        try:
            attendance = Attendance.objects.get(user=user, date=today)
        except Attendance.DoesNotExist:
            return Response(
                {'error': 'No check-in record found for today'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if attendance.check_out_time:
            return Response(
                {'error': 'Already checked out today'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        attendance.check_out_time = current_time
        attendance.check_out_latitude = latitude
        attendance.check_out_longitude = longitude
        attendance.check_out_address = address
        if serializer.validated_data.get('notes'):
            attendance.notes = serializer.validated_data.get('notes')
        attendance.save()  # This will trigger calculate_hours and determine_status
        
        response_serializer = AttendanceSerializer(attendance)
        return Response({
            'message': 'Successfully checked out',
            'attendance': response_serializer.data
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='today')
    def today_status(self, request):
        """
        Get today's attendance status with formatted times in IST
        """
        user = request.user
        today = timezone.now().date()
        
        try:
            attendance = Attendance.objects.get(user=user, date=today)
            
            # Format times in IST
            ist = pytz.timezone('Asia/Kolkata')
            check_in_formatted = None
            check_out_formatted = None
            
            if attendance.check_in_time:
                check_in_ist = attendance.check_in_time.astimezone(ist)
                check_in_formatted = check_in_ist.strftime('%I:%M %p')
            
            if attendance.check_out_time:
                check_out_ist = attendance.check_out_time.astimezone(ist)
                check_out_formatted = check_out_ist.strftime('%I:%M %p')
            
            data = {
                'has_checked_in': attendance.check_in_time is not None,
                'has_checked_out': attendance.check_out_time is not None,
                'check_in_time': attendance.check_in_time,
                'check_out_time': attendance.check_out_time,
                'check_in_time_formatted': check_in_formatted,
                'check_out_time_formatted': check_out_formatted,
                'total_hours': attendance.total_hours,
                'status': attendance.status,
                'date': attendance.date,
                'late_request': attendance.late_request,
                'late_request_status': attendance.late_request_status,
                'check_in_latitude': float(attendance.check_in_latitude) if attendance.check_in_latitude else None,
                'check_in_longitude': float(attendance.check_in_longitude) if attendance.check_in_longitude else None,
                'check_in_address': attendance.check_in_address,
                'check_out_latitude': float(attendance.check_out_latitude) if attendance.check_out_latitude else None,
                'check_out_longitude': float(attendance.check_out_longitude) if attendance.check_out_longitude else None,
                'check_out_address': attendance.check_out_address,
            }
        except Attendance.DoesNotExist:
            data = {
                'has_checked_in': False,
                'has_checked_out': False,
                'check_in_time': None,
                'check_out_time': None,
                'check_in_time_formatted': None,
                'check_out_time_formatted': None,
                'total_hours': 0.00,
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
        
        # Don't use TodayAttendanceSerializer, return dict directly
        return Response(data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['post'], url_path='request-late')
    def request_late(self, request):
        """
        Submit a late request for approval
        """
        serializer = LateRequestSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        user = request.user
        request_date = serializer.validated_data.get('request_date')
        reason = serializer.validated_data.get('reason')
        
        # Get the attendance record
        attendance = Attendance.objects.get(user=user, date=request_date)
        
        # Update late request fields
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
        """
        Approve or reject a late request (Admin only)
        """
        serializer = LateApprovalSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        attendance = self.get_object()
        action_type = serializer.validated_data.get('action')
        
        if not attendance.late_request:
            return Response(
                {'error': 'No late request found for this attendance record'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if action_type == 'approve':
            attendance.late_request_status = 'approved'
            attendance.late_approved_by = request.user
            attendance.late_approved_at = timezone.now()
            attendance.save()  # This will update status to 'late'
            message = 'Late request approved successfully'
        else:
            attendance.late_request_status = 'rejected'
            attendance.save()
            message = 'Late request rejected'
        
        response_serializer = AttendanceSerializer(attendance)
        return Response({
            'message': message,
            'attendance': response_serializer.data
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='pending-late-requests')
    def pending_late_requests(self, request):
        """
        Get all pending late requests (Admin only)
        """
        if not request.user.is_staff:
            return Response(
                {'error': 'Only admins can view pending late requests'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        pending_requests = Attendance.objects.filter(
            late_request=True,
            late_request_status='pending'
        ).order_by('-date')
        
        serializer = AttendanceSerializer(pending_requests, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='monthly-stats')
    def monthly_stats(self, request):
        """
        Get monthly attendance statistics
        """
        user = request.user
        
        # Get month and year from query params or use current
        year = int(request.query_params.get('year', timezone.now().year))
        month = int(request.query_params.get('month', timezone.now().month))
        
        # Get first and last day of the month
        first_day = datetime(year, month, 1).date()
        last_day = datetime(year, month, monthrange(year, month)[1]).date()
        
        # Query attendance for the month
        attendances = Attendance.objects.filter(
            user=user,
            date__gte=first_day,
            date__lte=last_day
        )
        
        # Calculate statistics
        present_count = attendances.filter(status='present').count()
        absent_count = attendances.filter(status='absent').count()
        late_count = attendances.filter(status='late').count()
        half_day_count = attendances.filter(status='half_day').count()
        
        total_hours = attendances.aggregate(Sum('total_hours'))['total_hours__sum'] or 0
        avg_hours = attendances.aggregate(Avg('total_hours'))['total_hours__avg'] or 0
        
        # Total working days in month
        total_days = attendances.count()
        if total_days == 0:
            # If no records, count working days in month
            current_date = first_day
            total_days = 0
            while current_date <= last_day:
                if current_date.weekday() < 5:  # Monday to Friday
                    total_days += 1
                current_date += timedelta(days=1)
        
        data = {
            'present': present_count,
            'absent': absent_count,
            'late': late_count,
            'half_day': half_day_count,
            'total_days': total_days,
            'total_hours': round(total_hours, 2),
            'average_hours': round(avg_hours, 2),
        }
        
        serializer = MonthlyStatsSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='history')
    def attendance_history(self, request):
        """
        Get attendance history with pagination
        """
        user = request.user
        
        # Get date range from query params
        days = int(request.query_params.get('days', 30))  # Default last 30 days
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days)
        
        attendances = Attendance.objects.filter(
            user=user,
            date__gte=start_date,
            date__lte=end_date
        ).order_by('-date')
        
        serializer = AttendanceSerializer(attendances, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AttendanceSettingsViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing attendance settings
    Only admins can modify settings
    """
    queryset = AttendanceSettings.objects.all()
    serializer_class = AttendanceSettingsSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_permissions(self):
        """
        Allow GET for all authenticated users
        Only admins can create/update/delete
        """
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]
    
    @action(detail=False, methods=['get'], url_path='current')
    def current_settings(self, request):
        """
        Get current attendance settings
        """
        settings = AttendanceSettings.objects.first()
        if not settings:
            # Create default settings if none exist
            settings = AttendanceSettings.objects.create()
        
        serializer = self.get_serializer(settings)
        return Response(serializer.data, status=status.HTTP_200_OK)