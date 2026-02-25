from rest_framework import serializers
from .models import Payroll
from employee_management.models import Employee
from master.models import Allowance, Deduction


class PayrollSerializer(serializers.ModelSerializer):
    """Serializer for Payroll model"""
    
    employee_name = serializers.CharField(read_only=True)
    employee_id = serializers.CharField(source='employee.employee_id', read_only=True)
    month_display = serializers.CharField(read_only=True)
    processed_by_username = serializers.CharField(source='processed_by.username', read_only=True, allow_null=True)
    
    class Meta:
        model = Payroll
        fields = [
            'id',
            'employee',
            'employee_id',
            'employee_name',
            'year',
            'month',
            'month_display',
            'basic_salary',
            'total_allowances',
            'total_deductions',
            'net_salary',
            'total_days_in_month',
            'total_working_days',
            'status',
            'notes',
            'processed_by',
            'processed_by_username',
            'processed_at',
            'payment_date',
            'payment_reference',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'net_salary']


class PayrollDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer with allowances and deductions breakdown"""
    
    employee_name = serializers.CharField(read_only=True)
    employee_id = serializers.CharField(source='employee.employee_id', read_only=True)
    month_display = serializers.CharField(read_only=True)
    employee_email = serializers.EmailField(source='employee.email', read_only=True)
    employee_position = serializers.CharField(source='employee.position', read_only=True)
    employee_department = serializers.CharField(source='employee.department.name', read_only=True)
    employee_phone = serializers.CharField(source='employee.phone', read_only=True)
    
    allowances = serializers.SerializerMethodField()
    deductions = serializers.SerializerMethodField()
    
    class Meta:
        model = Payroll
        fields = [
            'id',
            'employee',
            'employee_id',
            'employee_name',
            'employee_email',
            'employee_position',
            'employee_department',
            'employee_phone',
            'year',
            'month',
            'month_display',
            'basic_salary',
            'total_allowances',
            'total_deductions',
            'net_salary',
            'total_days_in_month',
            'total_working_days',
            'allowances',
            'deductions',
            'status',
            'notes',
            'processed_by',
            'processed_at',
            'payment_date',
            'payment_reference',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'net_salary']
    
    def get_allowances(self, obj):
        """Get all allowances for this employee and month"""
        allowances = Allowance.objects.filter(
            employee=obj.employee,
            year=obj.year,
            month=obj.month,
            is_active=True
        )
        return [
            {
                'id': a.id,
                'name': a.allowance_name,
                'amount': str(a.amount),
                'description': a.description,
            }
            for a in allowances
        ]
    
    def get_deductions(self, obj):
        """Get all deductions for this employee and month"""
        deductions = Deduction.objects.filter(
            employee=obj.employee,
            year=obj.year,
            month=obj.month,
            is_active=True
        )
        return [
            {
                'id': d.id,
                'name': d.deduction_name,
                'amount': str(d.amount),
                'description': d.description,
            }
            for d in deductions
        ]


class PayrollCalculateSerializer(serializers.Serializer):
    """Serializer for payroll calculation request"""
    
    employee_id = serializers.IntegerField(required=True)
    year = serializers.IntegerField(required=True)
    month = serializers.IntegerField(required=True, min_value=1, max_value=12)
