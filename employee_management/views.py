from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from HR.models import Candidate
from .models import Employee, Department, CustomFieldDefinition
from .serializers import EmployeeSerializer, DepartmentSerializer, CustomFieldDefinitionSerializer

User = get_user_model()

# Statuses that represent a fully offboarded employee
OFFBOARDED_STATUSES = {'terminated', 'resigned', 'retired', 'offboarded'}


def _deactivate_linked_user(employee):
    """
    Deactivate the system User account linked to this employee.

    Lookup order:
      1. employee.candidate.user  (if HR Candidate has a user FK)
      2. User matched by employee email  (fallback)

    Returns the deactivated User instance, or None if no match was found.
    """
    linked_user = None

    # 1. Try via candidate → user FK
    try:
        if employee.candidate_id:
            candidate = employee.candidate
            if hasattr(candidate, 'user') and candidate.user is not None:
                linked_user = candidate.user
    except Exception:
        pass

    # 2. Fall back to email match
    if linked_user is None and employee.email:
        linked_user = User.objects.filter(email=employee.email).first()

    if linked_user and linked_user.is_active:
        linked_user.is_active = False
        linked_user.save(update_fields=['is_active'])

    return linked_user


# ---------------------------------------------------------------------------
# Candidate → Employee prefill
# ---------------------------------------------------------------------------
class CandidateToEmployeeView(APIView):
    def get(self, request, candidate_id):
        candidate = Candidate.objects.get(id=candidate_id)
        name_parts = candidate.name.strip().split(" ", 1)

        return Response({
            "candidate_id": candidate.id,
            "first_name": name_parts[0],
            "last_name": name_parts[1] if len(name_parts) > 1 else "",
            "email": candidate.email,
            "phone": candidate.phone,
        })


# ---------------------------------------------------------------------------
# Employee List / Create
# ---------------------------------------------------------------------------
class EmployeeListCreateView(APIView):
    def get(self, request):
        employees = Employee.objects.select_related("department", "candidate")
        serializer = EmployeeSerializer(employees, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = EmployeeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Employee Detail (update / delete)
# Auto-deactivates linked user when status changes to an offboarded value.
# ---------------------------------------------------------------------------
class EmployeeDetailView(APIView):
    def put(self, request, pk):
        employee = Employee.objects.get(pk=pk)
        old_status = employee.status

        serializer = EmployeeSerializer(employee, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated_employee = serializer.save()

        # Auto-deactivate linked User when transitioning into an offboarded state
        new_status = updated_employee.status
        if new_status in OFFBOARDED_STATUSES and old_status not in OFFBOARDED_STATUSES:
            _deactivate_linked_user(updated_employee)

        return Response(serializer.data)

    def delete(self, request, pk):
        Employee.objects.filter(pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Complete Offboarding  (dedicated endpoint)
# POST /employee/employees/<pk>/complete-offboarding/
# ---------------------------------------------------------------------------
class CompleteOffboardingView(APIView):
    """
    Atomically:
      1. Sets employee.status = 'terminated'
      2. Deactivates the linked system User account (via candidate.user or email match)

    Response includes the updated employee data plus:
      - user_deactivated: bool
      - deactivated_username: str | null
    """
    def post(self, request, pk):
        try:
            employee = Employee.objects.select_related('candidate').get(pk=pk)
        except Employee.DoesNotExist:
            return Response({"error": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)

        if employee.status in OFFBOARDED_STATUSES:
            return Response(
                {"error": f"Employee is already offboarded (status: {employee.status})"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Mark employee as terminated
        employee.status = 'terminated'
        employee.save(update_fields=['status', 'updated_at'])

        # Deactivate linked user
        deactivated_user = _deactivate_linked_user(employee)

        serializer = EmployeeSerializer(employee)
        return Response({
            **serializer.data,
            "user_deactivated": deactivated_user is not None,
            "deactivated_username": deactivated_user.username if deactivated_user else None,
        }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Department CRUD
# ---------------------------------------------------------------------------
class DepartmentListCreateView(APIView):
    def get(self, request):
        departments = Department.objects.all()
        serializer = DepartmentSerializer(departments, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = DepartmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class DepartmentDetailView(APIView):
    def get(self, request, pk):
        try:
            department = Department.objects.get(pk=pk)
            serializer = DepartmentSerializer(department)
            return Response(serializer.data)
        except Department.DoesNotExist:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)

    def put(self, request, pk):
        try:
            department = Department.objects.get(pk=pk)
            serializer = DepartmentSerializer(department, data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        except Department.DoesNotExist:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request, pk):
        try:
            department = Department.objects.get(pk=pk)
            if department.employees.exists():
                return Response(
                    {"error": "Cannot delete department with associated employees"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            department.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Department.DoesNotExist:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# Custom Field Definition CRUD
# ---------------------------------------------------------------------------
class CustomFieldDefinitionListCreateView(APIView):
    def get(self, request):
        fields = CustomFieldDefinition.objects.filter(is_active=True)
        serializer = CustomFieldDefinitionSerializer(fields, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CustomFieldDefinitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CustomFieldDefinitionDetailView(APIView):
    def get(self, request, pk):
        try:
            field = CustomFieldDefinition.objects.get(pk=pk)
            serializer = CustomFieldDefinitionSerializer(field)
            return Response(serializer.data)
        except CustomFieldDefinition.DoesNotExist:
            return Response({"error": "Custom field not found"}, status=status.HTTP_404_NOT_FOUND)

    def put(self, request, pk):
        try:
            field = CustomFieldDefinition.objects.get(pk=pk)
            serializer = CustomFieldDefinitionSerializer(field, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        except CustomFieldDefinition.DoesNotExist:
            return Response({"error": "Custom field not found"}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request, pk):
        try:
            field = CustomFieldDefinition.objects.get(pk=pk)
            field.is_active = False
            field.save()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except CustomFieldDefinition.DoesNotExist:
            return Response({"error": "Custom field not found"}, status=status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# Employee Assets
# ---------------------------------------------------------------------------
from .models import EmployeeAsset
from .serializers import EmployeeAssetSerializer


class EmployeeAssetListCreateView(APIView):
    def get(self, request, employee_id):
        assets = EmployeeAsset.objects.filter(employee_id=employee_id).order_by('-assigned_date')
        return Response(EmployeeAssetSerializer(assets, many=True).data)

    def post(self, request, employee_id):
        data = {**request.data, 'employee': employee_id}
        serializer = EmployeeAssetSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EmployeeAssetDetailView(APIView):
    def put(self, request, employee_id, pk):
        asset = EmployeeAsset.objects.get(pk=pk, employee_id=employee_id)
        serializer = EmployeeAssetSerializer(asset, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, employee_id, pk):
        EmployeeAsset.objects.filter(pk=pk, employee_id=employee_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)