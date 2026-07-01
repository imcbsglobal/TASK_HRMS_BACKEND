from django.contrib.auth import get_user_model, authenticate
from rest_framework import serializers
from .models import CompanySettings

User = get_user_model()


# ---------------------------------------------------------------------------
# Read / list
# ---------------------------------------------------------------------------
class UserSerializer(serializers.ModelSerializer):
    profile_image = serializers.SerializerMethodField()
    employee_profile_image = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = (
            'id', 'username', 'first_name', 'last_name',
            'email', 'role', 'profile_image', 'employee_profile_image',
            'is_active', 'plain_password', 'work_location', 'client_id',
            'admin_owner', 'company_name', 'is_admin_user',
            'can_switch_client',
        )

    def get_profile_image(self, obj):
        if obj.profile_image:
            return obj.profile_image.url
        return None

    def get_employee_profile_image(self, obj):
        """Return the linked employee's profile photo (stored in R2) if available."""
        try:
            from employee_management.models import Employee
            employee = Employee.objects.filter(email=obj.email).first()
            if employee and employee.profile_image:
                return employee.profile_image.url
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Create  –  POST /users/create/
# ---------------------------------------------------------------------------
class UserCreateSerializer(serializers.ModelSerializer):
    password  = serializers.CharField(write_only=True)
    # Allow the caller (Super Admin) to supply a pre-validated client_id
    # coming from the license server.  The field is optional; if omitted the
    # model's save() method will auto-generate one as before.
    client_id    = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    company_name = serializers.CharField(required=False, allow_blank=True, default='')
    is_admin_user = serializers.BooleanField(required=False, default=False)
    can_switch_client = serializers.BooleanField(required=False, default=True)

    class Meta:
        model  = User
        fields = [
            'id', 'username', 'password', 'first_name', 'last_name',
            'email', 'role', 'profile_image', 'is_active', 'work_location',
            'admin_owner', 'client_id', 'company_name', 'is_admin_user',
            'can_switch_client',
        ]

    def validate_client_id(self, value):
        """
        If a client_id is provided, make sure it is not already taken by
        another account (prevents duplicate-key errors at the DB level).
        """
        if value:
            # Check for existing users with this client_id, excluding the
            # instance being updated (not relevant for creates, but safe).
            instance = getattr(self, 'instance', None)
            qs = User.objects.filter(client_id=value)
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    "An admin account with this Client ID already exists."
                )
        return value or None

    def create(self, validated_data):
        plain_pwd = validated_data.get('password', '')
        # Pop client_id so create_user doesn't try to pass it as an unknown kwarg.
        supplied_client_id = validated_data.pop('client_id', None)
        company_name       = validated_data.pop('company_name', '')

        user = User.objects.create_user(**validated_data)
        user.plain_password = plain_pwd

        # If the caller supplied a specific client_id (from the license server),
        # overwrite the auto-generated one.
        if supplied_client_id:
            user.client_id = supplied_client_id

        if company_name:
            user.company_name = company_name

        user.save()
        return user


# ---------------------------------------------------------------------------
# Update  –  PATCH /users/<id>/update/
# ---------------------------------------------------------------------------
class UserUpdateSerializer(serializers.ModelSerializer):
    profile_image = serializers.CharField(required=False, allow_null=True)
    password      = serializers.CharField(write_only=True, required=False, allow_blank=True)
    is_admin_user = serializers.BooleanField(required=False)
    can_switch_client = serializers.BooleanField(required=False)

    class Meta:
        model  = User
        fields = [
            'username', 'password', 'first_name', 'last_name',
            'email', 'role', 'is_active', 'profile_image', 'work_location',
            'is_admin_user', 'can_switch_client',
        ]

    def update(self, instance, validated_data):
        image_value = validated_data.pop('profile_image', 'NOT_PROVIDED')
        password    = validated_data.pop('password', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)
            instance.plain_password = password

        if image_value == 'NOT_PROVIDED':
            pass
        elif image_value is None or image_value == 'remove':
            instance.profile_image = None

        instance.save()
        return instance


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()

    def validate(self, data):
        user = authenticate(username=data['username'], password=data['password'])
        if not user:
            raise serializers.ValidationError("Invalid credentials")
        data['user'] = user
        return data


class CompanySettingsSerializer(serializers.ModelSerializer):
    client_id = serializers.CharField(source='owner.client_id', read_only=True)
    setup_completed = serializers.SerializerMethodField()

    class Meta:
        model = CompanySettings
        fields = (
            'client_id', 'setup_completed',
            'name', 'tagline', 'email', 'phone', 'website', 'address',
            'logo', 'primaryColor', 'currency', 'timezone',
        )

    def get_setup_completed(self, obj):
        return bool(obj and obj.name)
