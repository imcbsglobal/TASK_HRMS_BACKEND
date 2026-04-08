from rest_framework import serializers
from .models import Employee, Department, CustomFieldDefinition, EmployeeAsset


class DepartmentSerializer(serializers.ModelSerializer):
    """
    Serializer for Department model with employee count.
    admin_owner is write-only (set automatically in the view, never from client input).
    """
    employee_count = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ['id', 'name', 'description', 'employee_count', 'admin_owner']
        read_only_fields = ['employee_count']
        extra_kwargs = {
            'admin_owner': {'write_only': True, 'required': False},
        }

    def get_employee_count(self, obj):
        """Return the number of employees in this department (scoped to same tenant)."""
        return obj.employees.count()


class CustomFieldDefinitionSerializer(serializers.ModelSerializer):
    """
    Serializer for CustomFieldDefinition model.
    admin_owner is write-only (set automatically in the view).
    """
    options_list = serializers.SerializerMethodField()

    class Meta:
        model = CustomFieldDefinition
        fields = [
            'id', 'field_name', 'field_label', 'field_type',
            'field_options', 'options_list', 'is_required',
            'default_value', 'help_text', 'display_order',
            'is_active', 'created_at', 'updated_at', 'admin_owner',
        ]
        read_only_fields = ['created_at', 'updated_at']
        extra_kwargs = {
            'admin_owner': {'write_only': True, 'required': False},
        }

    def get_options_list(self, obj):
        """Return options as a list for easier frontend consumption."""
        return obj.get_options_list()

    def validate_field_name(self, value):
        """Ensure field_name has no spaces and is lowercase."""
        if ' ' in value:
            raise serializers.ValidationError(
                "Field name cannot contain spaces. Use underscores instead."
            )
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
            'employment_type', 'status', 'work_location',
            'date_of_birth', 'date_of_joining',
            'address', 'emergency_contact', 'emergency_contact_name',
            'emergency_contact_relationship',
            # Salary Details
            'salary', 'salary_currency',
            # Bank Details
            'bank_name', 'account_number', 'account_holder_name',
            'ifsc_code', 'branch_name', 'account_type',
            # PF Details
            'pf_enabled', 'pf_number', 'pf_contribution_type',
            'employee_pf_contribution', 'employer_pf_contribution',
            # Overtime Settings
            'overtime_enabled', 'overtime_rate_type', 'overtime_rate',
            'max_overtime_hours_per_month',
            # Shift Details
            'shift_type', 'shift_start_time', 'shift_end_time', 'weekly_off_days',
            # Custom Fields
            'custom_fields',
            # Tenant
            'admin_owner',
            # Timestamps
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'employee_id', 'created_at', 'updated_at',
            'department_name', 'candidate_id',
        ]
        extra_kwargs = {
            # admin_owner is injected by the view; clients must never supply it
            'admin_owner': {'write_only': True, 'required': False},
        }


class EmployeeAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeAsset
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']