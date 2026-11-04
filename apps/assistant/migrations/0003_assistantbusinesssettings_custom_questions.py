from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("assistant", "0002_platform_business_settings_and_quick_facts")]
    operations = [migrations.AddField(
        model_name="assistantbusinesssettings", name="custom_questions",
        field=models.JSONField(default=list, blank=True, help_text="Questions mapped to built-in record-backed reports."),
    )]
