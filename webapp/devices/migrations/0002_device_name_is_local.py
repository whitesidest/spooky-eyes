from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="name_is_local",
            field=models.BooleanField(
                default=False,
                help_text="The name lives only here (the board's firmware couldn't store it); boards never overwrite it",
            ),
        ),
    ]
