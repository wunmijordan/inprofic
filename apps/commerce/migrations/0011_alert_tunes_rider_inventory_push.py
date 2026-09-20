from django.db import migrations, models


TUNE_CHOICES = [
    ("gentle_chime", "Gentle chime"),
    ("double_ping", "Double ping"),
    ("urgent_pulse", "Urgent pulse"),
    ("hard_buzzer", "Hard buzzer · aggressive"),
    ("alarm_buzzer", "Alarm buzzer · very aggressive"),
]


class Migration(migrations.Migration):
    dependencies = [("commerce", "0010_notification_sound_repeat")]

    operations = [
        migrations.AddField(
            model_name="commercesettings",
            name="notification_sound_tune",
            field=models.CharField(
                choices=TUNE_CHOICES,
                default="double_ping",
                help_text="Foreground alert tune used by open INPROFIC pages and the installed app.",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="deliverysettings",
            name="rider_alert_sound_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Play repeating foreground alert sounds for riders who have an assigned delivery requiring attention.",
            ),
        ),
        migrations.AddField(
            model_name="deliverysettings",
            name="rider_alert_sound_repeat_minutes",
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text="Repeat a rider alert while their unread delivery activity remains. Use 0 for new-alert sound only.",
            ),
        ),
        migrations.AddField(
            model_name="deliverysettings",
            name="rider_alert_sound_tune",
            field=models.CharField(
                choices=TUNE_CHOICES,
                default="urgent_pulse",
                help_text="Foreground alert tune used for rider-only notification sessions.",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="commercenotification",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("checkout_received", "Checkout received"),
                    ("intake_received", "Order received"),
                    ("payment_started", "Payment started"),
                    ("payment_claim", "Payment claim submitted"),
                    ("payment_confirmed", "Payment confirmed"),
                    ("payment_review", "Payment needs review"),
                    ("delivery_created", "Delivery created"),
                    ("delivery_assigned", "Delivery assigned"),
                    ("delivery_status", "Delivery status changed"),
                    ("delivery_switch", "Delivery method switched"),
                    ("delivery_issue", "Delivery issue raised"),
                    ("delivery_provider", "Delivery provider update"),
                    ("inventory_alert", "Inventory stock alert"),
                ],
                max_length=28,
            ),
        ),
    ]
