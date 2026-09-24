from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_subscription_trial_policy"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FounderSignupContactState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email_key", models.CharField(max_length=254, unique=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("permanently_hidden", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="founder_signup_contact_state_changes", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["email_key"]},
        ),
    ]
