from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Attendance, AttendanceSettings, LeaveRequest, LateArrivalRequest
from django.utils import timezone
from datetime import datetime, timedelta
import pytz

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name']


class AttendanceSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    user_username = serializers.CharField(source='user.username', read_only=True)
    check_in_time_formatted = serializers.SerializerMethodField()
    check_out_time_formatted = serializers.SerializerMethodField()
    date_formatted = serializers.SerializerMethodField()
    late_approved_by_name = serializers.CharField(source='late_approved_by.get_full_name', read_only=True, allow_null=True)
    verified_by_name = serializers.CharField(source='verified_by.get_full_name', read_only=True, allow_null=True)
    check_in_map_url = serializers.SerializerMethodField()
    check_out_map_url = serializers.SerializerMethodField()
    # Leave type info – populated from approved LeaveRequest covering this attendance date
    leave_type = serializers.SerializerMethodField()
    leave_type_display = serializers.SerializerMethodField()
    
    class Meta:
        model = Attendance
        fields = [
            'id', 'user', 'user_name', 'user_username', 'date', 'date_formatted',
            'check_in_time', 'check_in_time_formatted', 
            'check_out_time', 'check_out_time_formatted',
            'status', 'total_hours', 'notes', 
            'is_verified', 'verified_by', 'verified_by_name', 'verified_at',
            'late_request', 'late_request_reason', 'late_request_status',
            'late_approved_by', 'late_approved_by_name', 'late_approved_at',
            'check_in_latitude', 'check_in_longitude', 'check_in_address', 'check_in_map_url',
            'check_out_latitude', 'check_out_longitude', 'check_out_address', 'check_out_map_url',
            'leave_type', 'leave_type_display',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['total_hours', 'created_at', 'updated_at', 
                           'late_approved_by', 'late_approved_at',
                           'verified_by', 'verified_at']
    
    def get_check_in_time_formatted(self, obj):
        if obj.check_in_time:
            ist = pytz.timezone('Asia/Kolkata')
            local_time = obj.check_in_time.astimezone(ist)
            return local_time.strftime('%I:%M %p')
        return None
    
    def get_check_out_time_formatted(self, obj):
        if obj.check_out_time:
            ist = pytz.timezone('Asia/Kolkata')
            local_time = obj.check_out_time.astimezone(ist)
            return local_time.strftime('%I:%M %p')
        return None
    
    def get_date_formatted(self, obj):
        return obj.date.strftime('%Y-%m-%d')
    
    def get_check_in_map_url(self, obj):
        return obj.get_check_in_map_url()
    
    def get_check_out_map_url(self, obj):
        return obj.get_check_out_map_url()

    def _get_leave_request(self, obj):
        """Return the approved LeaveRequest that covers this attendance date, if any."""
        if obj.status != 'leave':
            return None
        return LeaveRequest.objects.filter(
            user=obj.user,
            status='approved',
            start_date__lte=obj.date,
            end_date__gte=obj.date,
        ).first()

    def get_leave_type(self, obj):
        leave = self._get_leave_request(obj)
        return leave.leave_type if leave else None

    def get_leave_type_display(self, obj):
        leave = self._get_leave_request(obj)
        return leave.get_leave_type_display() if leave else None


class CheckInSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    
    def validate(self, data):
        user = self.context['request'].user
        today = timezone.now().date()
        existing = Attendance.objects.filter(user=user, date=today).first()
        if existing and existing.check_in_time:
            raise serializers.ValidationError("You have already checked in today.")
        return data


class CheckOutSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    
    def validate(self, data):
        user = self.context['request'].user
        today = timezone.now().date()
        attendance = Attendance.objects.filter(user=user, date=today).first()
        if not attendance:
            raise serializers.ValidationError("You haven't checked in yet.")
        if not attendance.check_in_time:
            raise serializers.ValidationError("You haven't checked in yet.")
        if attendance.check_out_time:
            raise serializers.ValidationError("You have already checked out today.")
        return data


class LateRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False)
    date = serializers.DateField(required=False)
    
    def validate(self, data):
        user = self.context['request'].user
        request_date = data.get('date', timezone.now().date())
        
        attendance = Attendance.objects.filter(user=user, date=request_date).first()
        if attendance:
            if attendance.late_request and attendance.late_request_status == 'pending':
                raise serializers.ValidationError("You already have a pending late request for this date.")
            if attendance.late_request and attendance.late_request_status == 'approved':
                raise serializers.ValidationError("Late request already approved for this date.")
        
        data['request_date'] = request_date
        return data


class LateApprovalSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['approve', 'reject'])
    
    def validate(self, data):
        if not self.context['request'].user.is_staff:
            raise serializers.ValidationError("Only admins can approve/reject late requests.")
        return data


class VerifyAttendanceSerializer(serializers.Serializer):
    STATUS_CHOICES = ['present', 'absent', 'half_day', 'late', 'leave']
    status = serializers.ChoiceField(choices=STATUS_CHOICES)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        user = self.context['request'].user
        if not (user.is_staff or user.is_superuser):
            role = getattr(user, 'role', None)
            if role not in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']:
                raise serializers.ValidationError("Only admins can verify attendance records.")
        return data


# ─────────────────────────────────────────────────────────────────────────────
# LATE ARRIVAL REQUEST SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class LateArrivalRequestSerializer(serializers.ModelSerializer):
    """Full read serializer – used in list/detail views."""
    user_name     = serializers.CharField(source='user.get_full_name', read_only=True)
    user_username = serializers.CharField(source='user.username',      read_only=True)
    reviewed_by_name = serializers.CharField(
        source='reviewed_by.get_full_name', read_only=True, allow_null=True
    )
    date_formatted = serializers.SerializerMethodField()
    arrival_time_formatted = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = LateArrivalRequest
        fields = [
            'id', 'user', 'user_name', 'user_username',
            'date', 'date_formatted',
            'expected_arrival_time', 'arrival_time_formatted',
            'reason', 'status', 'status_display',
            'reviewed_by', 'reviewed_by_name', 'reviewed_at', 'admin_notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'user', 'status', 'reviewed_by', 'reviewed_at', 'created_at', 'updated_at',
        ]

    def get_date_formatted(self, obj):
        return obj.date.strftime('%d %b %Y') if obj.date else None

    def get_arrival_time_formatted(self, obj):
        if obj.expected_arrival_time:
            # Convert time to 12-hour format
            t = obj.expected_arrival_time
            hour = t.hour
            minute = t.minute
            am_pm = 'AM' if hour < 12 else 'PM'
            hour_12 = hour % 12 or 12
            return f"{hour_12:02d}:{minute:02d} {am_pm}"
        return None


class CreateLateArrivalRequestSerializer(serializers.ModelSerializer):
    """Serializer for creating a new late arrival request."""

    class Meta:
        model  = LateArrivalRequest
        fields = ['date', 'expected_arrival_time', 'reason']

    def validate_date(self, value):
        # Allow past dates (retroactive), today, or near-future dates
        return value

    def validate(self, data):
        user = self.context['request'].user
        date = data.get('date')

        # Check for an existing request on the same date
        existing = LateArrivalRequest.objects.filter(user=user, date=date).first()
        if existing:
            if existing.status == 'pending':
                raise serializers.ValidationError(
                    "You already have a pending late arrival request for this date."
                )
            if existing.status == 'approved':
                raise serializers.ValidationError(
                    "A late arrival request for this date has already been approved."
                )
            # If cancelled/rejected, allow re-submission by deleting old record
            existing.delete()

        return data


class LateArrivalApprovalSerializer(serializers.Serializer):
    """Admin approves or rejects a late arrival request."""
    action      = serializers.ChoiceField(choices=['approve', 'reject'])
    admin_notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        user = self.context['request'].user
        is_admin = (
            user.is_staff or user.is_superuser or
            getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']
        )
        if not is_admin:
            raise serializers.ValidationError(
                "Only admins can approve/reject late arrival requests."
            )
        return data


