from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0017_online_fulfilment_delivery_batches"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="storefrontproduct",
            index=models.Index(fields=["business", "published"], name="sfprod_biz_published_idx"),
        ),
        migrations.AddIndex(
            model_name="commerceintake",
            index=models.Index(fields=["business", "-created_at"], name="intake_biz_recent_idx"),
        ),
        migrations.AddIndex(
            model_name="commercecheckoutsession",
            index=models.Index(fields=["business", "-created_at"], name="checkout_biz_recent_idx"),
        ),
        migrations.AddIndex(
            model_name="commercepayment",
            index=models.Index(fields=["business", "status", "-created_at"], name="payment_biz_status_idx"),
        ),
        migrations.AddIndex(
            model_name="commercepayment",
            index=models.Index(fields=["business", "-created_at"], name="payment_biz_recent_idx"),
        ),
        migrations.AddIndex(
            model_name="deliveryassignment",
            index=models.Index(fields=["business", "-created_at"], name="delivery_biz_recent_idx"),
        ),
        migrations.AddIndex(
            model_name="deliveryassignment",
            index=models.Index(fields=["business", "driver", "status", "-created_at"], name="delivery_driver_state_idx"),
        ),
        migrations.AddIndex(
            model_name="deliveryassignment",
            index=models.Index(fields=["business", "batch", "batch_stop_sequence"], name="delivery_batch_stop_idx"),
        ),
        migrations.AddIndex(
            model_name="deliveryassignment",
            index=models.Index(
                fields=["business", "provider_account", "provider_order_id"],
                condition=~models.Q(provider_order_id=""),
                name="delivery_provider_order_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="deliveryassignment",
            index=models.Index(
                fields=["business", "provider_account", "external_reference"],
                condition=~models.Q(external_reference=""),
                name="delivery_provider_ref_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="deliveryevent",
            index=models.Index(fields=["assignment", "created_at"], name="delivery_event_time_idx"),
        ),
        migrations.AddIndex(
            model_name="deliverybatch",
            index=models.Index(fields=["business", "driver", "status", "-created_at"], name="delbatch_driver_state_idx"),
        ),
        migrations.AddIndex(
            model_name="deliverymessage",
            index=models.Index(fields=["assignment", "created_at"], name="delivery_msg_time_idx"),
        ),
        migrations.AddIndex(
            model_name="deliveryissue",
            index=models.Index(fields=["assignment", "-created_at"], name="delivery_issue_assign_idx"),
        ),
        migrations.AddIndex(
            model_name="commercenotification",
            index=models.Index(fields=["business", "recipient_user", "-created_at"], name="notice_biz_user_time_idx"),
        ),
        migrations.AddIndex(
            model_name="commercepushsubscription",
            index=models.Index(fields=["user", "active"], name="push_user_active_idx"),
        ),
    ]
