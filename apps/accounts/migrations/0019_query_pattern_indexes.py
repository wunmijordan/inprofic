from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0018_privacy_policy_document_rendering"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="userbusiness",
            index=models.Index(fields=["user", "active", "business"], name="userbiz_user_active_idx"),
        ),
        migrations.AddIndex(
            model_name="userbusiness",
            index=models.Index(fields=["business", "active", "user"], name="userbiz_biz_active_idx"),
        ),
        migrations.AddIndex(
            model_name="businesssubscription",
            index=models.Index(fields=["status", "paid_until"], name="sub_status_paid_idx"),
        ),
        migrations.AddIndex(
            model_name="subscriptionpayment",
            index=models.Index(fields=["status", "-created_at"], name="subpay_status_time_idx"),
        ),
        migrations.AddIndex(
            model_name="platformmailrecipient",
            index=models.Index(fields=["status", "last_attempt_at"], name="mailrec_status_try_idx"),
        ),
        migrations.AddIndex(
            model_name="platformmailrecipient",
            index=models.Index(fields=["status", "campaign", "id"], name="mailrec_status_camp_idx"),
        ),
    ]
