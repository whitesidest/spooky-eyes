from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("boards/<str:device_id>/", views.board, name="board"),
    path("puppeteer/", views.puppeteer, name="puppeteer"),
    path("gaze/", views.gaze, name="gaze"),
    path("scenes/", views.scenes, name="scenes"),
    path("api/devices", views.api_devices, name="api-devices"),
    path("api/devices/<str:device_id>/preview", views.api_device_preview, name="api-device-preview"),
    path("api/devices/<str:device_id>", views.api_device, name="api-device"),
    path("api/state", views.api_state, name="api-state"),
    path("api/action", views.api_action, name="api-action"),
    path("api/themes", views.api_themes, name="api-themes"),
    path("api/scenes", views.api_scenes, name="api-scenes"),
    path("api/scenes/<int:pk>", views.api_scene, name="api-scene"),
    path("api/scenes/<int:pk>/apply", views.api_scene_apply, name="api-scene-apply"),
    path("api/groups", views.api_groups, name="api-groups"),
    path("api/groups/<int:pk>", views.api_group, name="api-group"),
    path("api/scan", views.api_scan, name="api-scan"),
]
