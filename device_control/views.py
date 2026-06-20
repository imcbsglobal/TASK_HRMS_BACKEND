"""
device_control/views.py

Proxies device data from the external license server (activate.imcbs.com)
and filters it to only the devices belonging to the current admin's client_id.

Endpoints:
  GET  /api/device-control/devices/      – list devices for the logged-in admin
  DELETE /api/device-control/devices/<device_id>/  – deregister a device
  GET  /api/device-control/license-info/ – full license summary for this client
"""

import requests as http_requests
import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from activitylog.utils import log_activity

logger = logging.getLogger(__name__)

# ── External API ──────────────────────────────────────────────────────────────
LICENSE_API_URL = "https://activate.imcbs.com/mobileapp/api/project/trellisco/"
DEVICE_DELETE_URL = "https://activate.imcbs.com/mobileapp/api/project/trellisco/mobile_control/"
API_TIMEOUT = 12  # seconds


def _get_admin_owner(user):
    """
    Return the ADMIN who owns the current request's tenant scope.
    - ADMIN       → themselves
    - USER        → their admin_owner
    - SUPER_ADMIN → None (cross-tenant; handled per-view)
    """
    if user.role == 'ADMIN':
        return user
    if user.role == 'USER':
        return user.admin_owner
    return None  # SUPER_ADMIN


