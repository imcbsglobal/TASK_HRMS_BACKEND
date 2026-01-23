from django.shortcuts import render


from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from .serializers import UserSerializer
from django.db.models import Q

User = get_user_model()

class LoginAPIView(APIView):
    """
    POST /api/login/
    Accepts JSON keys: email OR username OR identifier + password
    Returns: { refresh, access, user }
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        identifier = (
            (request.data.get('email') or '').strip() or
            (request.data.get('username') or '').strip() or
            (request.data.get('identifier') or '').strip()
        )
        password = request.data.get('password', '')

        if not identifier or not password:
            return Response({'detail': 'Identifier (email or username) and password are required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Try to find user:
        user = None

        # If it looks like an email, search by email first (case-insensitive)
        if '@' in identifier:
            try:
                user = User.objects.get(email__iexact=identifier)
            except User.DoesNotExist:
                user = None
        else:
            # Try username (case-sensitive depending on your model) then fallback to email
            try:
                user = User.objects.get(username=identifier)
            except User.DoesNotExist:
                try:
                    user = User.objects.get(email__iexact=identifier)
                except User.DoesNotExist:
                    user = None

        if not user:
            return Response({'detail': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.check_password(password):
            return Response({'detail': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.is_active:
            return Response({'detail': 'User account is disabled.'}, status=status.HTTP_403_FORBIDDEN)

        refresh = RefreshToken.for_user(user)
        access = str(refresh.access_token)

        return Response({
            'refresh': str(refresh),
            'access': access,
            'user': UserSerializer(user).data
        }, status=status.HTTP_200_OK)


class ProfileAPIView(APIView):
    """
    GET /api/profile/  (requires Authorization: Bearer <access_token>)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        serializer = UserSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # delete token
        request.user.auth_token.delete()
        return Response(
            {"message": "Logged out successfully"},
            status=status.HTTP_200_OK
        )
    

