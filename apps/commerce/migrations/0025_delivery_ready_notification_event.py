from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0024_deliveryassignment_driver_assigned_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="commercenotification",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("checkout_received", "Checkout received"),
                    ("intake_received", "Order received"),
                    ("payment_started", "Payment started"),
                    ("payment_claim", "Payment claim submitted"),
                    ("payment_confirmed", "Payment confirmed"),
                    ("payment_review", "Payment needs review"),
                    ("delivery_created", "Delivery created"),
                    ("delivery_ready", "Delivery ready"),
                    ("delivery_assigned", "Delivery assigned"),
                    ("delivery_status", "Delivery status changed"),
                    ("delivery_switch", "Delivery method switched"),
                    ("delivery_issue", "Delivery issue raised"),
                    ("delivery_provider", "Delivery provider update"),
                    ("inventory_alert", "Inventory stock alert"),
                ],
                max_length=28,
            ),
        ),
    ]
