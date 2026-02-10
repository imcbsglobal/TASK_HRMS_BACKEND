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
# ---------------------------------------------------------------------------
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']

        refresh = RefreshToken.for_user(user)

        # FIXED: Return role in lowercase with underscore for frontend consistency
        # Convert SUPER_ADMIN -> super_admin, ADMIN -> admin, USER -> user
        normalized_role = user.role.lower()

        return Response({
            "access":   str(refresh.access_token),
            "refresh":  str(refresh),
            "role":     normalized_role,  # Send normalized role
            "username": user.username,
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
                token.blacklist()                      # requires simplejwt blacklist app
            except TokenError:
                pass                                   # token already invalid – that's fine
        return Response({"message": "Logged out successfully"}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# User List – GET /api/users/
# ---------------------------------------------------------------------------
class UserListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        users      = User.objects.all().order_by('id')
        serializer = UserSerializer(users, many=True, context={'request': request})
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# User Create – POST /api/users/create/
# ---------------------------------------------------------------------------
class UserCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = UserCreateSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "User created successfully"}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# User Update – PATCH /api/users/<pk>/update/
# ---------------------------------------------------------------------------
class UserUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        # ── permission guard: only SUPER_ADMIN or the user themselves ──
        if request.user.role != 'SUPER_ADMIN' and request.user.id != pk:
            return Response(
                {"detail": "You do not have permission to edit this user."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Merge multipart data + files into a mutable dict
        data = request.data.copy()

        serializer = UserUpdateSerializer(user, data=data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Handle file upload separately (not part of the serializer CharField)
        if 'profile_image' in request.FILES:
            user.profile_image = request.FILES['profile_image']
        elif data.get('profile_image') in (None, 'remove', ''):
            user.profile_image = None

        serializer.save()                              # saves the other fields
        user.save()                                    # persists the image change

        # Return updated user via the read serializer
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
        # ── permission guard: only SUPER_ADMIN ──
        if request.user.role != 'SUPER_ADMIN':
            return Response(
                {"detail": "Only Super Admins can delete users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Prevent deleting yourself
        if request.user.id == pk:
            return Response(
                {"detail": "You cannot delete your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        user.delete()
        return Response({"message": "User deleted successfully"}, status=status.HTTP_204_NO_CONTENT)