from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("accounts", "0011_marketingpromocampaign")]

    operations = [
        migrations.AlterField(
            model_name="subscriptionpromotion",
            name="plan",
            field=models.ForeignKey(
                blank=True,
                help_text="Leave blank when this promotion applies to every active plan.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="promotions",
                to="accounts.subscriptionplan",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionpromotion",
            name="applies_to_all_plans",
            field=models.BooleanField(
                default=False,
                help_text="Apply this single promotion to every active subscription plan using each plan's own base price.",
            ),
        ),
    ]
