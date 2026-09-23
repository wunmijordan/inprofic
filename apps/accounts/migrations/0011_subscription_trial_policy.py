from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_platform_events"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SubscriptionPolicySettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("general_trial_days", models.PositiveSmallIntegerField(default=30, help_text="Default number of days for new eligible INPROFIC free trials.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="subscription_policy_settings_updates", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "subscription policy setting",
                "verbose_name_plural": "subscription policy settings",
            },
        ),
        migrations.CreateModel(
            name="FounderTrialGrant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("days", models.PositiveSmallIntegerField()),
                ("previous_ends_at", models.DateTimeField(blank=True, null=True)),
                ("granted_ends_at", models.DateTimeField()),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("granted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="founder_trial_grants_made", to=settings.AUTH_USER_MODEL)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="founder_trial_grants", to="accounts.subscriptionplan")),
                ("subscription", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="founder_trial_grants", to="accounts.businesssubscription")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
