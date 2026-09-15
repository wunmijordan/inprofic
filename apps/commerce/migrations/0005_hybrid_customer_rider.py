import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


DELIVERY_EVENT_CHOICES = [
    ("checkout_received", "Checkout received"),
    ("intake_received", "Order received"),
    ("payment_started", "Payment started"),
    ("payment_claim", "Payment claim submitted"),
    ("payment_confirmed", "Payment confirmed"),
    ("payment_review", "Payment needs review"),
    ("delivery_created", "Delivery created"),
    ("delivery_assigned", "Delivery assigned"),
    ("delivery_status", "Delivery status changed"),
    ("delivery_switch", "Delivery method switched"),
    ("delivery_issue", "Delivery issue raised"),
    ("delivery_provider", "Delivery provider update"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0004_glovo_laas_v2"),
        ("accounts", "0006_delivery_rider_role"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="commercesettings",
            name="notify_delivery_activity",
            field=models.BooleanField(
                default=True,
                help_text="Alert dispatchers and relevant staff about delivery assignments, status changes, provider exceptions, and rider issues.",
            ),
        ),
        migrations.AddField(
            model_name="deliverysettings",
            name="hybrid_routing_policy",
            field=models.CharField(
                choices=[
                    ("dispatcher_choice", "Dispatcher chooses per order"),
                    ("customer_choice", "Customer chooses at checkout"),
                    ("lowest_fee", "Automatically use the lowest fee"),
                    ("fastest_eta", "Automatically use the fastest ETA"),
                    ("inhouse_first", "Prefer in-house; Glovo remains available"),
                    ("glovo_first", "Prefer Glovo; in-house remains available"),
                ],
                default="dispatcher_choice",
                help_text="When Hybrid is enabled, decide who/what chooses between in-house delivery and the configured provider.",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="deliverysettings",
            name="hybrid_switch_policy",
            field=models.CharField(
                choices=[
                    ("locked", "Lock the chosen delivery method after payment"),
                    ("equal_or_lower", "Allow switches only when the new quote is not higher"),
                    ("business_absorbs", "Allow switches; the business absorbs any higher provider cost"),
                    ("approval_absorbs", "Higher-cost switches need manager approval; the business absorbs the difference"),
                ],
                default="business_absorbs",
                help_text="Controls whether dispatch staff may change the paid order's delivery method before pickup.",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="deliverysettings",
            name="customer_switch_policy_note",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional customer-facing clarification shown beside the standard Hybrid switching policy.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="deliverydriver",
            name="user",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional tenant staff login for an in-house rider. Linked riders see only deliveries assigned to this driver profile.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="delivery_driver_profiles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="deliverydriver",
            constraint=models.UniqueConstraint(
                condition=models.Q(user__isnull=False),
                fields=("business", "user"),
                name="unique_delivery_driver_login_per_business",
            ),
        ),
        migrations.AddField(
            model_name="deliveryquote",
            name="quote_group_id",
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False),
        ),
        migrations.AddField(
            model_name="deliveryquote",
            name="selection_source",
            field=models.CharField(
                choices=[
                    ("platform", "Platform routing policy"),
                    ("customer", "Customer choice"),
                    ("dispatcher", "Dispatcher choice"),
                ],
                default="platform",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="deliveryassignment",
            name="method_switch_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="deliveryassignment",
            name="last_method_switched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="DeliveryIssue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.CharField(choices=[("delay", "Delay / traffic"), ("customer_unavailable", "Customer unavailable"), ("address", "Address / location problem"), ("vehicle", "Vehicle / rider problem"), ("package", "Package / order problem"), ("safety", "Safety concern"), ("other", "Other issue / complaint")], default="other", max_length=28)),
                ("details", models.TextField()),
                ("status", models.CharField(choices=[("open", "Open"), ("acknowledged", "Acknowledged"), ("resolved", "Resolved")], default="open", max_length=16)),
                ("resolution_note", models.TextField(blank=True, default="")),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="issues", to="commerce.deliveryassignment")),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("reporter_driver", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reported_issues", to="commerce.deliverydriver")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [models.Index(fields=["business", "status", "created_at"], name="delivery_issue_open_idx")],
            },
        ),
        migrations.CreateModel(
            name="StorefrontCustomer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("email", models.EmailField(max_length=254)),
                ("name", models.CharField(max_length=160)),
                ("phone", models.CharField(blank=True, default="", max_length=40)),
                ("default_address", models.TextField(blank=True, default="")),
                ("password_hash", models.CharField(max_length=255)),
                ("active", models.BooleanField(default=True)),
                ("last_login_at", models.DateTimeField(blank=True, null=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["name", "id"],
                "indexes": [models.Index(fields=["business", "email"], name="storefront_customer_email_idx")],
                "constraints": [models.UniqueConstraint(fields=("business", "email"), name="unique_storefront_customer_email_per_business")],
            },
        ),
        migrations.AddField(
            model_name="commerceintake",
            name="storefront_customer",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="orders", to="commerce.storefrontcustomer"),
        ),
        migrations.AddField(
            model_name="commercecheckoutsession",
            name="storefront_customer",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="checkouts", to="commerce.storefrontcustomer"),
        ),
        migrations.RemoveConstraint(
            model_name="commercenotification",
            name="unique_commerce_notification_dedupe",
        ),
        migrations.AddField(
            model_name="commercenotification",
            name="recipient_user",
            field=models.ForeignKey(
                blank=True,
                help_text="When set, this alert is visible only to that tenant staff user.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="targeted_commerce_notifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="commercenotification",
            name="event_type",
            field=models.CharField(choices=DELIVERY_EVENT_CHOICES, max_length=28),
        ),
        migrations.AddConstraint(
            model_name="commercenotification",
            constraint=models.UniqueConstraint(
                condition=models.Q(recipient_user__isnull=True) & ~models.Q(dedupe_key=""),
                fields=("business", "dedupe_key"),
                name="unique_commerce_notification_dedupe",
            ),
        ),
        migrations.AddConstraint(
            model_name="commercenotification",
            constraint=models.UniqueConstraint(
                condition=models.Q(recipient_user__isnull=False) & ~models.Q(dedupe_key=""),
                fields=("business", "recipient_user", "dedupe_key"),
                name="unique_targeted_commerce_notification_dedupe",
            ),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="use_live_quotes",
            field=models.BooleanField(default=True, help_text="Use the provider's live quote/ETA when configured; hybrid mode can fall back to INPROFIC rate bands."),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="status_mapping",
            field=models.JSONField(blank=True, default=dict, help_text='Map provider statuses to INPROFIC statuses. Example: {"delivered": "delivered"}.'),
        ),
    ]
