from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

def backfill_trial_identities(apps, schema_editor):
    Business = apps.get_model("core", "Business")
    UserBusiness = apps.get_model("accounts", "UserBusiness")
    TrialIdentity = apps.get_model("accounts", "BusinessTrialIdentity")
    SignupContact = apps.get_model("accounts", "FounderSignupContactState")
    for business in Business.objects.all().iterator():
        membership = UserBusiness.objects.filter(business_id=business.pk, role__key="business_admin", active=True).select_related("user").order_by("id").first()
        user = membership.user if membership else None
        contact = SignupContact.objects.filter(business_id_snapshot=business.pk, deleted_at__isnull=True).order_by("id").first()
        email = ((getattr(contact, "signup_email", "") if contact else "") or getattr(user, "email", "") or "").strip().casefold()
        phone = "".join(ch for ch in (getattr(user, "phone", "") or "") if ch.isdigit())
        original_name = (getattr(contact, "business_name", "") if contact else "") or business.name or ""
        name_key = " ".join(original_name.strip().casefold().split())
        TrialIdentity.objects.get_or_create(business_id=business.pk, defaults={"email_key": email, "phone_key": phone, "business_name_key": name_key})


def seed_default_topic(apps, schema_editor):
    Topic = apps.get_model("accounts", "PlatformMailTemplate")
    Topic.objects.get_or_create(
        name="Business update",
        defaults={
            "subject": "An update from INPROFIC",
            "heading": "An update for {{ business_name }}",
            "body_html": "<p>We have an update for <strong>{{ business_name }}</strong>.</p><p>Your current INPROFIC service is <strong>{{ service }}</strong> on the <strong>{{ plan_name }}</strong> plan.</p><p>Use this topic as a starting point and replace this copy with the message you want to send.</p>",
            "cta_label": "", "cta_url": "", "active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0013_founder_signup_contact_snapshot")]
    operations = [
        migrations.CreateModel(name="BusinessTrialIdentity", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("email_key", models.CharField(blank=True, db_index=True, default="", max_length=254)),
            ("phone_key", models.CharField(blank=True, db_index=True, default="", max_length=40)),
            ("business_name_key", models.CharField(db_index=True, max_length=160)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("business", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="trial_identity", to="core.business")),
        ]),
        migrations.AddField(model_name="customuser", name="platform_mail_access", field=models.BooleanField(default=False, help_text="Project-level access to the INPROFIC mailing workspace only.")),
        migrations.CreateModel(name="PlatformMailTemplate", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("name", models.CharField(max_length=120, unique=True)), ("subject", models.CharField(max_length=180)), ("heading", models.CharField(max_length=180)), ("body_html", models.TextField(help_text="Email body HTML inside the branded INPROFIC shell.")), ("cta_label", models.CharField(blank=True, default="", max_length=80)), ("cta_url", models.URLField(blank=True, default="")), ("active", models.BooleanField(default=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="platform_mail_templates_created", to=settings.AUTH_USER_MODEL))], options={"ordering":["name"]}),
        migrations.CreateModel(name="PlatformMailCampaign", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("subject", models.CharField(max_length=180)), ("heading", models.CharField(max_length=180)), ("body_html", models.TextField()), ("cta_label", models.CharField(blank=True, default="", max_length=80)), ("cta_url", models.URLField(blank=True, default="")), ("status", models.CharField(choices=[("draft","Draft"),("queued","Queued"),("sending","Sending"),("sent","Sent"),("partial","Partially sent")], default="draft", max_length=12)), ("total_recipients", models.PositiveIntegerField(default=0)), ("sent_count", models.PositiveIntegerField(default=0)), ("failed_count", models.PositiveIntegerField(default=0)), ("created_at", models.DateTimeField(auto_now_add=True)), ("queued_at", models.DateTimeField(blank=True, null=True)), ("completed_at", models.DateTimeField(blank=True, null=True)), ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="platform_mail_campaigns_created", to=settings.AUTH_USER_MODEL)), ("template", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="campaigns", to="accounts.platformmailtemplate"))], options={"ordering":["-created_at"]}),
        migrations.CreateModel(name="PlatformMailRecipient", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("business_id_snapshot", models.PositiveBigIntegerField(blank=True, db_index=True, null=True)), ("business_name", models.CharField(max_length=180)), ("service", models.CharField(blank=True, default="", max_length=120)), ("plan_name", models.CharField(blank=True, default="", max_length=120)), ("recipient_name", models.CharField(blank=True, default="", max_length=160)), ("email", models.EmailField(max_length=254)), ("status", models.CharField(choices=[("pending","Pending"),("sent","Sent"),("failed","Failed")], default="pending", max_length=10)), ("error", models.CharField(blank=True, default="", max_length=255)), ("sent_at", models.DateTimeField(blank=True, null=True)), ("campaign", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recipients", to="accounts.platformmailcampaign"))], options={"ordering":["id"]}),
        migrations.AddConstraint(model_name="platformmailrecipient", constraint=models.UniqueConstraint(fields=("campaign","business_id_snapshot"), name="unique_platform_campaign_business")),
        migrations.RunPython(backfill_trial_identities, migrations.RunPython.noop),
        migrations.RunPython(seed_default_topic, migrations.RunPython.noop),
    ]
