from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager


class UserManager(BaseUserManager):
    def create_user(self, username, password=None, role='USER', **extra_fields):
        if not username:
            raise ValueError("Username is required")
        user = self.model(username=username, role=role, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, username, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(username, password, role='SUPER_ADMIN', **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = (
        ('SUPER_ADMIN', 'Super Admin'),
        ('ADMIN', 'Admin'),
        ('USER', 'User'),
    )

    username      = models.CharField(max_length=100, unique=True)
    first_name    = models.CharField(max_length=100, blank=True, default='')
    last_name     = models.CharField(max_length=100, blank=True, default='')
    email         = models.EmailField(unique=True, blank=True, null=True)
    role          = models.CharField(max_length=20, choices=ROLE_CHOICES, default='USER')
    profile_image = models.ImageField(upload_to='profile_images/', blank=True, null=True)
    is_active     = models.BooleanField(default=True)
    is_staff      = models.BooleanField(default=False)
    
    # WARNING: This field stores plain-text passwords - ONLY for development!
    # Remove this in production and use password reset functionality instead
    plain_password = models.CharField(max_length=128, blank=True, default='')

    USERNAME_FIELD = 'username'

    objects = UserManager()

    def __str__(self):
        return self.username

    # ---------------------------------------------------------------------------
    # Convenience: full name (used in serializers / admin)
    # ---------------------------------------------------------------------------
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.username