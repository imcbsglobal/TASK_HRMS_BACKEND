from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from HR.models import Candidate
from .models import Employee, Department, CustomFieldDefinition
from .serializers import (
    EmployeeSerializer,
    DepartmentSerializer,
    CustomFieldDefinitionSerializer,
)

User = get_user_model()

# Statuses that represent a fully offboarded employee
OFFBOARDED_STATUSES = {'terminated', 'resigned', 'retired', 'offboarded'}


# ---------------------------------------------------------------------------
# Tenant helpers
# ---------------------------------------------------------------------------

def _get_admin_owner(user):
    """
    Return the ADMIN who owns the current request's tenant scope.

    - SUPER_ADMIN : has no tenant scope of their own; returns None.
    - ADMIN       : they ARE the tenant root; returns themselves.
    - USER        : belongs to an admin's tenant; returns their admin_owner.
    """
    if user.role == 'ADMIN':
        return user
    if user.role == 'USER':
        return user.admin_owner
    return None  # SUPER_ADMIN — cross-tenant access handled per-view


def _employee_qs(user):
    """
    Return the base Employee queryset scoped to the requesting user's tenant.

    - SUPER_ADMIN → all employees across all tenants
    - ADMIN       → only employees owned by this admin
    - USER        → only employees owned by their admin_owner
    """
    if user.role == 'SUPER_ADMIN':
        return Employee.objects.select_related('department', 'candidate')

    admin = _get_admin_owner(user)
    if admin is None:
        return Employee.objects.none()

    return Employee.objects.select_related(
        'department', 'candidate'
    ).filter(admin_owner=admin)


def _department_qs(user):
    """Tenant-scoped Department queryset."""
    if user.role == 'SUPER_ADMIN':
        return Department.objects.all()

    admin = _get_admin_owner(user)
    if admin is None:
        return Department.objects.none()

    return Department.objects.filter(admin_owner=admin)


