from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_subscription_promotions_and_storefront_access"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MarketingPromoCampaign",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="Founder-only label for this campaign creative.", max_length=100)),
                ("content_html", models.TextField(help_text="Sanitised rich text rendered in the public promo stage.")),
                ("cta_label", models.CharField(default="See promotional plans", max_length=80)),
                ("animation_style", models.CharField(choices=[("kinetic", "Kinetic reveal"), ("spotlight", "Spotlight sweep"), ("parallax", "Parallax float"), ("marquee", "Marquee energy")], default="kinetic", max_length=16)),
                ("theme", models.CharField(choices=[("midnight", "Midnight"), ("ember", "Ember"), ("paper", "Paper"), ("gold", "Gold")], default="midnight", max_length=16)),
                ("priority", models.PositiveSmallIntegerField(default=50, help_text="Higher numbers appear first if several promotions are live.")),
                ("image", models.ImageField(blank=True, upload_to="marketing/promotions/%Y/%m/")),
                ("video", models.FileField(blank=True, upload_to="marketing/promotions/%Y/%m/", validators=[FileExtensionValidator(["mp4", "webm", "mov"])])),
                ("video_poster", models.ImageField(blank=True, upload_to="marketing/promotions/%Y/%m/")),
                ("media_alt", models.CharField(blank=True, default="", max_length=180)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="marketing_promo_campaigns_created", to=settings.AUTH_USER_MODEL)),
                ("promotion", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="marketing_campaign", to="accounts.subscriptionpromotion")),
            ],
            options={"ordering": ["-priority", "id"]},
        ),
        migrations.AddIndex(
            model_name="marketingpromocampaign",
            index=models.Index(fields=["active", "priority"], name="marketing_campaign_idx"),
        ),
    ]
