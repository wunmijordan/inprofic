from django.db import migrations, models
import django.db.models.deletion
import commerce.models


class Migration(migrations.Migration):
    dependencies = [("commerce", "0007_deliveryarea_radius_km")]

    operations = [
        migrations.AddField(
            model_name="commercepaymentconfiguration",
            name="transfer_enabled",
            field=models.BooleanField(default=False, help_text="Offer a direct bank transfer that does not use a payment gateway."),
        ),
        migrations.AddField(
            model_name="commercepaymentconfiguration",
            name="transfer_account",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="commerce_direct_transfer_configurations", to="core.cashaccount"),
        ),
        migrations.AddField(
            model_name="commercepaymentclaim",
            name="payment_proof",
            field=models.FileField(blank=True, help_text="Customer-supplied transfer receipt/evidence. Required for new direct Transfer claims.", upload_to=commerce.models.commerce_payment_proof_upload_to),
        ),
        migrations.AlterField(
            model_name="commercepayment",
            name="method",
            field=models.CharField(choices=[("paystack", "Paystack secure checkout"), ("monnify", "Monnify secure checkout"), ("bank_transfer", "Instant bank transfer"), ("transfer", "Transfer"), ("cash", "Cash"), ("pos_card", "Card on POS terminal")], max_length=20),
        ),
    ]
