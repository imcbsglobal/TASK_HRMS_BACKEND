# master/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import LeaveType, Allowance, Deduction
from .serializers import LeaveTypeSerializer, AllowanceSerializer, DeductionSerializer


class LeaveTypeViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Leave Type CRUD operations
    
    Endpoints:
    - GET    /api/master/leave-types/          - List all leave types
    - POST   /api/master/leave-types/          - Create new leave type
    - GET    /api/master/leave-types/{id}/     - Retrieve specific leave type
    - PUT    /api/master/leave-types/{id}/     - Update leave type
    - PATCH  /api/master/leave-types/{id}/     - Partial update
    - DELETE /api/master/leave-types/{id}/     - Delete leave type
    """
    queryset = LeaveType.objects.all()
    serializer_class = LeaveTypeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Optionally filter leave types based on query parameters
        """
        queryset = LeaveType.objects.all()
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Search by name or description
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search)
            )
        
        return queryset

    def list(self, request, *args, **kwargs):
        """List all leave types"""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        """Create a new leave type"""
        serializer = self.get_serializer(data=request.data)
        
        if serializer.is_valid():
            serializer.save()
            return Response(
                serializer.data,
                status=status.HTTP_201_CREATED
            )
        
        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific leave type"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        """Update a leave type (PUT)"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=partial
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

    def partial_update(self, request, *args, **kwargs):
        """Partially update a leave type (PATCH)"""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Delete a leave type"""
        instance = self.get_object()
        instance.delete()
        return Response(
            {"detail": "Leave type deleted successfully"},
            status=status.HTTP_204_NO_CONTENT
        )

    @action(detail=False, methods=['get'])
    def active(self, request):
        """Get only active leave types"""
        active_leave_types = self.queryset.filter(is_active=True)
        serializer = self.get_serializer(active_leave_types, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class AllowanceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Allowance CRUD operations
    
    Endpoints:
    - GET    /api/master/allowances/          - List all allowances
    - POST   /api/master/allowances/          - Create new allowance
    - GET    /api/master/allowances/{id}/     - Retrieve specific allowance
    - PUT    /api/master/allowances/{id}/     - Update allowance
    - PATCH  /api/master/allowances/{id}/     - Partial update
    - DELETE /api/master/allowances/{id}/     - Delete allowance
    """
    queryset = Allowance.objects.select_related('employee').all()
    serializer_class = AllowanceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Optionally filter allowances based on query parameters"""
        queryset = Allowance.objects.select_related('employee').all()
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee', None)
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by year
        year = self.request.query_params.get('year', None)
        if year:
            queryset = queryset.filter(year=year)
        
        # Filter by month
        month = self.request.query_params.get('month', None)
        if month:
            queryset = queryset.filter(month=month)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Search by allowance name
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(allowance_name__icontains=search) |
                Q(employee__first_name__icontains=search) |
                Q(employee__last_name__icontains=search) |
                Q(description__icontains=search)
            )
        
        return queryset

    def list(self, request, *args, **kwargs):
        """List all allowances"""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        """Create a new allowance"""
        serializer = self.get_serializer(data=request.data)
        
        if serializer.is_valid():
            serializer.save()
            return Response(
                serializer.data,
                status=status.HTTP_201_CREATED
            )
        
        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific allowance"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        """Update an allowance (PUT)"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=partial
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

    def partial_update(self, request, *args, **kwargs):
        """Partially update an allowance (PATCH)"""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Delete an allowance"""
        instance = self.get_object()
        instance.delete()
        return Response(
            {"detail": "Allowance deleted successfully"},
            status=status.HTTP_204_NO_CONTENT
        )

    @action(detail=False, methods=['get'])
    def by_employee(self, request):
        """Get allowances for a specific employee"""
        employee_id = request.query_params.get('employee_id', None)
        if not employee_id:
            return Response(
                {"error": "employee_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        allowances = self.queryset.filter(employee_id=employee_id)
        serializer = self.get_serializer(allowances, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class DeductionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Deduction CRUD operations
    
    Endpoints:
    - GET    /api/master/deductions/          - List all deductions
    - POST   /api/master/deductions/          - Create new deduction
    - GET    /api/master/deductions/{id}/     - Retrieve specific deduction
    - PUT    /api/master/deductions/{id}/     - Update deduction
    - PATCH  /api/master/deductions/{id}/     - Partial update
    - DELETE /api/master/deductions/{id}/     - Delete deduction
    """
    queryset = Deduction.objects.select_related('employee').all()
    serializer_class = DeductionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Optionally filter deductions based on query parameters"""
        queryset = Deduction.objects.select_related('employee').all()
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee', None)
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by year
        year = self.request.query_params.get('year', None)
        if year:
            queryset = queryset.filter(year=year)
        
        # Filter by month
        month = self.request.query_params.get('month', None)
        if month:
            queryset = queryset.filter(month=month)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Search by deduction name
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(deduction_name__icontains=search) |
                Q(employee__first_name__icontains=search) |
                Q(employee__last_name__icontains=search) |
                Q(description__icontains=search)
            )
        
        return queryset

    def list(self, request, *args, **kwargs):
        """List all deductions"""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        """Create a new deduction"""
        serializer = self.get_serializer(data=request.data)
        
        if serializer.is_valid():
            serializer.save()
            return Response(
                serializer.data,
                status=status.HTTP_201_CREATED
            )
        
        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific deduction"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        """Update a deduction (PUT)"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=partial
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

    def partial_update(self, request, *args, **kwargs):
        """Partially update a deduction (PATCH)"""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Delete a deduction"""
        instance = self.get_object()
        instance.delete()
        return Response(
            {"detail": "Deduction deleted successfully"},
            status=status.HTTP_204_NO_CONTENT
        )

    @action(detail=False, methods=['get'])
    def by_employee(self, request):
        """Get deductions for a specific employee"""
        employee_id = request.query_params.get('employee_id', None)
        if not employee_id:
            return Response(
                {"error": "employee_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        deductions = self.queryset.filter(employee_id=employee_id)
        serializer = self.get_serializer(deductions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

