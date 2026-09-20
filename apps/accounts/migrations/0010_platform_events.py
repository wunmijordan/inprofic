from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_platform_integration_settings"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="customuser",
            name="fullname",
            field=models.CharField(max_length=160, verbose_name="Full Name"),
        ),
        migrations.CreateModel(
            name="PlatformEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("signup_view", "Signup viewed"), ("registration_completed", "Registration completed"), ("login", "Login"), ("logout", "Logout"), ("module_view", "Module viewed"), ("subscription_started", "Subscription started"), ("subscription_trial_started", "Paid-plan trial started"), ("subscription_changed", "Subscription changed"), ("subscription_paid", "Subscription paid"), ("subscription_founder_grant", "Founder subscription grant")], db_index=True, max_length=40)),
                ("session_key", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("module", models.CharField(blank=True, db_index=True, default="", max_length=40)),
                ("route_name", models.CharField(blank=True, default="", max_length=100)),
                ("path", models.CharField(blank=True, default="", max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("occurred_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("business", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="platform_events", to="core.business")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="platform_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-occurred_at", "-id"]},
        ),
        migrations.AddIndex(model_name="platformevent", index=models.Index(fields=["event_type", "occurred_at"], name="platform_event_type_time")),
        migrations.AddIndex(model_name="platformevent", index=models.Index(fields=["business", "occurred_at"], name="platform_event_business_time")),
    ]
