from rest_framework import serializers
from .models import ActivityLog
from login.models import User


class ActivityLogSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    user_full_name = serializers.CharField(source='user.full_name', read_only=True)
    user_role = serializers.CharField(source='user.role', read_only=True)

    class Meta:
        model = ActivityLog
        fields = [
            'id',
            'user',
            'user_username',
            'user_full_name',
            'user_role',
            'action_type',
            'module',
            'description',
            'ip_address',
            'user_agent',
            'created_at',
        ]
        read_only_fields = [
            'id',
            'user',
            'user_username',
            'user_full_name',
            'user_role',
            'ip_address',
            'user_agent',
            'created_at',
        ]
