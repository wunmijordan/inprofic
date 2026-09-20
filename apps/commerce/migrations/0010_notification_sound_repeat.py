from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0009_provider_neutral_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="commercesettings",
            name="notification_sound_repeat_minutes",
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text="Repeat the Commerce alert sound while unread activity remains. Use 0 to sound only when activity first appears.",
            ),
        ),
    ]
