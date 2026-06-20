from django.urls import path
from .views import DeviceListView, DeviceDeleteView, LicenseInfoView

urlpatterns = [
    # GET  /api/device-control/devices/             – list devices for current admin
    path('devices/', DeviceListView.as_view(), name='device-list'),

    # DELETE /api/device-control/devices/<device_id>/  – deregister a device
    path('devices/<str:device_id>/', DeviceDeleteView.as_view(), name='device-delete'),

    # GET /api/device-control/license-info/         – license summary
    path('license-info/', LicenseInfoView.as_view(), name='license-info'),
]
