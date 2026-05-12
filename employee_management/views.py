from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from HR.models import Candidate
from .models import Employee, Department, CustomFieldDefinition, SalaryIncrementHistory
from .serializers import (
    EmployeeSerializer,
    DepartmentSerializer,
    CustomFieldDefinitionSerializer,
    SalaryIncrementHistorySerializer,
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

    return CustomFieldDefinition.objects.filter(admin_owner=admin, is_active=True)


def _deactivate_linked_user(employee):
    """
    Deactivate the system User account linked to this employee (if any).

    Priority:
      1. employee.candidate.user  (cleanest link)
      2. User whose email matches employee.email

    Returns the deactivated User, or None.
    """
    linked_user = None

    # 1. Via candidate → user link
    if (
        hasattr(employee, 'candidate')
        and employee.candidate
        and hasattr(employee.candidate, 'user')
        and employee.candidate.user
    ):
        linked_user = employee.candidate.user

    # 2. Fallback: match by email
    if linked_user is None and employee.email:
        linked_user = User.objects.filter(email=employee.email).first()

    if linked_user and linked_user.is_active:
        linked_user.is_active = False
        linked_user.save(update_fields=['is_active'])
        return linked_user

    return None


# ---------------------------------------------------------------------------
# Employee List / Create
# ---------------------------------------------------------------------------
class EmployeeListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        employees = _employee_qs(request.user)
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
        old_salary = employee.salary
        old_last_increment_date = employee.last_increment_date
        old_increment_cycle_months = employee.increment_cycle_months
        old_next_increment_date = employee.next_increment_date
        serializer = EmployeeSerializer(employee, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            updated_employee = serializer.save()

            salary_increased = updated_employee.salary > old_salary
            increment_schedule_changed = any([
                updated_employee.last_increment_date != old_last_increment_date,
                updated_employee.increment_cycle_months != old_increment_cycle_months,
                updated_employee.next_increment_date != old_next_increment_date,
            ])

            if salary_increased or increment_schedule_changed:
                increment_amount = (
                    updated_employee.salary - old_salary
                    if salary_increased else Decimal('0.00')
                )
                increment_percentage = Decimal('0.00')
                if salary_increased and old_salary:
                    increment_percentage = (
                        (increment_amount / old_salary) * Decimal('100')
                    ).quantize(Decimal('0.01'))

                notes = request.data.get('increment_notes', '')
                if not notes and increment_schedule_changed and not salary_increased:
                    notes = 'Increment schedule updated'

                SalaryIncrementHistory.objects.create(
                    employee=updated_employee,
                    increment_date=updated_employee.last_increment_date or timezone.localdate(),
                    old_salary=old_salary,
                    new_salary=updated_employee.salary,
                    increment_amount=increment_amount,
                    increment_percentage=increment_percentage,
                    increment_cycle_months=updated_employee.increment_cycle_months,
                    next_increment_date=updated_employee.next_increment_date,
                    notes=notes,
                    created_by=request.user,
                )

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
# Salary Increment History  (GET list + POST create)
# ---------------------------------------------------------------------------
class SalaryIncrementHistoryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_employee(self, request, pk):
        try:
            employee = _employee_qs(request.user).get(pk=pk)
        except Employee.DoesNotExist:
            return None, Response(
                {"error": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return employee, None

    def get(self, request, pk):
        """GET /employee/employees/<pk>/salary-increments/ — list all increment logs."""
        employee, err = self._get_employee(request, pk)
        if err:
            return err

        logs = employee.salary_increment_logs.select_related(
            'employee', 'created_by'
        ).all()
        return Response(SalaryIncrementHistorySerializer(logs, many=True).data)

    def post(self, request, pk):
        """
        POST /employee/employees/<pk>/salary-increments/
        Add a new salary increment directly from the Increment Log page.

        Expected body:
          {
            "increment_date":       "2025-04-01",
            "new_salary":           75000,
            "increment_cycle_months": 12,   (optional)
            "next_increment_date":  "2026-04-01",  (optional)
            "notes":                "Annual revision"  (optional)
          }
        """
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to add salary increments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        employee, err = self._get_employee(request, pk)
        if err:
            return err

        new_salary_raw = request.data.get('new_salary')
        increment_date = request.data.get('increment_date') or str(timezone.localdate())

        if new_salary_raw is None:
            return Response(
                {"error": "new_salary is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            new_salary = Decimal(str(new_salary_raw))
        except Exception:
            return Response(
                {"error": "new_salary must be a valid number."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_salary = employee.salary or Decimal('0.00')
        increment_amount = new_salary - old_salary
        increment_percentage = Decimal('0.00')
        if old_salary and increment_amount != Decimal('0.00'):
            increment_percentage = (
                (increment_amount / old_salary) * Decimal('100')
            ).quantize(Decimal('0.01'))

        cycle = request.data.get('increment_cycle_months')
        next_date = request.data.get('next_increment_date') or None
        notes = request.data.get('notes', '')

        with transaction.atomic():
            # Update the employee's salary and increment tracking fields
            employee.salary = new_salary
            employee.last_increment_date = increment_date
            if cycle is not None:
                employee.increment_cycle_months = int(cycle) if cycle != '' else None
            if next_date:
                employee.next_increment_date = next_date
            employee.save(update_fields=[
                'salary', 'last_increment_date',
                'increment_cycle_months', 'next_increment_date', 'updated_at',
            ])

            log = SalaryIncrementHistory.objects.create(
                employee=employee,
                increment_date=increment_date,
                old_salary=old_salary,
                new_salary=new_salary,
                increment_amount=increment_amount,
                increment_percentage=increment_percentage,
                increment_cycle_months=int(cycle) if cycle not in (None, '') else None,
                next_increment_date=next_date or None,
                notes=notes,
                created_by=request.user,
            )

        return Response(
            SalaryIncrementHistorySerializer(log).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Salary Increment History Detail  (PUT update + DELETE)
# ---------------------------------------------------------------------------
class SalaryIncrementHistoryDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_log(self, request, pk, log_id):
        """Fetch the log record, ensuring it belongs to a tenant-visible employee."""
        try:
            employee = _employee_qs(request.user).get(pk=pk)
        except Employee.DoesNotExist:
            return None, None, Response(
                {"error": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            log = employee.salary_increment_logs.get(pk=log_id)
        except SalaryIncrementHistory.DoesNotExist:
            return None, None, Response(
                {"error": "Increment record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return employee, log, None

    def put(self, request, pk, log_id):
        """
        PUT /employee/employees/<pk>/salary-increments/<log_id>/
        Edit an existing increment log entry (notes, dates, cycle).
        Does NOT change employee salary — editing history only.
        """
        if request.user.role == 'USER':
            return Response(
                {"detail": "You do not have permission to edit salary increments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        employee, log, err = self._get_log(request, pk, log_id)
        if err:
            return err

        # Only allow editing of editable fields on the log record
        editable_fields = [
            'increment_date', 'new_salary', 'increment_cycle_months',
            'next_increment_date', 'notes',
        ]
        for field in editable_fields:
            if field in request.data:
                if field == 'new_salary':
                    try:
                        val = Decimal(str(request.data[field]))
                        log.new_salary = val
                        log.increment_amount = val - log.old_salary
                        if log.old_salary:
                            log.increment_percentage = (
                                (log.increment_amount / log.old_salary) * Decimal('100')
                            ).quantize(Decimal('0.01'))
                    except Exception:
                        return Response(
                            {"error": "new_salary must be a valid number."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                elif field == 'increment_cycle_months':
                    v = request.data[field]
                    log.increment_cycle_months = int(v) if v not in (None, '') else None
                else:
                    setattr(log, field, request.data[field] or None if field in ('next_increment_date',) else request.data[field])

        log.save()
        return Response(SalaryIncrementHistorySerializer(log).data)

    def delete(self, request, pk, log_id):
        """
        DELETE /employee/employees/<pk>/salary-increments/<log_id>/
        Remove an increment record.
        """
        if request.user.role not in ('SUPER_ADMIN', 'ADMIN'):
            return Response(
                {"detail": "You do not have permission to delete increment records."},
                status=status.HTTP_403_FORBIDDEN,
            )

        employee, log, err = self._get_log(request, pk, log_id)
        if err:
            return err

        log.delete()
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
        serializer = DepartmentSerializer(dept, data=request.data, partial=True)
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
        dept.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Custom Field Definition CRUD
# ---------------------------------------------------------------------------
class CustomFieldDefinitionListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        fields = _custom_field_qs(request.user).order_by('display_order', 'id')
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
        try:
            field = _custom_field_qs(request.user).get(pk=pk)
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
        field.is_active = False
        field.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Employee Asset CRUD
# ---------------------------------------------------------------------------
from .models import EmployeeAsset
from .serializers import EmployeeAssetSerializer


class EmployeeAssetListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_employee(self, request, employee_id):
        try:
            return _employee_qs(request.user).get(pk=employee_id), None
        except Employee.DoesNotExist:
            return None, Response({"error": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

    def get(self, request, employee_id):
        employee, err = self._get_employee(request, employee_id)
        if err:
            return err
        assets = employee.assets.all()
        return Response(EmployeeAssetSerializer(assets, many=True).data)

    def post(self, request, employee_id):
        employee, err = self._get_employee(request, employee_id)
        if err:
            return err
        data = {**request.data, 'employee': employee.id}
        serializer = EmployeeAssetSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EmployeeAssetDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_asset(self, request, employee_id, pk):
        try:
            employee = _employee_qs(request.user).get(pk=employee_id)
        except Employee.DoesNotExist:
            return None, Response({"error": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            asset = employee.assets.get(pk=pk)
        except EmployeeAsset.DoesNotExist:
            return None, Response({"error": "Asset not found."}, status=status.HTTP_404_NOT_FOUND)
        return asset, None

    def get(self, request, employee_id, pk):
        asset, err = self._get_asset(request, employee_id, pk)
        if err:
            return err
        return Response(EmployeeAssetSerializer(asset).data)

    def put(self, request, employee_id, pk):
        asset, err = self._get_asset(request, employee_id, pk)
        if err:
            return err
        serializer = EmployeeAssetSerializer(asset, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, employee_id, pk):
        asset, err = self._get_asset(request, employee_id, pk)
        if err:
            return err
        asset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Candidate → Employee conversion
# ---------------------------------------------------------------------------
class CandidateToEmployeeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, candidate_id):
        try:
            candidate = Candidate.objects.get(pk=candidate_id)
        except Candidate.DoesNotExist:
            return Response({"error": "Candidate not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "candidate_id": candidate.id,
            "first_name": candidate.name.split()[0] if candidate.name else '',
            "last_name": ' '.join(candidate.name.split()[1:]) if candidate.name and len(candidate.name.split()) > 1 else '',
            "email": candidate.email or '',
            "phone": candidate.phone or '',
        })


# ---------------------------------------------------------------------------
# Upcoming Increments  (dashboard widget)
# GET /employee/upcoming-increments/?days=30
# ---------------------------------------------------------------------------
class UpcomingIncrementsView(APIView):
    """
    Return employees with a next_increment_date within the next `days` days
    (default 30).  Ordered by next_increment_date ascending.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        days = int(request.query_params.get('days', 30))
        today    = timezone.localdate()
        deadline = today + timezone.timedelta(days=days)

        employees = (
            _employee_qs(request.user)
            .filter(status='active')
            .exclude(next_increment_date__isnull=True)
            .filter(next_increment_date__gte=today, next_increment_date__lte=deadline)
            .order_by('next_increment_date')
            .values(
                'id', 'employee_id', 'first_name', 'last_name',
                'position', 'salary', 'salary_currency',
                'last_increment_date', 'increment_cycle_months',
                'next_increment_date',
            )
        )

        data = []
        for emp in employees:
            days_until = (emp['next_increment_date'] - today).days
            data.append({
                **emp,
                'last_increment_date':  str(emp['last_increment_date']) if emp['last_increment_date'] else None,
                'increment_cycle_months': emp['increment_cycle_months'],
                'next_increment_date':  str(emp['next_increment_date']),
                'days_until_increment': days_until,
            })

        return Response(data)