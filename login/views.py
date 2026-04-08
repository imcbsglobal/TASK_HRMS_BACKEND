from django.contrib.auth import get_user_model
from rest_framework.views        import APIView
from rest_framework.response     import Response
from rest_framework              import status, permissions
from rest_framework.permissions  import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from .serializers import (
    UserSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    LoginSerializer,
)

User = get_user_model()


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

        # ── Issue tokens ───────────────────────────────────────────────────
        refresh         = RefreshToken.for_user(user)
        normalized_role = user.role.lower()

        return Response({
            "access":    str(refresh.access_token),
            "refresh":   str(refresh),
            "role":      normalized_role,
            "username":  user.username,
            "client_id": user.client_id,
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