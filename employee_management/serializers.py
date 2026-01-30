from rest_framework import serializers
from .models import Employee, Department, CustomFieldDefinition


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


class CustomFieldDefinitionSerializer(serializers.ModelSerializer):
    """
    Serializer for CustomFieldDefinition model
    """
    options_list = serializers.SerializerMethodField()
    
    class Meta:
        model = CustomFieldDefinition
        fields = [
            'id', 'field_name', 'field_label', 'field_type',
            'field_options', 'options_list', 'is_required',
            'default_value', 'help_text', 'display_order',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_options_list(self, obj):
        """Return options as a list for easier frontend consumption"""
        return obj.get_options_list()
    
    def validate_field_name(self, value):
        """Ensure field_name has no spaces and is lowercase"""
        if ' ' in value:
            raise serializers.ValidationError("Field name cannot contain spaces. Use underscores instead.")
        return value.lower().replace('-', '_')


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
            'first_name', 'last_name', 'email', 'phone', 'profile_image',
            'department', 'department_name', 'position', 
            'employment_type', 'status',
            'date_of_birth', 'date_of_joining',
            'address', 'emergency_contact', 'emergency_contact_name',
            'emergency_contact_relationship',
            # Salary Details
            'salary', 'salary_currency',
            # Bank Details
            'bank_name', 'account_number', 'account_holder_name',
            'ifsc_code', 'branch_name', 'account_type',
            # Custom Fields
            'custom_fields',
            # Timestamps
            'created_at', 'updated_at'
        ]
        read_only_fields = ['employee_id', 'created_at', 'updated_at', 'department_name', 'candidate_id']