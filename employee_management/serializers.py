from rest_framework import serializers
from .models import Employee, Department


class DepartmentSerializer(serializers.ModelSerializer):
    """
    Serializer for Department model with employee count
    """
    employee_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Department
        fields = ['id', 'name', 'description', 'employee_count']
        read_only_fields = ['employee_count']
    
    def get_employee_count(self, obj):
        """Return the number of employees in this department"""
        return obj.employees.count()


class EmployeeSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(
        source="department.name", read_only=True
    )
    candidate_id = serializers.IntegerField(
        source="candidate.id", read_only=True, allow_null=True
    )

    class Meta:
        model = Employee
        fields = [
            'id', 'employee_id', 'candidate', 'candidate_id',
            # Basic Details
            'first_name', 'last_name', 'email', 'phone',
            'department', 'department_name', 'position', 
            'employment_type', 'status',
            'date_of_birth', 'date_of_joining',
            'address', 'emergency_contact', 'emergency_contact_name',
            'emergency_contact_relationship',
            # Salary Details
            'salary', 'salary_currency', 'payment_frequency',
            'bonus_eligible', 'bonus_amount', 'allowances', 'deductions',
            # Bank Details
            'bank_name', 'account_number', 'account_holder_name',
            'ifsc_code', 'branch_name', 'account_type',
            # Timestamps
            'created_at', 'updated_at'
        ]
        read_only_fields = ['employee_id', 'created_at', 'updated_at', 'department_name', 'candidate_id']