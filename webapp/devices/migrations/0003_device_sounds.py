from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("devices", "0002_device_name_is_local")]

    operations = [
        migrations.AddField(
            model_name="device",
            name="sounds",
            field=models.JSONField(blank=True, default=dict, help_text="Last /api/sounds (built-ins, clips, free_bytes)"),
        ),
        migrations.AlterField(
            model_name="device",
            name="info",
            field=models.JSONField(blank=True, default=dict, help_text="Last /api/info (themes, moods, features)"),
        ),
    ]
