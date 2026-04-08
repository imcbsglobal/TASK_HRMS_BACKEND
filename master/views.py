# master/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django.utils import timezone
from .models import LeaveType, Allowance, Deduction, Holiday, Announcement
from .serializers import (
    LeaveTypeSerializer, 
    AllowanceSerializer, 
    DeductionSerializer, 
    HolidaySerializer, 
    AnnouncementSerializer
)


def _get_admin_owner(user):
    """
    Return the ADMIN who owns the current request's tenant scope.
    """
    if user.role == 'ADMIN':
        return user
    if user.role == 'USER':
        return user.admin_owner
    return None  # SUPER_ADMIN


class LeaveTypeViewSet(viewsets.ModelViewSet):
    """ViewSet for Leave Type CRUD operations"""
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


class AllowanceViewSet(viewsets.ModelViewSet):
    """ViewSet for Allowance CRUD operations"""
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


class DeductionViewSet(viewsets.ModelViewSet):
    """ViewSet for Deduction CRUD operations"""
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


class HolidayViewSet(viewsets.ModelViewSet):
    """ViewSet for Holiday CRUD operations"""
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


class AnnouncementViewSet(viewsets.ModelViewSet):
    """ViewSet for Announcement CRUD operations"""
    queryset = Announcement.objects.all()
    serializer_class = AnnouncementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
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
        return queryset

    def perform_create(self, serializer):
        admin = _get_admin_owner(self.request.user)
        serializer.save(admin_owner=admin)

    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """Return latest 4 active announcements for the Dashboard widget (pinned first)"""
        announcements = self.get_queryset().filter(is_active=True).order_by(
            '-is_pinned', '-date', '-created_at'
        )[:4]
        serializer = self.get_serializer(announcements, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)