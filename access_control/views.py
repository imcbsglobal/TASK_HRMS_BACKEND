from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import Menu, UserMenuAccess, UserRole
from .serializers import (
    MenuSerializer, 
    UserMenuAccessSerializer, 
    UserRoleSerializer,
    UserWithAccessSerializer,
    BulkMenuAccessSerializer
)

User = get_user_model()


class IsAdminOrSuperAdmin(permissions.BasePermission):
    """
    Custom permission to only allow admin or super_admin users
    FIXED: Now properly checks both User.role and UserRole.role
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Check User model role field (SUPER_ADMIN, ADMIN, USER)
        user_role_value = getattr(request.user, 'role', None)
        if user_role_value and user_role_value in ['SUPER_ADMIN', 'ADMIN']:
            return True
        
        # Also check UserRole model if it exists
        try:
            user_role = request.user.user_role
            if user_role.role in ['admin', 'super_admin']:
                return True
        except UserRole.DoesNotExist:
            pass
        
        return False


from rest_framework.decorators import action

class MenuViewSet(viewsets.ModelViewSet):
    queryset = Menu.objects.filter(is_active=True)
    serializer_class = MenuSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'hierarchy']:
            return [permissions.IsAuthenticated()]
        return [IsAdminOrSuperAdmin()]

    @action(detail=False, methods=['get'], url_path='hierarchy')
    def hierarchy(self, request):
        user = request.user
        
        user_role_value = getattr(user, 'role', None)
        is_admin = user_role_value in ['SUPER_ADMIN', 'ADMIN']

        if not is_admin:
            try:
                user_role = user.user_role
                is_admin = user_role.role in ['admin', 'super_admin']
            except UserRole.DoesNotExist:
                pass

        if is_admin:
            parent_menus = Menu.objects.filter(parent__isnull=True, is_active=True)
        else:
            accessible_menu_ids = user.menu_access.filter(can_view=True).values_list('menu_id', flat=True)
            parent_menus = Menu.objects.filter(
                parent__isnull=True,
                is_active=True,
                id__in=accessible_menu_ids
            )

        serializer = self.get_serializer(parent_menus, many=True)
        return Response(serializer.data)


class UserMenuAccessViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user menu access
    Only admin and super_admin can manage access
    """
    queryset = UserMenuAccess.objects.all()
    serializer_class = UserMenuAccessSerializer
    permission_classes = [IsAdminOrSuperAdmin]

    def get_queryset(self):
        """
        Filter by user_id if provided in query params
        """
        queryset = UserMenuAccess.objects.all().select_related('user', 'menu', 'granted_by')
        user_id = self.request.query_params.get('user_id', None)
        
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        
        return queryset

    @action(detail=False, methods=['post'])
    def bulk_update(self, request):
        """
        Bulk update menu access for a user
        Expected payload:
        {
            "user_id": 1,
            "menu_access": [
                {"menu_id": 1, "can_view": true, "can_create": false, "can_edit": false, "can_delete": false},
                {"menu_id": 2, "can_view": true, "can_create": true, "can_edit": true, "can_delete": false}
            ]
        }
        """
        serializer = BulkMenuAccessSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save(granted_by=request.user)
            
            # Return updated user data with access
            user_serializer = UserWithAccessSerializer(user)
            return Response(user_serializer.data, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserAccessControlViewSet(viewsets.ViewSet):
    """
    ViewSet for user access control management
    """
    permission_classes = [IsAdminOrSuperAdmin]

    def list(self, request):
        """
        List all users with their access information
        """
        users = User.objects.all().select_related('user_role').prefetch_related('menu_access')
        
        # Filter by role if provided
        role = request.query_params.get('role', None)
        if role:
            users = users.filter(user_role__role=role)
        
        # Filter by search term
        search = request.query_params.get('search', None)
        if search:
            users = users.filter(
                Q(username__icontains=search) |
                Q(email__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search)
            )
        
        serializer = UserWithAccessSerializer(users, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        """
        Get detailed access information for a specific user
        """
        try:
            user = User.objects.get(pk=pk)
            serializer = UserWithAccessSerializer(user)
            return Response(serializer.data)
        except User.DoesNotExist:
            return Response(
                {"error": "User not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['post'])
    def grant_access(self, request, pk=None):
        """
        Grant menu access to a user
        Expected payload:
        {
            "menu_id": 1,
            "can_view": true,
            "can_create": false,
            "can_edit": false,
            "can_delete": false
        }
        """
        try:
            user = User.objects.get(pk=pk)
            menu_id = request.data.get('menu_id')
            
            if not menu_id:
                return Response(
                    {"error": "menu_id is required"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                menu = Menu.objects.get(pk=menu_id)
            except Menu.DoesNotExist:
                return Response(
                    {"error": "Menu not found"}, 
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Create or update access
            access, created = UserMenuAccess.objects.update_or_create(
                user=user,
                menu=menu,
                defaults={
                    'can_view': request.data.get('can_view', True),
                    'can_create': request.data.get('can_create', False),
                    'can_edit': request.data.get('can_edit', False),
                    'can_delete': request.data.get('can_delete', False),
                    'granted_by': request.user
                }
            )
            
            serializer = UserMenuAccessSerializer(access)
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except User.DoesNotExist:
            return Response(
                {"error": "User not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['delete'])
    def revoke_access(self, request, pk=None):
        """
        Revoke menu access from a user
        """
        try:
            user = User.objects.get(pk=pk)
            menu_id = request.query_params.get('menu_id')
            
            if not menu_id:
                return Response(
                    {"error": "menu_id is required"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            UserMenuAccess.objects.filter(user=user, menu_id=menu_id).delete()
            
            return Response(
                {"message": "Access revoked successfully"}, 
                status=status.HTTP_200_OK
            )
            
        except User.DoesNotExist:
            return Response(
                {"error": "User not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['post'])
    def copy_access(self, request, pk=None):
        """
        Copy menu access from one user to another
        Expected payload:
        {
            "from_user_id": 1
        }
        """
        try:
            to_user = User.objects.get(pk=pk)
            from_user_id = request.data.get('from_user_id')
            
            if not from_user_id:
                return Response(
                    {"error": "from_user_id is required"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                from_user = User.objects.get(pk=from_user_id)
            except User.DoesNotExist:
                return Response(
                    {"error": "Source user not found"}, 
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Get all access from source user
            source_access = UserMenuAccess.objects.filter(user=from_user)
            
            # Delete existing access for target user
            UserMenuAccess.objects.filter(user=to_user).delete()
            
            # Copy access
            new_access = []
            for access in source_access:
                new_access.append(
                    UserMenuAccess(
                        user=to_user,
                        menu=access.menu,
                        can_view=access.can_view,
                        can_create=access.can_create,
                        can_edit=access.can_edit,
                        can_delete=access.can_delete,
                        granted_by=request.user
                    )
                )
            
            UserMenuAccess.objects.bulk_create(new_access)
            
            # Return updated user data
            serializer = UserWithAccessSerializer(to_user)
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except User.DoesNotExist:
            return Response(
                {"error": "User not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )