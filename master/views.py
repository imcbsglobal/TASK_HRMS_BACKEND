# master/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django.utils import timezone
from .models import LeaveType, Allowance, Deduction, Holiday, Announcement, JobTitle,PayrollPolicy,Section
from .serializers import (
    LeaveTypeSerializer, 
    AllowanceSerializer, 
    DeductionSerializer, 
    HolidaySerializer, 
    AnnouncementSerializer,
    JobTitleSerializer,
    PayrollPolicySerializer,
    SectionSerializer,
)
from activitylog.utils import ActivityLogMixin, log_activity


def _get_admin_owner(user):
    """
    Return the ADMIN who owns the current request's tenant scope.
    """
    if user.role == 'ADMIN':
        return user
    if user.role == 'USER':
        return user.admin_owner
    return None  # SUPER_ADMIN


class LeaveTypeViewSet(ActivityLogMixin, viewsets.ModelViewSet):
    """ViewSet for Leave Type CRUD operations"""
    activity_log_module = "Master"
    activity_log_object_name = "Leave Type"
    queryset = LeaveType.objects.all()
    serializer_class = LeaveTypeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'SUPER_ADMIN':
            queryset = LeaveType.objects.all()
        else:
            admin = _get_admin_owner(user)
            if admin is None:
                return LeaveType.objects.none()
            queryset = LeaveType.objects.filter(admin_owner=admin)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Search by name or description
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )
        return queryset

    def perform_create(self, serializer):
        admin = _get_admin_owner(self.request.user)
        serializer.save(admin_owner=admin)

    @action(detail=False, methods=['get'])
    def active(self, request):
        """Get only active leave types"""
        active_leave_types = self.get_queryset().filter(is_active=True)
        serializer = self.get_serializer(active_leave_types, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AllowanceViewSet(ActivityLogMixin, viewsets.ModelViewSet):
    """ViewSet for Allowance CRUD operations"""
    activity_log_module = "Master"
    activity_log_object_name = "Allowance"
    queryset = Allowance.objects.all()
    serializer_class = AllowanceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'SUPER_ADMIN':
            queryset = Allowance.objects.select_related('employee').all()
        else:
            admin = _get_admin_owner(user)
            if admin is None:
                return Allowance.objects.none()
            queryset = Allowance.objects.select_related('employee').filter(admin_owner=admin)
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee', None)
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by year/month
        for field in ['year', 'month']:
            val = self.request.query_params.get(field)
            if val:
                queryset = queryset.filter(**{field: val})
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Search
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(allowance_name__icontains=search) |
                Q(employee__first_name__icontains=search) |
                Q(employee__last_name__icontains=search) |
                Q(description__icontains=search)
            )
        return queryset

    def perform_create(self, serializer):
        admin = _get_admin_owner(self.request.user)
        serializer.save(admin_owner=admin)

    @action(detail=False, methods=['get'])
    def by_employee(self, request):
        """Get allowances for a specific employee"""
        employee_id = request.query_params.get('employee_id', None)
        if not employee_id:
            return Response({"error": "employee_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        allowances = self.get_queryset().filter(employee_id=employee_id)
        serializer = self.get_serializer(allowances, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class DeductionViewSet(ActivityLogMixin, viewsets.ModelViewSet):
    """ViewSet for Deduction CRUD operations"""
    activity_log_module = "Master"
    activity_log_object_name = "Deduction"
    queryset = Deduction.objects.all()
    serializer_class = DeductionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'SUPER_ADMIN':
            queryset = Deduction.objects.select_related('employee').all()
        else:
            admin = _get_admin_owner(user)
            if admin is None:
                return Deduction.objects.none()
            queryset = Deduction.objects.select_related('employee').filter(admin_owner=admin)
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee', None)
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by year/month
        for field in ['year', 'month']:
            val = self.request.query_params.get(field)
            if val:
                queryset = queryset.filter(**{field: val})
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Search
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(deduction_name__icontains=search) |
                Q(employee__first_name__icontains=search) |
                Q(employee__last_name__icontains=search) |
                Q(description__icontains=search)
            )
        return queryset

    def perform_create(self, serializer):
        admin = _get_admin_owner(self.request.user)
        serializer.save(admin_owner=admin)

    @action(detail=False, methods=['get'])
    def by_employee(self, request):
        """Get deductions for a specific employee"""
        employee_id = request.query_params.get('employee_id', None)
        if not employee_id:
            return Response({"error": "employee_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        deductions = self.get_queryset().filter(employee_id=employee_id)
        serializer = self.get_serializer(deductions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class HolidayViewSet(ActivityLogMixin, viewsets.ModelViewSet):
    """ViewSet for Holiday CRUD operations"""
    activity_log_module = "Master"
    activity_log_object_name = "Holiday"
    queryset = Holiday.objects.all()
    serializer_class = HolidaySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'SUPER_ADMIN':
            queryset = Holiday.objects.all()
        else:
            admin = _get_admin_owner(user)
            if admin is None:
                return Holiday.objects.none()
            queryset = Holiday.objects.filter(admin_owner=admin)

        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        type_filter = self.request.query_params.get('type', None)
        if type_filter:
            queryset = queryset.filter(type=type_filter)

        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )
        return queryset

    def perform_create(self, serializer):
        admin = _get_admin_owner(self.request.user)
        serializer.save(admin_owner=admin)

    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        """Return active holidays from today onward, ordered by date"""
        today = timezone.now().date()
        holidays = self.get_queryset().filter(is_active=True, date__gte=today).order_by('date')
        serializer = self.get_serializer(holidays, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AnnouncementViewSet(ActivityLogMixin, viewsets.ModelViewSet):
    """ViewSet for Announcement CRUD operations"""
    activity_log_module = "Master"
    activity_log_object_name = "Announcement"
    queryset = Announcement.objects.all()
    serializer_class = AnnouncementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from django.utils import timezone
        today = timezone.now().date()

        user = self.request.user
        if user.role == 'SUPER_ADMIN':
            queryset = Announcement.objects.all()
        else:
            admin = _get_admin_owner(user)
            if admin is None:
                return Announcement.objects.none()
            queryset = Announcement.objects.filter(admin_owner=admin)

        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        tag = self.request.query_params.get('tag', None)
        if tag:
            queryset = queryset.filter(tag=tag)

        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(body__icontains=search)
            )

        # Exclude announcements whose duration has elapsed.
        # Keep rows where duration_days is NULL (no expiry)
        # OR where date + duration_days >= today (not yet expired).
        # Using extra() for PostgreSQL-compatible date arithmetic:
        # (date + duration_days * INTERVAL '1 day') >= today
        from django.db.models.expressions import RawSQL
        queryset = queryset.filter(
            Q(duration_days__isnull=True) |
            Q(id__in=queryset.extra(
                where=["(master_announcements.date + (master_announcements.duration_days * INTERVAL '1 day')) >= %s"],
                params=[today]
            ).values('id'))
        )

        return queryset

    def perform_create(self, serializer):
        admin = _get_admin_owner(self.request.user)
        serializer.save(admin_owner=admin)

    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """Return latest 4 active, non-expired announcements for the Dashboard widget (pinned first)"""
        announcements = self.get_queryset().filter(is_active=True).order_by(
            '-is_pinned', '-date', '-created_at'
        )[:4]
        serializer = self.get_serializer(announcements, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class JobTitleViewSet(ActivityLogMixin, viewsets.ModelViewSet):
    """ViewSet for Job Title CRUD operations"""
    activity_log_module = "Master"
    activity_log_object_name = "Job Title"
    queryset = JobTitle.objects.all()
    serializer_class = JobTitleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'SUPER_ADMIN':
            queryset = JobTitle.objects.all()
        else:
            admin = _get_admin_owner(user)
            if admin is None:
                return JobTitle.objects.none()
            queryset = JobTitle.objects.filter(admin_owner=admin)

        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )
        return queryset

    def perform_create(self, serializer):
        admin = _get_admin_owner(self.request.user)
        serializer.save(admin_owner=admin)

    @action(detail=False, methods=['get'])
    def active(self, request):
        """Return only active job titles"""
        titles = self.get_queryset().filter(is_active=True)
        serializer = self.get_serializer(titles, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
# ─────────────────────────────────────────────────────────────────────────────
# ADD THIS to master/views.py
# Also add PayrollPolicy + PayrollPolicySerializer to the respective imports.
# ─────────────────────────────────────────────────────────────────────────────

class PayrollPolicyViewSet(viewsets.ViewSet):
    """
    Single-resource endpoint for the tenant's payroll policy.

    GET  /api/master/payroll-policy/current/  → returns the current policy
    PUT  /api/master/payroll-policy/current/  → full replace
    """
    permission_classes = [IsAuthenticated]

    # ── helpers ──────────────────────────────────────────────────────────────
    def _get_admin(self):
        return _get_admin_owner(self.request.user)

    def _get_or_create_policy(self, admin):
        """Return (instance, created) for the admin's policy row."""
        if admin is None:
            return None, False
        obj, created = PayrollPolicy.objects.get_or_create(
            admin_owner=admin,
            defaults={'policy_data': {}}
        )
        return obj, created

    # ── actions ───────────────────────────────────────────────────────────────
    @action(detail=False, methods=['get', 'put', 'patch'], url_path='current')
    def current(self, request):
        """
        GET  → return the stored policy (or empty dict if none saved yet).
        PUT  → replace the entire policy_data.
        PATCH→ merge only the provided keys into policy_data (shallow).
        """
        admin = self._get_admin()

        # SUPER_ADMIN has no single policy; return 403 for safety
        if admin is None and request.user.role != 'SUPER_ADMIN':
            return Response({'detail': 'No admin tenant found.'}, status=status.HTTP_403_FORBIDDEN)

        if request.method == 'GET':
            if admin is None:
                return Response({'policy_data': {}})
            obj, _ = self._get_or_create_policy(admin)
            serializer = PayrollPolicySerializer(obj)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # PUT — full replace
        if request.method == 'PUT':
            obj, _ = self._get_or_create_policy(admin)
            serializer = PayrollPolicySerializer(obj, data=request.data, partial=False)
            serializer.is_valid(raise_exception=True)
            serializer.save(admin_owner=admin)
            log_activity(
                user=request.user,
                action_type='UPDATE',
                module='Master',
                description='Updated Payroll Policy',
                request=request,
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        # PATCH — shallow merge
        if request.method == 'PATCH':
            obj, _ = self._get_or_create_policy(admin)
            # Merge incoming policy_data over stored policy_data
            incoming = request.data.get('policy_data', {})
            merged = {**obj.policy_data, **incoming}
            obj.policy_data = merged
            obj.save(update_fields=['policy_data', 'updated_at'])
            log_activity(
                user=request.user,
                action_type='UPDATE',
                module='Master',
                description='Updated Payroll Policy (partial)',
                request=request,
            )
            serializer = PayrollPolicySerializer(obj)
            return Response(serializer.data, status=status.HTTP_200_OK)
        
class SectionViewSet(ActivityLogMixin, viewsets.ModelViewSet):
    """ViewSet for Section CRUD operations"""
    activity_log_module = "Master"
    activity_log_object_name = "Section"
    queryset = Section.objects.all()
    serializer_class = SectionSerializer
    permission_classes = [IsAuthenticated]
 
    def get_queryset(self):
        user = self.request.user
        if user.role == 'SUPER_ADMIN':
            queryset = Section.objects.all()
        else:
            admin = _get_admin_owner(user)
            if admin is None:
                return Section.objects.none()
            queryset = Section.objects.filter(admin_owner=admin)
 
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
 
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )
        return queryset
 
    def perform_create(self, serializer):
        admin = _get_admin_owner(self.request.user)
        serializer.save(admin_owner=admin)
 
    @action(detail=False, methods=['get'])
    def active(self, request):
        """Return only active sections"""
        sections = self.get_queryset().filter(is_active=True)
        serializer = self.get_serializer(sections, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)