# master/serializers.py
from rest_framework import serializers
from .models import LeaveType, Allowance, Deduction, Holiday, Announcement


class AnnouncementSerializer(serializers.ModelSerializer):
    """Serializer for Announcement model"""
    tag_display = serializers.CharField(source='get_tag_display', read_only=True)

    class Meta:
        model = Announcement
        fields = [
            'id', 'title', 'body', 'date', 'tag', 'tag_display',
            'icon', 'is_pinned', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'tag_display']


class HolidaySerializer(serializers.ModelSerializer):
    """Serializer for Holiday model"""
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    days_until   = serializers.SerializerMethodField()

    class Meta:
        model = Holiday
        fields = [
            'id', 'name', 'date', 'type', 'type_display',
            'description', 'is_active', 'days_until',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'type_display', 'days_until']

    def get_days_until(self, obj):
        from django.utils import timezone
        today = timezone.now().date()
        delta = (obj.date - today).days
        return delta





class LeaveTypeSerializer(serializers.ModelSerializer):
    """
    Serializer for LeaveType model
    """
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    payment_status_display = serializers.CharField(source='get_payment_status_display', read_only=True)
    
    class Meta:
        model = LeaveType
        fields = [
            'id',
            'name',
            'date',
            'category',
            'category_display',
            'payment_status',
            'payment_status_display',
            'description',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'category_display', 'payment_status_display']

class AllowanceSerializer(serializers.ModelSerializer):
    """Serializer for Allowance model"""
    employee_name = serializers.CharField(read_only=True)
    month_display = serializers.CharField(source='get_month_display', read_only=True)
    employee_details = serializers.SerializerMethodField()
    
    class Meta:
        model = Allowance
        fields = [
            'id',
            'employee',
            'employee_name',
            'employee_details',
            'allowance_name',
            'year',
            'month',
            'month_display',
            'amount',
            'description',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'employee_name', 'month_display']
    
    def get_employee_details(self, obj):
        return {
            'id': obj.employee.id,
            'first_name': obj.employee.first_name,
            'last_name': obj.employee.last_name,
            'email': obj.employee.email,
            'employee_id': obj.employee.employee_id,
        }


class DeductionSerializer(serializers.ModelSerializer):
    """Serializer for Deduction model"""
    employee_name = serializers.CharField(read_only=True)
    month_display = serializers.CharField(source='get_month_display', read_only=True)
    employee_details = serializers.SerializerMethodField()
    
    class Meta:
        model = Deduction
        fields = [
            'id',
            'employee',
            'employee_name',
            'employee_details',
            'deduction_name',
            'year',
            'month',
            'month_display',
            'amount',
            'description',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'employee_name', 'month_display']
    
    def get_employee_details(self, obj):
        return {
            'id': obj.employee.id,
            'first_name': obj.employee.first_name,
            'last_name': obj.employee.last_name,
            'email': obj.employee.email,
            'employee_id': obj.employee.employee_id,
        }