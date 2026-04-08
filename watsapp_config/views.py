import requests
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from .models import WhatsAppConfig, WhatsAppAdminNumber, WhatsAppNotificationPurpose
from .serializers import (
    WhatsAppConfigSerializer,
    WhatsAppAdminNumberSerializer,
    WhatsAppNotificationPurposeSerializer,
)


def _get_admin_owner(user):
    """
    Return the ADMIN who owns the current request's tenant scope.
    """
    if user.role == 'ADMIN':
        return user
    if user.role == 'USER':
        return user.admin_owner
    return None  # SUPER_ADMIN


class WhatsAppConfigView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return the current config, admin numbers, and notification purposes."""
        user = request.user
        admin = _get_admin_owner(user)

        if user.role != 'SUPER_ADMIN' and admin is None:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        # Config — tenant-specific
        config_qs = WhatsAppConfig.objects.all()
        if user.role != 'SUPER_ADMIN':
            config_qs = config_qs.filter(admin_owner=admin)
        
        config_obj = config_qs.first()
        if not config_obj and user.role != 'SUPER_ADMIN':
            config_obj = WhatsAppConfig.objects.create(admin_owner=admin)
        
        config_data = WhatsAppConfigSerializer(config_obj).data if config_obj else {}

        # Admin numbers
        admin_numbers_qs = WhatsAppAdminNumber.objects.all()
        if user.role != 'SUPER_ADMIN':
            admin_numbers_qs = admin_numbers_qs.filter(admin_owner=admin)
        admin_data = WhatsAppAdminNumberSerializer(admin_numbers_qs, many=True).data

        # Notification purposes
        purposes_qs = WhatsAppNotificationPurpose.objects.all()
        if user.role != 'SUPER_ADMIN':
            purposes_qs = purposes_qs.filter(admin_owner=admin)
        purpose_data = WhatsAppNotificationPurposeSerializer(purposes_qs, many=True).data

        return Response({
            'config':        config_data,
            'admin_numbers': admin_data,
            'purposes':      purpose_data,
        })

    def post(self, request):
        """
        Save whichever section the frontend sends.
        """
        user = request.user
        if user.role == 'USER':
            return Response({'error': 'Only admins can change config'}, status=status.HTTP_403_FORBIDDEN)
        
        admin = _get_admin_owner(user)
        data = request.data

        # ── Save API config ───────────────────────────────────────────────
        if 'config' in data:
            config_qs = WhatsAppConfig.objects.all()
            if user.role != 'SUPER_ADMIN':
                config_qs = config_qs.filter(admin_owner=admin)
            
            config_obj = config_qs.first()
            if not config_obj and user.role != 'SUPER_ADMIN':
                config_obj = WhatsAppConfig.objects.create(admin_owner=admin)

            if config_obj:
                serializer = WhatsAppConfigSerializer(config_obj, data=data['config'], partial=True)
                if not serializer.is_valid():
                    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
                serializer.save(admin_owner=admin if user.role != 'SUPER_ADMIN' else config_obj.admin_owner)

        # ── Save admin numbers ────────────────────────────────────────────
        if 'admin_numbers' in data:
            # Replace all existing rows for this tenant
            admin_num_qs = WhatsAppAdminNumber.objects.all()
            if user.role != 'SUPER_ADMIN':
                admin_num_qs = admin_num_qs.filter(admin_owner=admin)
            admin_num_qs.delete()
            
            for item in data['admin_numbers']:
                item.pop('id', None)
                if user.role != 'SUPER_ADMIN':
                    WhatsAppAdminNumber.objects.create(**item, admin_owner=admin)
                else:
                    WhatsAppAdminNumber.objects.create(**item)

        # ── Save notification purposes ────────────────────────────────────
        if 'purposes' in data:
            for item in data['purposes']:
                lookup = {'key': item['key']}
                if user.role != 'SUPER_ADMIN':
                    lookup['admin_owner'] = admin

                WhatsAppNotificationPurpose.objects.update_or_create(
                    **lookup,
                    defaults={
                        'label':            item.get('label', ''),
                        'icon':             item.get('icon', ''),
                        'desc':             item.get('desc', ''),
                        'enabled':          item.get('enabled', False),
                        'send_to_employee': item.get('sendToEmployee', True),
                        'send_to_admin':    item.get('sendToAdmin', False),
                    }
                )

        return Response({'detail': 'Saved successfully.'}, status=status.HTTP_200_OK)


class WhatsAppTestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        Test the WhatsApp API connection for the given provider config.
        """
        config = request.data.get('config', {})
        provider   = config.get('provider', '')
        instance_id = config.get('instance_id', '')
        api_token   = config.get('api_token', '')

        if not instance_id or not api_token:
            return Response(
                {'error': 'Instance ID and API Token are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if provider == 'ultramsg':
                url  = f"https://api.ultramsg.com/{instance_id}/instance/status"
                resp = requests.get(url, params={'token': api_token}, timeout=10)
                if resp.status_code == 200:
                    return Response({'detail': 'Connection successful!'})
                return Response(
                    {'error': f"UltraMsg returned {resp.status_code}: {resp.text}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            elif provider == 'waapi':
                url  = f"https://waapi.app/api/v1/instances/{instance_id}/client/status"
                resp = requests.get(
                    url,
                    headers={'Authorization': f'Bearer {api_token}'},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return Response({'detail': 'Connection successful!'})
                return Response(
                    {'error': f"WaAPI returned {resp.status_code}: {resp.text}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            elif provider == 'twilio':
                from twilio.rest import Client
                client = Client(instance_id, api_token)
                client.api.accounts(instance_id).fetch()
                return Response({'detail': 'Twilio connection successful!'})

            elif provider == 'meta':
                url  = f"https://graph.facebook.com/v18.0/{instance_id}"
                resp = requests.get(
                    url,
                    params={'access_token': api_token},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return Response({'detail': 'Meta Cloud API connection successful!'})
                return Response(
                    {'error': f"Meta returned {resp.status_code}: {resp.text}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            elif provider == 'wablas':
                url  = f"{instance_id.rstrip('/')}/api/device/info"
                resp = requests.get(
                    url,
                    headers={'Authorization': api_token},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return Response({'detail': 'Wablas connection successful!'})
                return Response(
                    {'error': f"Wablas returned {resp.status_code}: {resp.text}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            else:
                return Response({'detail': 'Credentials saved. Cannot auto-test custom providers.'})

        except requests.exceptions.ConnectionError:
            return Response(
                {'error': 'Could not reach the provider. Check your instance ID / URL.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except requests.exceptions.Timeout:
            return Response(
                {'error': 'Connection timed out. The provider did not respond.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )