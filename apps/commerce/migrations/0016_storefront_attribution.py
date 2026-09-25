from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0015_alter_commercesettings_notification_sound_enabled"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="commerceintake", name="attribution_source", field=models.CharField(blank=True, default="direct", max_length=80)),
        migrations.AddField(model_name="commerceintake", name="attribution_medium", field=models.CharField(blank=True, default="", max_length=80)),
        migrations.AddField(model_name="commerceintake", name="attribution_campaign", field=models.CharField(blank=True, default="", max_length=120)),
        migrations.AddField(model_name="commerceintake", name="attribution_content", field=models.CharField(blank=True, default="", max_length=120)),
        migrations.AddField(model_name="commerceintake", name="attribution_term", field=models.CharField(blank=True, default="", max_length=120)),
        migrations.AddField(model_name="commerceintake", name="attribution_referrer", field=models.CharField(blank=True, default="", max_length=500)),
        migrations.AddField(model_name="commercecheckoutsession", name="attribution_source", field=models.CharField(blank=True, default="direct", max_length=80)),
        migrations.AddField(model_name="commercecheckoutsession", name="attribution_medium", field=models.CharField(blank=True, default="", max_length=80)),
        migrations.AddField(model_name="commercecheckoutsession", name="attribution_campaign", field=models.CharField(blank=True, default="", max_length=120)),
        migrations.AddField(model_name="commercecheckoutsession", name="attribution_content", field=models.CharField(blank=True, default="", max_length=120)),
        migrations.AddField(model_name="commercecheckoutsession", name="attribution_term", field=models.CharField(blank=True, default="", max_length=120)),
        migrations.AddField(model_name="commercecheckoutsession", name="attribution_referrer", field=models.CharField(blank=True, default="", max_length=500)),
        migrations.CreateModel(
            name="StorefrontAttributionVisit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("visit_key", models.CharField(max_length=64)),
                ("target", models.CharField(choices=[("storefront", "Storefront"), ("order_now", "Order Now")], default="storefront", max_length=16)),
                ("attribution_source", models.CharField(blank=True, default="direct", max_length=80)),
                ("attribution_medium", models.CharField(blank=True, default="", max_length=80)),
                ("attribution_campaign", models.CharField(blank=True, default="", max_length=120)),
                ("attribution_content", models.CharField(blank=True, default="", max_length=120)),
                ("attribution_term", models.CharField(blank=True, default="", max_length=120)),
                ("attribution_referrer", models.CharField(blank=True, default="", max_length=500)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(model_name="storefrontattributionvisit", constraint=models.UniqueConstraint(fields=("business", "visit_key", "target"), name="unique_storefront_attribution_visit")),
        migrations.AddIndex(model_name="storefrontattributionvisit", index=models.Index(fields=["business", "created_at"], name="storefront_attr_visit_idx")),
    ]
