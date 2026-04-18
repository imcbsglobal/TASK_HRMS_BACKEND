from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
import random
import string


def generate_client_id():
    def make_id():
        part1 = ''.join(random.choices(string.ascii_uppercase, k=2))
        part2 = ''.join(random.choices(string.digits, k=3))
        part3 = ''.join(random.choices(string.ascii_uppercase, k=1))
        part4 = ''.join(random.choices(string.digits, k=2))
        part5 = ''.join(random.choices(string.ascii_uppercase, k=2))
        part6 = ''.join(random.choices(string.digits, k=3))
        return f"{part1}{part2}{part3}{part4}{part5}{part6}"

    client_id = make_id()
    while User.objects.filter(client_id=client_id).exists():
        client_id = make_id()
    return client_id


class UserManager(BaseUserManager):
    def create_user(self, username, password=None, role='USER', **extra_fields):
        if not username:
            raise ValueError("Username is required")
        user = self.model(username=username, role=role, **extra_fields)
        user.set_password(password)
        if role in ('ADMIN', 'SUPER_ADMIN') and not user.client_id:
            user.client_id = generate_client_id()
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

    # Client ID: auto-generated for ADMIN and SUPER_ADMIN
    # Format: CD541B60NT354
    client_id     = models.CharField(max_length=13, unique=True, blank=True, null=True)

    WORK_LOCATION_CHOICES = (
        ('IN_OFFICE',     'In Office'),
        ('OUT_OF_OFFICE', 'Out of Office'),
    )
    work_location = models.CharField(
        max_length=20,
        choices=WORK_LOCATION_CHOICES,
        default='IN_OFFICE',
    )

    # WARNING: plain-text password storage — development only!
    plain_password = models.CharField(max_length=128, blank=True, default='')

    # Company name fetched from the license server at admin creation time
    company_name = models.CharField(max_length=255, blank=True, default='')

    # ── Tenant isolation ──────────────────────────────────────────────────────
    # Every USER belongs to the ADMIN who created them.
    # ADMIN and SUPER_ADMIN rows have this NULL.
    # Deleting an ADMIN cascades and removes all their owned users.
    admin_owner = models.ForeignKey(
        'self',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='owned_users',
        limit_choices_to={'role': 'ADMIN'},
    )

    USERNAME_FIELD = 'username'
    objects = UserManager()

    def __str__(self):
        return self.username

    def save(self, *args, **kwargs):
        if self.role in ('ADMIN', 'SUPER_ADMIN') and not self.client_id:
            def make_id():
                p1 = ''.join(random.choices(string.ascii_uppercase, k=2))
                p2 = ''.join(random.choices(string.digits, k=3))
                p3 = ''.join(random.choices(string.ascii_uppercase, k=1))
                p4 = ''.join(random.choices(string.digits, k=2))
                p5 = ''.join(random.choices(string.ascii_uppercase, k=2))
                p6 = ''.join(random.choices(string.digits, k=3))
                return f"{p1}{p2}{p3}{p4}{p5}{p6}"
            new_id = make_id()
            while User.objects.filter(client_id=new_id).exclude(pk=self.pk).exists():
                new_id = make_id()
            self.client_id = new_id
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.username