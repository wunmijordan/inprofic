from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0012_founder_signup_contact_state")]

    operations = [
        migrations.AddField(
            model_name="foundersignupcontactstate",
            name="signup_email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="foundersignupcontactstate",
            name="signup_name",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="foundersignupcontactstate",
            name="business_name",
            field=models.CharField(blank=True, default="", max_length=180),
        ),
        migrations.AddField(
            model_name="foundersignupcontactstate",
            name="business_id_snapshot",
            field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="foundersignupcontactstate",
            name="vertical",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="foundersignupcontactstate",
            name="service",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="foundersignupcontactstate",
            name="signed_up_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
