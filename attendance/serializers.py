from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Attendance, AttendanceSettings
from django.utils import timezone
from datetime import datetime, timedelta
import pytz

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model"""
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name']


class AttendanceSerializer(serializers.ModelSerializer):
    """Serializer for Attendance model"""
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    user_username = serializers.CharField(source='user.username', read_only=True)
    check_in_time_formatted = serializers.SerializerMethodField()
    check_out_time_formatted = serializers.SerializerMethodField()
    date_formatted = serializers.SerializerMethodField()
    late_approved_by_name = serializers.CharField(source='late_approved_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = Attendance
        fields = [
            'id', 'user', 'user_name', 'user_username', 'date', 'date_formatted',
            'check_in_time', 'check_in_time_formatted', 
            'check_out_time', 'check_out_time_formatted',
            'status', 'total_hours', 'notes', 
            'late_request', 'late_request_reason', 'late_request_status',
            'late_approved_by', 'late_approved_by_name', 'late_approved_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['total_hours', 'status', 'created_at', 'updated_at', 
                           'late_approved_by', 'late_approved_at']
    
    def get_check_in_time_formatted(self, obj):
        """Convert UTC time to IST and format for display"""
        if obj.check_in_time:
            # Convert UTC to IST (Asia/Kolkata)
            ist = pytz.timezone('Asia/Kolkata')
            local_time = obj.check_in_time.astimezone(ist)
            return local_time.strftime('%I:%M %p')
        return None
    
    def get_check_out_time_formatted(self, obj):
        """Convert UTC time to IST and format for display"""
        if obj.check_out_time:
            # Convert UTC to IST (Asia/Kolkata)
            ist = pytz.timezone('Asia/Kolkata')
            local_time = obj.check_out_time.astimezone(ist)
            return local_time.strftime('%I:%M %p')
        return None
    
    def get_date_formatted(self, obj):
        return obj.date.strftime('%Y-%m-%d')


class CheckInSerializer(serializers.Serializer):
    """Serializer for check-in action"""
    notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        user = self.context['request'].user
        today = timezone.now().date()
        
        # Check if already checked in today
        existing = Attendance.objects.filter(user=user, date=today).first()
        if existing and existing.check_in_time:
            raise serializers.ValidationError("You have already checked in today.")
        
        return data


class CheckOutSerializer(serializers.Serializer):
    """Serializer for check-out action"""
    notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        user = self.context['request'].user
        today = timezone.now().date()
        
        # Check if attendance record exists
        attendance = Attendance.objects.filter(user=user, date=today).first()
        if not attendance:
            raise serializers.ValidationError("You haven't checked in yet.")
        
        if not attendance.check_in_time:
            raise serializers.ValidationError("You haven't checked in yet.")
        
        if attendance.check_out_time:
            raise serializers.ValidationError("You have already checked out today.")
        
        return data


class LateRequestSerializer(serializers.Serializer):
    """Serializer for submitting late request"""
    reason = serializers.CharField(required=True, allow_blank=False)
    date = serializers.DateField(required=False)
    
    def validate(self, data):
        user = self.context['request'].user
        request_date = data.get('date', timezone.now().date())
        
        # Check if attendance record exists for that date
        try:
            attendance = Attendance.objects.get(user=user, date=request_date)
        except Attendance.DoesNotExist:
            raise serializers.ValidationError("No attendance record found for this date.")
        
        # Check if already has a late request
        if attendance.late_request and attendance.late_request_status == 'pending':
            raise serializers.ValidationError("You already have a pending late request for this date.")
        
        if attendance.late_request and attendance.late_request_status == 'approved':
            raise serializers.ValidationError("Late request already approved for this date.")
        
        data['request_date'] = request_date
        return data


class LateApprovalSerializer(serializers.Serializer):
    """Serializer for approving/rejecting late requests"""
    action = serializers.ChoiceField(choices=['approve', 'reject'])
    
    def validate(self, data):
        # Ensure the user is admin
        if not self.context['request'].user.is_staff:
            raise serializers.ValidationError("Only admins can approve/reject late requests.")
        
        return data


class MonthlyStatsSerializer(serializers.Serializer):
    """Serializer for monthly attendance statistics"""
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    late = serializers.IntegerField()
    half_day = serializers.IntegerField()
    total_days = serializers.IntegerField()
    total_hours = serializers.DecimalField(max_digits=6, decimal_places=2)
    average_hours = serializers.DecimalField(max_digits=5, decimal_places=2)


class TodayAttendanceSerializer(serializers.Serializer):
    """Serializer for today's attendance status"""
    has_checked_in = serializers.BooleanField()
    has_checked_out = serializers.BooleanField()
    check_in_time = serializers.DateTimeField(allow_null=True)
    check_out_time = serializers.DateTimeField(allow_null=True)
    total_hours = serializers.DecimalField(max_digits=5, decimal_places=2)
    status = serializers.CharField()
    date = serializers.DateField()
    late_request = serializers.BooleanField()
    late_request_status = serializers.CharField(allow_null=True)
    
    # Add formatted time fields
    check_in_time_formatted = serializers.SerializerMethodField()
    check_out_time_formatted = serializers.SerializerMethodField()
    
    def get_check_in_time_formatted(self, obj):
        """Convert UTC time to IST and format for display"""
        check_in = obj.get('check_in_time')
        if check_in:
            # If it's already a datetime object
            if isinstance(check_in, datetime):
                ist = pytz.timezone('Asia/Kolkata')
                local_time = check_in.astimezone(ist)
                return local_time.strftime('%I:%M %p')
        return None
    
    def get_check_out_time_formatted(self, obj):
        """Convert UTC time to IST and format for display"""
        check_out = obj.get('check_out_time')
        if check_out:
            # If it's already a datetime object
            if isinstance(check_out, datetime):
                ist = pytz.timezone('Asia/Kolkata')
                local_time = check_out.astimezone(ist)
                return local_time.strftime('%I:%M %p')
        return None


class AttendanceSettingsSerializer(serializers.ModelSerializer):
    """Serializer for Attendance Settings"""
    class Meta:
        model = AttendanceSettings
        fields = [
            'id', 'office_start_time', 'office_end_time', 
            'grace_period_minutes', 'minimum_hours_full_day', 
            'minimum_hours_half_day', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']