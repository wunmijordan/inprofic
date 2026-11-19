from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0018_daily_brief_report_sections")]
    operations = [
        migrations.AddField(
            model_name="business",
            name="headquarters",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="branch_workspaces",
                to="core.business",
            ),
        ),
        migrations.AddField(
            model_name="business",
            name="label_overrides",
            field=models.JSONField(default=dict, blank=True),
        ),
    ]