def _custom_field_qs(user):
    """Tenant-scoped CustomFieldDefinition queryset (active only)."""
    if user.role == 'SUPER_ADMIN':
        return CustomFieldDefinition.objects.filter(is_active=True)

    admin = _get_admin_owner(user)
    if admin is None:
        return CustomFieldDefinition.objects.none()

    return CustomFieldDefinition.objects.filter(
        admin_owner=admin, is_active=True
    )


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
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, candidate_id):
        try:
            candidate = Candidate.objects.get(id=candidate_id)
        except Candidate.DoesNotExist:
            return Response(
                {"error": "Candidate not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        name_parts = candidate.name.strip().split(" ", 1)
        return Response({
            "candidate_id": candidate.id,
            "first_name":   name_parts[0],
            "last_name":    name_parts[1] if len(name_parts) > 1 else "",
            "email":        candidate.email,
            "phone":        candidate.phone,
        })


# ---------------------------------------------------------------------------
# Employee List / Create
# ---------------------------------------------------------------------------
class EmployeeListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """
        SUPER_ADMIN → all employees
        ADMIN       → only their own employees
        USER        → only their admin's employees (read-only context)
        """
        employees  = _employee_qs(request.user)
        serializer = EmployeeSerializer(employees, many=True)
        return Response(serializer.data)

    def post(self, request):
        """
        Only ADMIN (and SUPER_ADMIN) may create employees.
        admin_owner is injected server-side; clients cannot supply it.
        """
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to create employees."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = EmployeeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Inject tenant owner
        admin = _get_admin_owner(request.user)  # None for SUPER_ADMIN
        serializer.save(admin_owner=admin)

        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Employee Detail (retrieve / update / delete)
# ---------------------------------------------------------------------------
class EmployeeDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_employee(self, request, pk):
        """
        Fetch employee by PK, enforcing tenant scope.
        Returns (employee, error_response).
        """
        try:
            employee = _employee_qs(request.user).get(pk=pk)
        except Employee.DoesNotExist:
            return None, Response(
                {"error": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return employee, None

    def get(self, request, pk):
        employee, err = self._get_employee(request, pk)
        if err:
            return err
        return Response(EmployeeSerializer(employee).data)

    def put(self, request, pk):
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to update employees."},
                status=status.HTTP_403_FORBIDDEN,
            )

        employee, err = self._get_employee(request, pk)
        if err:
            return err

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
        if request.user.role not in ('SUPER_ADMIN', 'ADMIN'):
            return Response(
                {"detail": "You do not have permission to delete employees."},
                status=status.HTTP_403_FORBIDDEN,
            )

        employee, err = self._get_employee(request, pk)
        if err:
            return err

        employee.delete()
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
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to offboard employees."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            employee = _employee_qs(request.user).select_related('candidate').get(pk=pk)
        except Employee.DoesNotExist:
            return Response(
                {"error": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

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
            "user_deactivated":    deactivated_user is not None,
            "deactivated_username": deactivated_user.username if deactivated_user else None,
        }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Department CRUD
# ---------------------------------------------------------------------------
class DepartmentListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        departments = _department_qs(request.user)
        serializer  = DepartmentSerializer(departments, many=True)
        return Response(serializer.data)

    def post(self, request):
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to create departments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = DepartmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        admin = _get_admin_owner(request.user)
        serializer.save(admin_owner=admin)

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class DepartmentDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_department(self, request, pk):
        try:
            dept = _department_qs(request.user).get(pk=pk)
        except Department.DoesNotExist:
            return None, Response(
                {"error": "Department not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return dept, None

    def get(self, request, pk):
        dept, err = self._get_department(request, pk)
        if err:
            return err
        return Response(DepartmentSerializer(dept).data)

    def put(self, request, pk):
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to update departments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        dept, err = self._get_department(request, pk)
        if err:
            return err

        serializer = DepartmentSerializer(dept, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if request.user.role not in ('SUPER_ADMIN', 'ADMIN'):
            return Response(
                {"detail": "You do not have permission to delete departments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        dept, err = self._get_department(request, pk)
        if err:
            return err

        if dept.employees.exists():
            return Response(
                {"error": "Cannot delete department with associated employees."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        dept.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Custom Field Definition CRUD
# ---------------------------------------------------------------------------
class CustomFieldDefinitionListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        fields     = _custom_field_qs(request.user)
        serializer = CustomFieldDefinitionSerializer(fields, many=True)
        return Response(serializer.data)

    def post(self, request):
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to create custom fields."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CustomFieldDefinitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        admin = _get_admin_owner(request.user)
        serializer.save(admin_owner=admin)

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CustomFieldDefinitionDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_field(self, request, pk):
        # Note: include inactive fields for detail operations (soft-delete awareness)
        qs = _custom_field_qs(request.user).filter(is_active__in=[True, False])
        try:
            field = qs.get(pk=pk)
        except CustomFieldDefinition.DoesNotExist:
            return None, Response(
                {"error": "Custom field not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return field, None

    def get(self, request, pk):
        field, err = self._get_field(request, pk)
        if err:
            return err
        return Response(CustomFieldDefinitionSerializer(field).data)

    def put(self, request, pk):
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to update custom fields."},
                status=status.HTTP_403_FORBIDDEN,
            )

        field, err = self._get_field(request, pk)
        if err:
            return err

        serializer = CustomFieldDefinitionSerializer(field, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if request.user.role not in ('SUPER_ADMIN', 'ADMIN'):
            return Response(
                {"detail": "You do not have permission to delete custom fields."},
                status=status.HTTP_403_FORBIDDEN,
            )

        field, err = self._get_field(request, pk)
        if err:
            return err

        # Soft delete
        field.is_active = False
        field.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Employee Assets
# ---------------------------------------------------------------------------
from .models import EmployeeAsset
from .serializers import EmployeeAssetSerializer


class EmployeeAssetListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_employee(self, request, employee_id):
        """Fetch employee enforcing tenant scope."""
        try:
            return _employee_qs(request.user).get(pk=employee_id), None
        except Employee.DoesNotExist:
            return None, Response(
                {"error": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

    def get(self, request, employee_id):
        _, err = self._get_employee(request, employee_id)
        if err:
            return err

        assets = EmployeeAsset.objects.filter(
            employee_id=employee_id
        ).order_by('-assigned_date')
        return Response(EmployeeAssetSerializer(assets, many=True).data)

    def post(self, request, employee_id):
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to assign assets."},
                status=status.HTTP_403_FORBIDDEN,
            )

        _, err = self._get_employee(request, employee_id)
        if err:
            return err

        data       = {**request.data, 'employee': employee_id}
        serializer = EmployeeAssetSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EmployeeAssetDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_asset(self, request, employee_id, pk):
        # First confirm the employee is in scope for this tenant
        try:
            _employee_qs(request.user).get(pk=employee_id)
        except Employee.DoesNotExist:
            return None, Response(
                {"error": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            asset = EmployeeAsset.objects.get(pk=pk, employee_id=employee_id)
        except EmployeeAsset.DoesNotExist:
            return None, Response(
                {"error": "Asset not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return asset, None

    def put(self, request, employee_id, pk):
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to update assets."},
                status=status.HTTP_403_FORBIDDEN,
            )

        asset, err = self._get_asset(request, employee_id, pk)
        if err:
            return err

        serializer = EmployeeAssetSerializer(asset, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, employee_id, pk):
        if request.user.role not in ('SUPER_ADMIN', 'ADMIN'):
            return Response(
                {"detail": "You do not have permission to delete assets."},
                status=status.HTTP_403_FORBIDDEN,
            )

        asset, err = self._get_asset(request, employee_id, pk)
        if err:
            return err

        asset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)