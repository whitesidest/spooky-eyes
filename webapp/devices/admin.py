from django.contrib import admin

from .models import Device, Group, Scene


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "device_id", "host", "fw", "last_seen")
    search_fields = ("name", "device_id", "host")


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    filter_horizontal = ("devices",)


@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin):
    list_display = ("name", "group")
