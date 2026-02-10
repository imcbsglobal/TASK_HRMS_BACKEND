from django.db import models
from django.conf import settings

class Menu(models.Model):
    """
    Represents a menu item in the sidebar navigation
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    icon = models.CharField(max_length=50, blank=True, null=True)  # Icon name/class
    route = models.CharField(max_length=200, blank=True, null=True)  # Frontend route
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    order = models.IntegerField(default=0)  # For ordering menu items
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Menu'
        verbose_name_plural = 'Menus'

    def __str__(self):
        return self.name

    @property
    def full_path(self):
        """Returns the full path including parent hierarchy"""
        if self.parent:
            return f"{self.parent.full_path} > {self.name}"
        return self.name


class UserMenuAccess(models.Model):
    """
    Tracks which menus a user has access to
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='menu_access')
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name='user_access')
    can_view = models.BooleanField(default=True)
    can_create = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='granted_access')
    granted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'menu')
        verbose_name = 'User Menu Access'
        verbose_name_plural = 'User Menu Access'

    def __str__(self):
        return f"{self.user.username} - {self.menu.name}"


class UserRole(models.Model):
    """
    Extended user role information
    """
    ROLE_CHOICES = [
        ('super_admin', 'Super Admin'),
        ('admin', 'Admin'),
        ('user', 'User'),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='user_role')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user')
    department = models.CharField(max_length=100, blank=True, null=True)
    designation = models.CharField(max_length=100, blank=True, null=True)
    employee_id = models.CharField(max_length=50, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    profile_image = models.ImageField(upload_to='profile_images/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"

    @property
    def is_admin_or_super(self): 
        """Check if user has admin privileges"""
        return self.role in ['admin', 'super_admin']