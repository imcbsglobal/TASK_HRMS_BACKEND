from django.contrib.auth import get_user_model
from rest_framework.views        import APIView
from rest_framework.response     import Response
from rest_framework              import status, permissions
from rest_framework.permissions  import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
import requests as _req
import logging

from .serializers import (
    UserSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    LoginSerializer,
    CompanySettingsSerializer,
)
from .models import CompanySettings
from activitylog.utils import log_activity

logger = logging.getLogger(__name__)
User = get_user_model()

# ---------------------------------------------------------------------------
# License status check helper
# Must be defined at the TOP so LoginView can call it.
# ---------------------------------------------------------------------------
_TRELLISCO_LICENSE_API = "https://activate.imcbs.com/mobileapp/api/project/trellisco/"

def _check_license_status(client_id):
    """
    Returns (is_active: bool, error_message: str | None).
    - Blocks login when the client_id is found and status != 'Active'.
    - Fails OPEN (allows login) only when the license server is unreachable,
      so a network blip never locks everyone out.
    - Fails OPEN when the client_id is not in the list (manually-created admins).
    """
    try:
        resp = _req.get(_TRELLISCO_LICENSE_API, timeout=8)
        resp.raise_for_status()
        customers = resp.json().get("customers", [])
        for customer in customers:
            if customer.get("client_id") == client_id:
                lic_status = (customer.get("status") or "").strip()
                if lic_status.lower() == "active":
                    return True, None
                customer_name = customer.get("customer_name", client_id)
                return False, (
                    f"License for '{customer_name}' is {lic_status}. "
                    f"Please contact your administrator to activate the license."
                )
        # client_id not in license list → fail open
        return True, None
    except Exception as exc:
        # Log the real error so it's visible in Django logs
        logger.warning("License check failed for %s: %s", client_id, exc)
        # Fail open — don't lock users out due to a network issue
        return True, None


def get_tenant_admin(user):
    if user.role == 'ADMIN':
        return user
    if user.role == 'USER':
        return user.admin_owner
    return None


