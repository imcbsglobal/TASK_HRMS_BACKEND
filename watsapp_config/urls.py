

from django.urls import path
from .views import WhatsAppConfigView, WhatsAppTestView

urlpatterns = [
    path('config/', WhatsAppConfigView.as_view(), name='whatsapp-config'),
    path('test/',   WhatsAppTestView.as_view(),   name='whatsapp-test'),
]