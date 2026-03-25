from rest_framework import serializers
from .models import WhatsAppConfig, WhatsAppAdminNumber, WhatsAppNotificationPurpose


class WhatsAppConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model  = WhatsAppConfig
        fields = [
            'id', 'provider', 'instance_id', 'api_token',
            'phone_number', 'webhook_url', 'is_active',
        ]


class WhatsAppAdminNumberSerializer(serializers.ModelSerializer):
    class Meta:
        model  = WhatsAppAdminNumber
        fields = ['id', 'name', 'phone', 'role', 'purposes', 'active']


class WhatsAppNotificationPurposeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = WhatsAppNotificationPurpose
        fields = ['key', 'label', 'icon', 'desc', 'enabled', 'send_to_employee', 'send_to_admin']