def _fetch_all_customers():
    """
    Fetch all customer data from the license server.
    Returns (customers_list, error_response_or_None).
    """
    try:
        resp = http_requests.get(LICENSE_API_URL, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get('customers', []), None
    except http_requests.exceptions.Timeout:
        logger.warning("device_control: License server timed out.")
        return None, Response(
            {"detail": "License server timed out. Please try again."},
            status=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except http_requests.exceptions.RequestException as exc:
        logger.error("device_control: Could not reach license server: %s", exc)
        return None, Response(
            {"detail": f"Could not reach license server: {exc}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )


def _find_customer(customers, client_id):
    """Return the customer dict matching client_id, or None."""
    for customer in customers:
        if customer.get('client_id') == client_id:
            return customer
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Device List View
# ─────────────────────────────────────────────────────────────────────────────

class DeviceListView(APIView):
    """
    GET /api/device-control/devices/

    Returns all registered devices for the logged-in admin's client_id,
    fetched live from the external license server.

    Admins see only their own client_id's devices.
    Super Admins can pass ?client_id=<id> to inspect any client.

    Response shape:
    {
        "client_id": "...",
        "customer_name": "...",
        "license_summary": { "registered_devices": 2, "max_devices": 5 },
        "license_validity": { ... },
        "status": "Active",
        "devices": [
            {
                "device_id": "...",
                "device_name": "...",
                "user_name": "...",
                "ip_address": "...",
                "logged_in_at": "..."
            },
            ...
        ]
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        # Resolve the effective client_id
        if user.role == 'SUPER_ADMIN':
            # Super admin may query any client_id via query param
            target_client_id = request.query_params.get('client_id', '').strip()
            if not target_client_id:
                # Return all customers' summaries
                customers, err = _fetch_all_customers()
                if err:
                    return err
                result = []
                for c in customers:
                    result.append({
                        'client_id':        c.get('client_id'),
                        'customer_name':    c.get('customer_name'),
                        'status':           c.get('status'),
                        'license_summary':  c.get('license_summary', {}),
                        'license_validity': c.get('license_validity', {}),
                        'device_count':     len(c.get('registered_devices', [])),
                    })
                return Response({'customers': result}, status=status.HTTP_200_OK)
        else:
            admin = _get_admin_owner(user)
            if admin is None:
                return Response(
                    {"detail": "Could not determine your client ID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            target_client_id = admin.client_id

        if not target_client_id:
            return Response(
                {"detail": "No client ID is associated with this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customers, err = _fetch_all_customers()
        if err:
            return err

        customer = _find_customer(customers, target_client_id)
        if not customer:
            # Client ID not found on the license server — return empty list
            return Response({
                'client_id':        target_client_id,
                'customer_name':    getattr(_get_admin_owner(user), 'company_name', '') or '',
                'status':           'Unknown',
                'license_summary':  {'registered_devices': 0, 'max_devices': 0},
                'license_validity': {},
                'devices':          [],
            }, status=status.HTTP_200_OK)

        # Normalise the device list field name
        devices = customer.get('registered_devices', [])

        return Response({
            'client_id':        customer.get('client_id'),
            'customer_name':    customer.get('customer_name'),
            'status':           customer.get('status'),
            'license_summary':  customer.get('license_summary', {}),
            'license_validity': customer.get('license_validity', {}),
            'devices':          devices,
        }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
# Device Delete View
# ─────────────────────────────────────────────────────────────────────────────

class DeviceDeleteView(APIView):
    """
    DELETE /api/device-control/devices/<str:device_id>/

    Deregisters (removes) a device from the external license server for the
    logged-in admin's client_id.

    The external API for deregistration is:
      POST https://activate.imcbs.com/mobileapp/api/project/trellisco/logout/
    with body: { "license_key": "...", "device_id": "..." }

    The license server's response is treated as the source of truth — a non-2xx
    response, or a 200 with an explicit error/success=false in the body, is
    surfaced back to the caller as a real error (not swallowed/optimistic).
    """
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, device_id):
        user = request.user

        # Only admins can delete devices
        if user.role not in ('ADMIN', 'SUPER_ADMIN'):
            return Response(
                {"detail": "Only admins can remove devices."},
                status=status.HTTP_403_FORBIDDEN,
            )

        admin = _get_admin_owner(user)
        if user.role == 'SUPER_ADMIN':
            target_client_id = request.data.get('client_id', '').strip()
        else:
            target_client_id = admin.client_id if admin else ''

        if not target_client_id:
            return Response(
                {"detail": "No client ID is associated with this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify the device actually belongs to this client before deleting
        customers, err = _fetch_all_customers()
        if err:
            return err

        customer = _find_customer(customers, target_client_id)
        if not customer:
            return Response(
                {"detail": "Client ID not found on the license server."},
                status=status.HTTP_404_NOT_FOUND,
            )

        devices = customer.get('registered_devices', [])
        device = next((d for d in devices if d.get('device_id') == device_id), None)
        if not device:
            return Response(
                {"detail": "Device not found for this client."},
                status=status.HTTP_404_NOT_FOUND,
            )

        device_name = device.get('device_name', device_id)
        license_key = customer.get('license_key')

        if not license_key:
            return Response(
                {"detail": "No license key found for this client; cannot deregister device."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Call the external deregister endpoint.
        # POST https://activate.imcbs.com/mobileapp/api/project/trellisco/logout/
        # Body: { "license_key": "...", "device_id": "..." }
        deregister_url = "https://activate.imcbs.com/mobileapp/api/project/trellisco/logout/"
        try:
            ext_resp = http_requests.post(
                deregister_url,
                json={"license_key": license_key, "device_id": device_id},
                timeout=API_TIMEOUT,
            )
        except http_requests.exceptions.Timeout:
            logger.warning(
                "device_control: Deregister timed out for device %s.", device_id
            )
            return Response(
                {"detail": "License server timed out while deregistering the device. Please try again."},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except http_requests.exceptions.RequestException as exc:
            logger.error(
                "device_control: Deregister call failed for device %s: %s", device_id, exc
            )
            return Response(
                {"detail": f"Could not reach license server to deregister device: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Try to parse the external API's response body, regardless of status,
        # so we can surface a meaningful message either way.
        try:
            ext_body = ext_resp.json()
        except ValueError:
            ext_body = {}

        if not ext_resp.ok:
            ext_message = (
                ext_body.get('detail')
                or ext_body.get('message')
                or ext_body.get('error')
                or ext_resp.text[:200]
                or "Unknown error from license server."
            )
            logger.warning(
                "device_control: Deregister rejected for device %s (status %s): %s",
                device_id, ext_resp.status_code, ext_message,
            )
            return Response(
                {"detail": f"Failed to deregister device: {ext_message}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Some APIs return 200 with an embedded error/success flag in the body.
        if isinstance(ext_body, dict) and (
            ext_body.get('success') is False or ext_body.get('error')
        ):
            ext_message = ext_body.get('message') or ext_body.get('error') or "Deregistration failed."
            logger.warning(
                "device_control: Deregister returned success=False for device %s: %s",
                device_id, ext_message,
            )
            return Response(
                {"detail": f"Failed to deregister device: {ext_message}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        log_activity(
            user=request.user,
            action_type='DELETE',
            module='Device Control',
            description=(
                f"Deregistered device '{device_name}' (ID: {device_id}) "
                f"for client '{target_client_id}'"
            ),
            request=request,
        )

        return Response(
            {"detail": f"Device '{device_name}' has been deregistered."},
            status=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────────────────────────
# License Info View
# ─────────────────────────────────────────────────────────────────────────────

class LicenseInfoView(APIView):
    """
    GET /api/device-control/license-info/

    Returns the full license summary for the logged-in admin's client_id,
    including device count, max devices, validity, and status.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        if user.role == 'SUPER_ADMIN':
            target_client_id = request.query_params.get('client_id', '').strip()
            if not target_client_id:
                return Response(
                    {"detail": "Pass ?client_id=<id> to query a specific client."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            admin = _get_admin_owner(user)
            target_client_id = admin.client_id if admin else ''

        if not target_client_id:
            return Response(
                {"detail": "No client ID is associated with this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customers, err = _fetch_all_customers()
        if err:
            return err

        customer = _find_customer(customers, target_client_id)
        if not customer:
            return Response(
                {"detail": "Client ID not found on the license server."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            'client_id':        customer.get('client_id'),
            'customer_name':    customer.get('customer_name'),
            'license_key':      customer.get('license_key'),
            'package':          customer.get('package'),
            'modules':          customer.get('modules', []),
            'status':           customer.get('status'),
            'license_summary':  customer.get('license_summary', {}),
            'license_validity': customer.get('license_validity', {}),
        }, status=status.HTTP_200_OK)