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
        source="candidate.id", read_only=True
    )

    class Meta:
        model = Employee
        fields = "__all__"