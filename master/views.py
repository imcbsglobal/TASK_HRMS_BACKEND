# master/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import LeaveType
from .serializers import LeaveTypeSerializer


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