from django.contrib.auth import get_user_model, authenticate
from rest_framework import serializers

User = get_user_model()


# ---------------------------------------------------------------------------
# Read / list  –  used by UserListView & ProfileAPIView
# ---------------------------------------------------------------------------
class UserSerializer(serializers.ModelSerializer):
    profile_image = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = (
            'id', 'username', 'first_name', 'last_name',
            'email', 'role', 'profile_image', 'is_active',
        )

    def get_profile_image(self, obj):
        """Return an absolute URL for the image, or None."""
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
        fields = ['id', 'username', 'password', 'first_name', 'last_name', 'email', 'role', 'profile_image']

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


# ---------------------------------------------------------------------------
# Update  –  PATCH /users/<id>/update/
# ---------------------------------------------------------------------------
class UserUpdateSerializer(serializers.ModelSerializer):
    """
    All fields are optional so that a partial (PATCH) update works.
    profile_image accepts a new file upload OR the string "remove" to clear it.
    """
    profile_image = serializers.CharField(required=False, allow_null=True)

    class Meta:
        model  = User
        fields = ['first_name', 'last_name', 'email', 'role', 'is_active', 'profile_image']

    def update(self, instance, validated_data):
        image_value = validated_data.pop('profile_image', 'NOT_PROVIDED')

        # Update simple fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Handle profile_image separately
        if image_value == 'NOT_PROVIDED':
            pass                          # field was not sent at all – keep current
        elif image_value is None or image_value == 'remove':
            instance.profile_image = None # explicit removal
        else:
            # A new file was uploaded – it arrives via request.FILES,
            # so we grab it from the serializer's context in the view.
            pass                          # handled in view before calling save()

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