# ---------------------------------------------------------------------------
# Login – POST /api/login/
#
# Login rules:
#   SUPER_ADMIN  →  username + password only  (client_id must be EMPTY)
#   ADMIN        →  client_id + username + password  (client_id = their own)
#   USER         →  client_id + username + password  (client_id = their admin's)
# ---------------------------------------------------------------------------
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        username  = request.data.get('username', '').strip()
        password  = request.data.get('password', '').strip()
        client_id = request.data.get('client_id', '').strip()

        if not username or not password:
            return Response(
                {"detail": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.check_password(password):
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.is_active:
            return Response({"detail": "This account is inactive."}, status=status.HTTP_403_FORBIDDEN)

        # ── Role-based client_id validation ────────────────────────────────
        if user.role == 'SUPER_ADMIN':
            if client_id:
                return Response(
                    {"detail": "Super Admin login does not require a Client ID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        elif user.role == 'ADMIN':
            # Admin must supply their own client_id
            if not client_id:
                return Response(
                    {"detail": "Client ID is required for Admin login."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if user.client_id != client_id:
                return Response(
                    {"detail": "Invalid Client ID for this account."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

        elif user.role == 'USER':
            # User must supply the client_id of the ADMIN who owns them
            if not client_id:
                return Response(
                    {"detail": "Client ID is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Verify the client_id belongs to this user's admin_owner
            if not user.admin_owner or user.admin_owner.client_id != client_id:
                return Response(
                    {"detail": "Invalid Client ID."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

        # ── License status check (ADMIN and USER only) ────────────────────
        # Determine which client_id to check against the license server.
        # SUPER_ADMIN has no client_id so we skip the check entirely.
        if user.role in ('ADMIN', 'USER'):
            check_id = (
                user.client_id if user.role == 'ADMIN'
                else (user.admin_owner.client_id if user.admin_owner else None)
            )
            if check_id:
                is_active, license_error = _check_license_status(check_id)
                if not is_active:
                    return Response(
                        {"detail": license_error},
                        status=status.HTTP_403_FORBIDDEN,
                    )

        # ── Issue tokens ───────────────────────────────────────────────────
        refresh         = RefreshToken.for_user(user)
        normalized_role = user.role.lower()
        tenant_admin    = get_tenant_admin(user)
        setup_completed = (
            CompanySettings.objects.filter(owner=tenant_admin).exists()
            if tenant_admin else True
        )

        # Log the login activity
        log_activity(
            user=user,
            action_type='LOGIN',
            module='Authentication',
            description=f'User {user.username} logged in successfully',
            request=request
        )

        return Response({
            "access":    str(refresh.access_token),
            "refresh":   str(refresh),
            "role":      normalized_role,
            "username":  user.username,
            "client_id": user.client_id if user.role == 'ADMIN' else client_id,
            "company_setup_completed": setup_completed,
        })


# ---------------------------------------------------------------------------
# Profile – GET /api/profile/
# ---------------------------------------------------------------------------
class ProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user, context={'request': request})
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Logout – POST /api/logout/
# ---------------------------------------------------------------------------
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
            except TokenError:
                pass
        # Log the logout activity
        log_activity(
            user=request.user,
            action_type='LOGOUT',
            module='Authentication',
            description=f'User {request.user.username} logged out',
            request=request
        )
        return Response({"message": "Logged out successfully"}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# User List – GET /api/users/
#
# SUPER_ADMIN → only ADMIN accounts (all admins)
# ADMIN       → only USERs that belong to this admin (admin_owner = request.user)
# ---------------------------------------------------------------------------
class UserListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role == 'SUPER_ADMIN':
            # Super admin sees only the admins they created
            users = User.objects.filter(role='ADMIN').order_by('id')
        elif request.user.role == 'ADMIN':
            # Admin sees only the users they own
            users = User.objects.filter(
                role='USER',
                admin_owner=request.user
            ).order_by('id')
        else:
            return Response(
                {"detail": "You do not have permission to list users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = UserSerializer(users, many=True, context={'request': request})
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# User Create – POST /api/users/create/
#
# SUPER_ADMIN → can only create ADMIN accounts
# ADMIN       → can only create USER accounts; admin_owner is set to self
# ---------------------------------------------------------------------------
class UserCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        requesting_role = request.user.role
        target_role     = request.data.get('role', 'USER')

        if requesting_role == 'SUPER_ADMIN':
            if target_role != 'ADMIN':
                return Response(
                    {"detail": "Super Admins can only create Admin accounts."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        elif requesting_role == 'ADMIN':
            if target_role != 'USER':
                return Response(
                    {"detail": "Admins can only create User accounts."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            return Response(
                {"detail": "You do not have permission to create users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        data = request.data.copy()

        # Automatically assign admin_owner when an ADMIN creates a USER
        if requesting_role == 'ADMIN':
            data['admin_owner'] = request.user.id

        serializer = UserCreateSerializer(data=data)
        if serializer.is_valid():
            user = serializer.save()
            log_activity(
                user=request.user,
                action_type='CREATE',
                module='User Management',
                description=f"Created user '{user.username}' with role {user.role}",
                request=request,
            )
            return Response(
                {
                    "message":   "User created successfully",
                    "client_id": user.client_id,
                    "user":      UserSerializer(user, context={'request': request}).data,
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# User Update – PATCH /api/users/<pk>/update/
# ---------------------------------------------------------------------------
class UserUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Permission check: ADMIN can only edit their own users
        if request.user.role == 'ADMIN':
            if user.admin_owner_id != request.user.id:
                return Response(
                    {"detail": "You do not have permission to edit this user."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        elif request.user.role == 'USER':
            if request.user.id != pk:
                return Response(
                    {"detail": "You do not have permission to edit this user."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        new_role = request.data.get('role')
        if new_role in ('ADMIN', 'SUPER_ADMIN') and request.user.role != 'SUPER_ADMIN':
            return Response(
                {"detail": "Only Super Admins can assign Admin roles."},
                status=status.HTTP_403_FORBIDDEN,
            )

        data       = request.data.copy()
        serializer = UserUpdateSerializer(user, data=data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        if 'profile_image' in request.FILES:
            user.profile_image = request.FILES['profile_image']
        elif data.get('profile_image') in (None, 'remove', ''):
            user.profile_image = None

        serializer.save()
        user.save()

        return Response(
            UserSerializer(user, context={'request': request}).data,
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# User Delete – DELETE /api/users/<pk>/delete/
# ---------------------------------------------------------------------------
class UserDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        if request.user.role not in ('SUPER_ADMIN', 'ADMIN'):
            return Response(
                {"detail": "You do not have permission to delete users."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if request.user.id == pk:
            return Response(
                {"detail": "You cannot delete your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # ADMIN can only delete their own users
        if request.user.role == 'ADMIN':
            if user.role != 'USER' or user.admin_owner_id != request.user.id:
                return Response(
                    {"detail": "Admins can only delete their own User accounts."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        user.delete()
        return Response({"message": "User deleted successfully"}, status=status.HTTP_204_NO_CONTENT)

# ---------------------------------------------------------------------------
# License Proxy – GET /api/license/customers/
#
# Proxies the request to the external license server so the browser never
# has to make a cross-origin request (which would be blocked by CORS).
# Only SUPER_ADMIN can call this endpoint.
# ---------------------------------------------------------------------------
import requests as http_requests

class LicenseCustomersProxyView(APIView):
    permission_classes = [IsAuthenticated]

    LICENSE_API_URL = "https://activate.imcbs.com/mobileapp/api/project/trellisco/"

    def get(self, request):
        if request.user.role != 'SUPER_ADMIN':
            return Response(
                {"detail": "Only Super Admins can access license data."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            resp = http_requests.get(self.LICENSE_API_URL, timeout=10)
            resp.raise_for_status()
            return Response(resp.json(), status=resp.status_code)
        except http_requests.exceptions.Timeout:
            return Response(
                {"detail": "License server timed out. Please try again."},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except http_requests.exceptions.RequestException as e:
            return Response(
                {"detail": f"Could not reach license server: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )


# ---------------------------------------------------------------------------
# Corporate Client List Proxy – GET /api/corporate-clients/
#
# Returns the full corporate → shops → client_id tree from the license server.
# Used by the frontend to build the client-switcher dropdown.
# ---------------------------------------------------------------------------
CORPORATE_CLIENT_API_URL = "https://activate.imcbs.com/corporate-clientid/list/"

def _fetch_corporate_list():
    """Fetch and return the corporate client list from the license server.
    Returns (data_list, error_response) — one of them will be None."""
    try:
        resp = http_requests.get(CORPORATE_CLIENT_API_URL, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("data", []), None
    except http_requests.exceptions.Timeout:
        return None, Response(
            {"detail": "License server timed out. Please try again."},
            status=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except http_requests.exceptions.RequestException as e:
        return None, Response(
            {"detail": f"Could not reach license server: {str(e)}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )


def _find_corporate_for_client(data_list, client_id):
    """Return the corporate entry that contains the given client_id, or None."""
    for corporate in data_list:
        for shop in corporate.get("shops", []):
            if shop.get("client_id") == client_id:
                return corporate
    return None


class CorporateClientListView(APIView):
    """
    GET /api/corporate-clients/

    Returns the corporate group that the currently-logged-in ADMIN belongs to,
    along with all sibling client_ids under the same corporate_id.
    Each sibling entry also indicates whether it is registered in this HRMS.

    Falls back gracefully when the license server is unreachable or the
    client_id is not found in the corporate list — returns a single-item
    list containing only the current client so the UI never hard-errors.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Resolve the effective client_id and company name for this session
        if user.role == 'ADMIN':
            current_client_id = user.client_id
            current_company_name = user.company_name or user.username
        else:
            return Response(
                {"detail": "Only Admin accounts can switch clients."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not current_client_id:
            return Response(
                {"detail": "No client ID is associated with this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Try to fetch the full corporate list from the license server ──────
        # If it fails (network error, timeout, or client_id not found), we fall
        # back to a single-item response so the UI still works.
        data_list, err = _fetch_corporate_list()

        corporate = None
        if data_list:
            corporate = _find_corporate_for_client(data_list, current_client_id)

        # ── Fallback: client_id not in license server or server unreachable ───
        if not corporate:
            # Return a minimal single-shop response so the switcher shows
            # "only you" rather than crashing with an error.
            return Response({
                "corporate_id":      None,
                "corporate_name":    current_company_name,
                "current_client_id": current_client_id,
                "license_server_error": (
                    "This client ID is not registered in the license server. "
                    "Switching is unavailable."
                ) if not err else None,
                "shops": [
                    {
                        "shop_name":             current_company_name,
                        "client_id":             current_client_id,
                        "projects":              [],
                        "is_registered_in_hrms": True,
                        "is_current":            True,
                    }
                ],
            })

        # ── Normal path: build the full sibling list (registered in HRMS only) ──
        shops_with_status = []
        for shop in corporate.get("shops", []):
            cid = shop.get("client_id")
            is_registered = User.objects.filter(
                client_id=cid, role__in=('ADMIN', 'SUPER_ADMIN')
            ).exists()
            # Only include shops that are registered in this HRMS
            if not is_registered and cid != current_client_id:
                continue
            shops_with_status.append({
                "shop_name":             shop.get("shop_name"),
                "client_id":             cid,
                "projects":              shop.get("projects", []),
                "is_registered_in_hrms": is_registered,
                "is_current":            cid == current_client_id,
            })

        return Response({
            "corporate_id":      corporate.get("corporate_id"),
            "corporate_name":    corporate.get("corporate_name"),
            "current_client_id": current_client_id,
            "shops":             shops_with_status,
        })


# ---------------------------------------------------------------------------
# Switch Client – POST /api/switch-client/
#
# Allows an ADMIN (or USER) to switch to a different client_id that belongs
# to the SAME corporate group.
#
# Rules:
#   1. The target client_id must be in the same corporate group as the caller.
#   2. The target client_id must be registered as an ADMIN in this HRMS.
#   3. A new JWT token pair is issued scoped to the target admin's tenant.
#
# Request body:  { "target_client_id": "XXXXXXXXXXX" }
# ---------------------------------------------------------------------------
class SwitchClientView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        target_client_id = request.data.get("target_client_id", "").strip()

        if not target_client_id:
            return Response(
                {"detail": "target_client_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve the caller's current client_id
        if user.role == 'ADMIN':
            current_client_id = user.client_id
        else:
            return Response(
                {"detail": "Only Admin accounts can switch clients."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not current_client_id:
            return Response(
                {"detail": "No client ID is associated with this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cannot switch to the same client
        if target_client_id == current_client_id:
            return Response(
                {"detail": "You are already logged in to this client."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fetch corporate list and validate both client_ids are in the same group
        data_list, err = _fetch_corporate_list()
        if err:
            return err

        current_corporate = _find_corporate_for_client(data_list, current_client_id)
        if not current_corporate:
            return Response(
                {"detail": f"Your current client ID '{current_client_id}' is not found in the license server."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check target belongs to the same corporate
        target_corporate = _find_corporate_for_client(data_list, target_client_id)
        if not target_corporate:
            return Response(
                {"detail": f"Target client ID '{target_client_id}' is not registered in the license server."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if current_corporate.get("corporate_id") != target_corporate.get("corporate_id"):
            return Response(
                {
                    "detail": (
                        f"Client ID '{target_client_id}' belongs to a different corporate group "
                        f"({target_corporate.get('corporate_name')}) and cannot be accessed from "
                        f"your current corporate ({current_corporate.get('corporate_name')})."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check the target client_id is registered as an ADMIN in this HRMS
        try:
            target_admin = User.objects.get(
                client_id=target_client_id,
                role__in=('ADMIN', 'SUPER_ADMIN'),
            )
        except User.DoesNotExist:
            # Find the shop name for a friendlier error message
            shop_name = next(
                (s.get("shop_name") for s in target_corporate.get("shops", [])
                 if s.get("client_id") == target_client_id),
                target_client_id,
            )
            return Response(
                {
                    "detail": (
                        f"Client '{shop_name}' (ID: {target_client_id}) is not yet registered "
                        f"in this HRMS system. Please ask the system administrator to create "
                        f"an admin account for this client first."
                    ),
                    "error_code": "CLIENT_NOT_REGISTERED",
                    "target_client_id": target_client_id,
                    "shop_name": shop_name,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not target_admin.is_active:
            return Response(
                {"detail": f"The admin account for client '{target_client_id}' is inactive."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Issue new tokens for the target admin's tenant
        refresh = RefreshToken.for_user(target_admin)
        setup_completed = CompanySettings.objects.filter(owner=target_admin).exists()

        # Find the shop name for the target
        shop_name = next(
            (s.get("shop_name") for s in target_corporate.get("shops", [])
             if s.get("client_id") == target_client_id),
            target_admin.company_name or target_client_id,
        )

        return Response({
            "access":    str(refresh.access_token),
            "refresh":   str(refresh),
            "role":      target_admin.role.lower(),
            "username":  target_admin.username,
            "client_id": target_client_id,
            "company_name": target_admin.company_name or shop_name,
            "shop_name": shop_name,
            "corporate_id":   current_corporate.get("corporate_id"),
            "corporate_name": current_corporate.get("corporate_name"),
            "company_setup_completed": setup_completed,
            "switched_from": current_client_id,
            "switched_to":   target_client_id,
        })


# ---------------------------------------------------------------------------
# Company Settings - GET/PATCH /api/company-settings/current/
#
# ADMIN owns one settings record for the client_id.
# USER reads the settings owned by their admin_owner.
# ---------------------------------------------------------------------------
class CompanySettingsCurrentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant_admin = get_tenant_admin(request.user)
        if not tenant_admin:
            return Response(
                {"detail": "Company settings are not available for this account."},
                status=status.HTTP_403_FORBIDDEN,
            )

        settings_obj = CompanySettings.objects.filter(owner=tenant_admin).first()
        if not settings_obj:
            return Response(
                {
                    "client_id": tenant_admin.client_id,
                    "setup_completed": False,
                    "name": tenant_admin.company_name or "",
                    "tagline": "",
                    "email": "",
                    "phone": "",
                    "website": "",
                    "address": "",
                    "logo": "",
                    "primaryColor": "#6d3ef6",
                    "currency": "USD",
                    "timezone": "UTC",
                },
                status=status.HTTP_200_OK,
            )

        serializer = CompanySettingsSerializer(settings_obj)
        return Response(serializer.data)

    def patch(self, request):
        if request.user.role != 'ADMIN':
            return Response(
                {"detail": "Only admins can update company settings."},
                status=status.HTTP_403_FORBIDDEN,
            )

        settings_obj, _ = CompanySettings.objects.get_or_create(
            owner=request.user,
            defaults={"name": request.user.company_name or request.user.username},
        )
        serializer = CompanySettingsSerializer(settings_obj, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save(owner=request.user)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Change Password – POST /api/change-password/
#
# Works for all roles (SUPER_ADMIN, ADMIN, USER).
# Requires the user to supply their current password for verification.
# ---------------------------------------------------------------------------
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        current_password = request.data.get('current_password', '').strip()
        new_password     = request.data.get('new_password', '').strip()
        confirm_password = request.data.get('confirm_password', '').strip()

        if not current_password or not new_password or not confirm_password:
            return Response(
                {"detail": "current_password, new_password, and confirm_password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.check_password(current_password):
            return Response(
                {"detail": "Current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_password != confirm_password:
            return Response(
                {"detail": "new_password and confirm_password do not match."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(new_password) < 6:
            return Response(
                {"detail": "New password must be at least 6 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if current_password == new_password:
            return Response(
                {"detail": "New password must be different from the current password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.plain_password = new_password   # keep plain_password in sync
        user.save()

        return Response(
            {"detail": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )