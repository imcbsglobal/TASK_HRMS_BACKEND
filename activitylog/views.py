from rest_framework import viewsets, status, permissions
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q
from datetime import datetime
from .models import ActivityLog
from .serializers import ActivityLogSerializer


def _is_admin(user):
    return (
        user.is_staff or user.is_superuser or
        getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'admin', 'super_admin'] or
        getattr(user, 'is_admin_user', False)
    )


class ActivityLogPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class ActivityLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing activity logs
    - Admin can see all logs for their tenant
    - Users can only see their own logs
    - Supports filtering and pagination
    """
    serializer_class = ActivityLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ActivityLogPagination

    def get_queryset(self):
        user = self.request.user
        if _is_admin(user):
            # Admin/SUPER_ADMIN: get all logs for their tenant
            if user.admin_owner:
                queryset = ActivityLog.objects.filter(admin_owner=user.admin_owner)
            else:
                queryset = ActivityLog.objects.filter(admin_owner=user)
        else:
            # Regular user: only their own logs
            queryset = ActivityLog.objects.filter(user=user)

        # Apply filters from query params
        # Search filter (search in username, description, module)
        search_query = self.request.query_params.get('search')
        if search_query:
            queryset = queryset.filter(
                Q(user__username__icontains=search_query) |
                Q(description__icontains=search_query) |
                Q(module__icontains=search_query)
            )

        # Action type filter
        action_type = self.request.query_params.get('action_type')
        if action_type:
            queryset = queryset.filter(action_type__iexact=action_type)

        # Module filter
        module = self.request.query_params.get('module')
        if module:
            queryset = queryset.filter(module__icontains=module)

        # Date range filters
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')

        if date_from:
            try:
                from_date = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=from_date)
            except ValueError:
                pass

        if date_to:
            try:
                to_date = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=to_date)
            except ValueError:
                pass

        # Order by most recent first
        return queryset.order_by('-created_at')

