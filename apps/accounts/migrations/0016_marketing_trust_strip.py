# Generated manually for Founder-controlled marketing trust logos.

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_platform_mail_delivery_state"),
    ]

    operations = [
        migrations.CreateModel(
            name="MarketingTrustSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="marketing_trust_settings_updates", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "marketing trust setting", "verbose_name_plural": "marketing trust settings"},
        ),
        migrations.CreateModel(
            name="MarketingTrustLogo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("logo", models.ImageField(upload_to="marketing/trusted-businesses/%Y/%m/", validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp"])])),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="marketing_trust_logos_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["sort_order", "name", "id"]},
        ),
    ]
