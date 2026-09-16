import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def configure_plan_limits(apps, schema_editor):
    SubscriptionPlan = apps.get_model("accounts", "SubscriptionPlan")
    BusinessSubscription = apps.get_model("accounts", "BusinessSubscription")
    SubscriptionPlan.objects.filter(code="starter").update(
        monthly_price=0,
        trial_days=0,
        user_limit=1,
        additional_service_limit=0,
    )
    SubscriptionPlan.objects.filter(code="production", user_limit__isnull=True).update(
        user_limit=5,
        additional_service_limit=1,
    )
    BusinessSubscription.objects.filter(
        plan__code="starter",
        founder_lifetime=False,
    ).update(status="active", trial_ends_at=None, paid_until=None)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_delivery_rider_role"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriptionplan",
            name="additional_service_limit",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Maximum additional business/service profiles beyond the primary business. Leave blank for unlimited.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="user_limit",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Maximum unique active users across this subscription. Leave blank for unlimited.",
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="PaidPlanTrialClaim",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("credential_kind", models.CharField(choices=[("username", "Username"), ("email", "Email"), ("phone", "Phone")], max_length=12)),
                ("credential_fingerprint", models.CharField(max_length=64, unique=True)),
                ("claimed_at", models.DateTimeField(auto_now_add=True)),
                ("plan", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="trial_claims", to="accounts.subscriptionplan")),
                ("subscription", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="trial_claims", to="accounts.businesssubscription")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="paid_plan_trial_claims", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-claimed_at", "-id"]},
        ),
        migrations.RunPython(configure_plan_limits, migrations.RunPython.noop),
    ]
