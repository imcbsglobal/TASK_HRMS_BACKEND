from rest_framework import serializers

from employee_management.models import Employee
from .models import ExperienceCertificate


class ExperienceCertificateSerializer(serializers.ModelSerializer):
    employee_display = serializers.SerializerMethodField()
    department_name = serializers.CharField(source='employee.department.name', read_only=True)

    class Meta:
        model = ExperienceCertificate
        fields = [
            'id', 'employee', 'employee_display', 'department_name',
            'certificate_number', 'employee_name', 'employee_code',
            'designation', 'department', 'employment_type',
            'start_date', 'end_date', 'issue_date', 'conduct',
            'responsibilities', 'remarks', 'signatory_name',
            'signatory_designation', 'status', 'admin_owner',
            'issued_by', 'issued_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'certificate_number', 'employee_display', 'department_name',
            'employee_name', 'employee_code', 'admin_owner',
            'issued_by', 'issued_at', 'created_at', 'updated_at',
        ]
        extra_kwargs = {
            'employee_name': {'required': False},
            'start_date': {'required': False},
            'designation': {'required': False, 'allow_blank': True},
            'end_date': {'required': True},
        }

    def get_employee_display(self, obj):
        return f'{obj.employee.employee_id} - {obj.employee_name}'

    def validate_employee(self, employee):
        request = self.context.get('request')
        if not request or request.user.role == 'SUPER_ADMIN':
            return employee

        admin = request.user if request.user.role == 'ADMIN' else request.user.admin_owner
        if employee.admin_owner_id != getattr(admin, 'id', None):
            raise serializers.ValidationError('Employee is outside your company scope.')
        return employee

    def validate(self, attrs):
        employee = attrs.get('employee') or getattr(self.instance, 'employee', None)
        start_date = attrs.get('start_date') or getattr(self.instance, 'start_date', None)
        end_date = attrs.get('end_date') or getattr(self.instance, 'end_date', None)

        if employee and not start_date:
            attrs['start_date'] = employee.date_of_joining
            start_date = attrs['start_date']

        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError({
                'end_date': 'End date cannot be before start date.'
            })

        return attrs
