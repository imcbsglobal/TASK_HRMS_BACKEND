from django.contrib.auth import get_user_model, authenticate
from rest_framework import serializers

User = get_user_model()


# ---------------------------------------------------------------------------
# Read / list
# ---------------------------------------------------------------------------
class UserSerializer(serializers.ModelSerializer):
    profile_image = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = (
            'id', 'username', 'first_name', 'last_name',
            'email', 'role', 'profile_image', 'is_active',
            'plain_password', 'work_location', 'client_id', 'admin_owner',
        )

    def get_profile_image(self, obj):
        if obj.profile_image:
            request = self.context.get('request')
            return request.build_absolute_uri(obj.profile_image.url) if request else obj.profile_image.url
        return None


# ---------------------------------------------------------------------------
# Create  –  POST /users/create/
# ---------------------------------------------------------------------------
class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model  = User
        fields = [
            'id', 'username', 'password', 'first_name', 'last_name',
            'email', 'role', 'profile_image', 'is_active', 'work_location',
            'admin_owner',
        ]
        # client_id is intentionally excluded – it is auto-generated in model.save()

    def create(self, validated_data):
        plain_pwd = validated_data.get('password', '')
        user = User.objects.create_user(**validated_data)
        user.plain_password = plain_pwd
        user.save()
        return user


# ---------------------------------------------------------------------------
# Update  –  PATCH /users/<id>/update/
# ---------------------------------------------------------------------------
class UserUpdateSerializer(serializers.ModelSerializer):
    profile_image = serializers.CharField(required=False, allow_null=True)
    password      = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model  = User
        fields = [
            'username', 'password', 'first_name', 'last_name',
            'email', 'role', 'is_active', 'profile_image', 'work_location',
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