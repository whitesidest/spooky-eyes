from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("gaze/", views.gaze, name="gaze"),
    path("api/devices", views.api_devices, name="api-devices"),
    path("api/devices/<str:device_id>/preview", views.api_device_preview, name="api-device-preview"),
    path("api/devices/<str:device_id>", views.api_device_delete, name="api-device-delete"),
    path("api/state", views.api_state, name="api-state"),
    path("api/action", views.api_action, name="api-action"),
    path("api/scenes/<int:pk>/apply", views.api_scene_apply, name="api-scene-apply"),
    path("api/scan", views.api_scan, name="api-scan"),
]
