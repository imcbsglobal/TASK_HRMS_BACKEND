from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MenuViewSet, UserMenuAccessViewSet, UserAccessControlViewSet

router = DefaultRouter()
router.register(r'menus', MenuViewSet, basename='menu')
router.register(r'menu-access', UserMenuAccessViewSet, basename='menu-access')
router.register(r'user-access', UserAccessControlViewSet, basename='user-access')

urlpatterns = [
    path('', include(router.urls)),
]