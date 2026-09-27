from datetime import date

from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


def seed_privacy_policy(apps, schema_editor):
    PlatformPrivacyPolicy = apps.get_model("accounts", "PlatformPrivacyPolicy")
    PlatformPrivacyPolicy.objects.get_or_create(
        pk=1,
        defaults={"effective_date": date(2026, 9, 27)},
    )


def unseed_privacy_policy(apps, schema_editor):
    PlatformPrivacyPolicy = apps.get_model("accounts", "PlatformPrivacyPolicy")
    PlatformPrivacyPolicy.objects.filter(pk=1).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0016_marketing_trust_strip"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformPrivacyPolicy",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("body_html", models.TextField(blank=True, default="")),
                ("effective_date", models.DateField(blank=True, null=True)),
                ("source_file", models.FileField(blank=True, help_text="Optional HTML, TXT or Markdown source retained with the current policy.", upload_to="platform/privacy/", validators=[django.core.validators.FileExtensionValidator(["html", "htm", "txt", "md", "markdown"])])),
                ("source_filename", models.CharField(blank=True, default="", max_length=255)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="platform_privacy_policy_updates", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "platform privacy policy",
                "verbose_name_plural": "platform privacy policy",
            },
        ),
        migrations.RunPython(seed_privacy_policy, unseed_privacy_policy),
    ]
