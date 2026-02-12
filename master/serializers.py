# master/serializers.py
from rest_framework import serializers
from .models import LeaveType


class LeaveTypeSerializer(serializers.ModelSerializer):
    """
    Serializer for LeaveType model
    """
    class Meta:
        model = LeaveType
        fields = [
            'id',
            'name',
            'description',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']