# ─────────────────────────────────────────────────────────────────────────────
# LEAVE REQUEST SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class LeaveRequestSerializer(serializers.ModelSerializer):
    """Full serializer for LeaveRequest - used for list/detail views"""
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    user_username = serializers.CharField(source='user.username', read_only=True)
    reviewed_by_name = serializers.CharField(source='reviewed_by.get_full_name', read_only=True, allow_null=True)
    total_days = serializers.ReadOnlyField()
    leave_type_display = serializers.CharField(source='get_leave_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    start_date_formatted = serializers.SerializerMethodField()
    end_date_formatted = serializers.SerializerMethodField()
    
    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'user', 'user_name', 'user_username',
            'leave_type', 'leave_type_display',
            'start_date', 'start_date_formatted',
            'end_date', 'end_date_formatted',
            'reason', 'status', 'status_display',
            'total_days',
            'reviewed_by', 'reviewed_by_name', 'reviewed_at', 'admin_notes',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['user', 'status', 'reviewed_by', 'reviewed_at', 'created_at', 'updated_at']
    
    def get_start_date_formatted(self, obj):
        return obj.start_date.strftime('%d %b %Y') if obj.start_date else None
    
    def get_end_date_formatted(self, obj):
        return obj.end_date.strftime('%d %b %Y') if obj.end_date else None


class CreateLeaveRequestSerializer(serializers.ModelSerializer):
    """Serializer for creating a new leave request"""
    class Meta:
        model = LeaveRequest
        fields = ['leave_type', 'start_date', 'end_date', 'reason']
    
    def validate(self, data):
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        if start_date and end_date:
            if end_date < start_date:
                raise serializers.ValidationError("End date cannot be before start date.")
        
        user = self.context['request'].user
        if start_date and end_date:
            overlapping = LeaveRequest.objects.filter(
                user=user,
                status__in=['pending', 'approved'],
                start_date__lte=end_date,
                end_date__gte=start_date
            )
            if self.instance:
                overlapping = overlapping.exclude(pk=self.instance.pk)
            if overlapping.exists():
                raise serializers.ValidationError(
                    "You already have a leave request for this date range."
                )
        
        return data


class LeaveApprovalSerializer(serializers.Serializer):
    """Serializer for admin approving/rejecting leave requests"""
    action = serializers.ChoiceField(choices=['approve', 'reject'])
    admin_notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        user = self.context['request'].user
        is_admin = (
            user.is_staff or user.is_superuser or
            getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin']
        )
        if not is_admin:
            raise serializers.ValidationError("Only admins can approve/reject leave requests.")
        return data


class MonthlyStatsSerializer(serializers.Serializer):
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    late = serializers.IntegerField()
    half_day = serializers.IntegerField()
    leave = serializers.IntegerField()
    total_days = serializers.IntegerField()
    total_hours = serializers.DecimalField(max_digits=6, decimal_places=2)
    average_hours = serializers.DecimalField(max_digits=5, decimal_places=2)


class TodayAttendanceSerializer(serializers.Serializer):
    has_checked_in = serializers.BooleanField()
    has_checked_out = serializers.BooleanField()
    check_in_time = serializers.DateTimeField(allow_null=True)
    check_out_time = serializers.DateTimeField(allow_null=True)
    total_hours = serializers.DecimalField(max_digits=5, decimal_places=2)
    status = serializers.CharField()
    date = serializers.DateField()
    late_request = serializers.BooleanField()
    late_request_status = serializers.CharField(allow_null=True)
    check_in_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, allow_null=True)
    check_in_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, allow_null=True)
    check_in_address = serializers.CharField(allow_null=True)
    check_out_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, allow_null=True)
    check_out_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, allow_null=True)
    check_out_address = serializers.CharField(allow_null=True)
    check_in_time_formatted = serializers.SerializerMethodField()
    check_out_time_formatted = serializers.SerializerMethodField()
    
    def get_check_in_time_formatted(self, obj):
        check_in = obj.get('check_in_time')
        if check_in and isinstance(check_in, datetime):
            ist = pytz.timezone('Asia/Kolkata')
            local_time = check_in.astimezone(ist)
            return local_time.strftime('%I:%M %p')
        return None
    
    def get_check_out_time_formatted(self, obj):
        check_out = obj.get('check_out_time')
        if check_out and isinstance(check_out, datetime):
            ist = pytz.timezone('Asia/Kolkata')
            local_time = check_out.astimezone(ist)
            return local_time.strftime('%I:%M %p')
        return None


class AttendanceSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceSettings
        fields = [
            'id', 'office_start_time', 'office_end_time', 
            'grace_period_minutes', 'minimum_hours_full_day', 
            'minimum_hours_half_day', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']