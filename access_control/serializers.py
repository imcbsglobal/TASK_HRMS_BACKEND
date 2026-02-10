from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Menu, UserMenuAccess, UserRole

User = get_user_model()


class MenuSerializer(serializers.ModelSerializer):
    """
    Serializer for Menu model
    """
    children = serializers.SerializerMethodField()
    full_path = serializers.ReadOnlyField()

    class Meta:
        model = Menu
        fields = ['id', 'name', 'slug', 'icon', 'route', 'parent', 'order', 
                  'is_active', 'children', 'full_path', 'created_at', 'updated_at']

    def get_children(self, obj):
        """Get child menus"""
        if obj.children.exists():
            return MenuSerializer(obj.children.filter(is_active=True), many=True).data
        return []


class UserMenuAccessSerializer(serializers.ModelSerializer):
    """
    Serializer for UserMenuAccess model
    """
    menu_name = serializers.CharField(source='menu.name', read_only=True)
    menu_slug = serializers.CharField(source='menu.slug', read_only=True)
    menu_icon = serializers.CharField(source='menu.icon', read_only=True)
    menu_route = serializers.CharField(source='menu.route', read_only=True)
    granted_by_username = serializers.SerializerMethodField()

    class Meta:
        model = UserMenuAccess
        fields = ['id', 'user', 'menu', 'menu_name', 'menu_slug', 'menu_icon', 
                  'menu_route', 'can_view', 'can_create', 'can_edit', 'can_delete',
                  'granted_by', 'granted_by_username', 'granted_at', 'updated_at']
    
    def get_granted_by_username(self, obj):
        return obj.granted_by.username if obj.granted_by else None


class UserRoleSerializer(serializers.ModelSerializer):
    """
    Serializer for UserRole model
    """
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)

    class Meta:
        model = UserRole
        fields = ['id', 'user', 'username', 'email', 'first_name', 'last_name',
                  'role', 'department', 'designation', 'employee_id', 'phone',
                  'profile_image', 'created_at', 'updated_at']


class UserWithAccessSerializer(serializers.ModelSerializer):
    """
    Complete user serializer with menu access and role information
    FIXED: Removed date_joined field as it doesn't exist in custom User model
    """
    user_role = UserRoleSerializer(read_only=True)
    menu_access = serializers.SerializerMethodField()
    accessible_menus = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'is_active', 'role',
                  'user_role', 'menu_access', 'accessible_menus']

    def get_menu_access(self, obj):
        """Get all menu access permissions for the user"""
        access = UserMenuAccess.objects.filter(user=obj).select_related('menu')
        return UserMenuAccessSerializer(access, many=True).data

    def get_accessible_menus(self, obj):
        """Get list of menu IDs the user can access"""
        return list(obj.menu_access.filter(can_view=True).values_list('menu_id', flat=True))


class BulkMenuAccessSerializer(serializers.Serializer):
    """
    Serializer for bulk updating menu access for a user
    """
    user_id = serializers.IntegerField()
    menu_access = serializers.ListField(
        child=serializers.DictField(
            child=serializers.JSONField()
        )
    )

    def validate_user_id(self, value):
        """Validate that the user exists"""
        if not User.objects.filter(id=value).exists():
            raise serializers.ValidationError("User not found")
        return value

    def validate_menu_access(self, value):
        """Validate menu access data"""
        for access in value:
            if 'menu_id' not in access:
                raise serializers.ValidationError("Each access entry must have a menu_id")
            if not Menu.objects.filter(id=access['menu_id']).exists():
                raise serializers.ValidationError(f"Menu with id {access['menu_id']} not found")
        return value

    def save(self, granted_by=None):
        """
        Save or update menu access for the user
        """
        user_id = self.validated_data['user_id']
        menu_access_data = self.validated_data['menu_access']
        
        user = User.objects.get(id=user_id)
        
        # Delete existing access
        UserMenuAccess.objects.filter(user=user).delete()
        
        # Create new access entries
        access_objects = []
        for access in menu_access_data:
            menu = Menu.objects.get(id=access['menu_id'])
            access_objects.append(
                UserMenuAccess(
                    user=user,
                    menu=menu,
                    can_view=access.get('can_view', True),
                    can_create=access.get('can_create', False),
                    can_edit=access.get('can_edit', False),
                    can_delete=access.get('can_delete', False),
                    granted_by=granted_by
                )
            )
        
        UserMenuAccess.objects.bulk_create(access_objects)
        return user