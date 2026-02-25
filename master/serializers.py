# master/serializers.py
from rest_framework import serializers
from .models import LeaveType, Allowance, Deduction


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

