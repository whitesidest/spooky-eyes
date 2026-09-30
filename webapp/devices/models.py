from django.db import models
from django.utils import timezone

ONLINE_WINDOW_S = 120


class Device(models.Model):
    device_id = models.CharField(max_length=12, unique=True, help_text="12 lowercase hex digits of the MAC")
    name = models.CharField(max_length=100)
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=80)
    model = models.CharField(max_length=50, blank=True)
    fw = models.CharField(max_length=30, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_state = models.JSONField(default=dict, blank=True)
    info = models.JSONField(default=dict, blank=True, help_text="Last /api/info (themes, moods)")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def base_url(self):
        return f"http://{self.host}:{self.port}"

    @property
    def online(self):
        return bool(self.last_seen) and (timezone.now() - self.last_seen).total_seconds() < ONLINE_WINDOW_S

    @property
    def themes(self):
        return self.info.get("themes", [])

    @property
    def preview_path(self):
        """Live-picture path the board advertises (simulators only), or None."""
        path = self.info.get("preview")
        if isinstance(path, str) and path.startswith("/") and not path.startswith("//"):
            return path
        return None

    @property
    def moods(self):
        return self.info.get("moods", ["neutral", "angry", "surprised", "sleepy", "asleep"])


class Group(models.Model):
    name = models.CharField(max_length=100, unique=True)
    devices = models.ManyToManyField(Device, related_name="groups", blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Scene(models.Model):
    """A saved look: a partial state (and optional action) applied to a group or every board."""

    name = models.CharField(max_length=100, unique=True)
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.SET_NULL,
                              help_text="Leave empty to target every board")
    state = models.JSONField(default=dict, blank=True, help_text='e.g. {"theme": "sauron", "mood": "angry"}')
    action = models.JSONField(null=True, blank=True, help_text='e.g. {"action": "look", "x": -1, "y": 0, "duration": 5}')

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def targets(self):
        return list(self.group.devices.all()) if self.group else list(Device.objects.all())
