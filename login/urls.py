from django.urls import path
from .views import (
    LoginView,
    ProfileAPIView,
    LogoutView,
    UserCreateView,
    UserListView,
    UserUpdateView,
    UserDeleteView,
)
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    # 🔐 Auth
    path('login/',          LoginView.as_view(),         name='api_login'),
    path('token/refresh/',  TokenRefreshView.as_view(),  name='token_refresh'),
    path('profile/',        ProfileAPIView.as_view(),    name='profile'),
    path('logout/',         LogoutView.as_view(),        name='logout'),

    # 👥 User Management
    path('users/',                      UserListView.as_view(),   name='user_list'),
    path('users/create/',               UserCreateView.as_view(), name='user_create'),
    path('users/<int:pk>/update/',      UserUpdateView.as_view(), name='user_update'),
    path('users/<int:pk>/delete/',      UserDeleteView.as_view(), name='user_delete'),
]