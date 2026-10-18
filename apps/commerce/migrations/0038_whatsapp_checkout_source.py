from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0037_neutral_archived_provider_names"),
    ]

    operations = [
        migrations.AlterField(
            model_name="commerceintake",
            name="source",
            field=models.CharField(
                choices=[
                    ("storefront", "Hosted storefront"),
                    ("api", "API"),
                    ("connector", "External connector"),
                    ("whatsapp", "WhatsApp storefront"),
                    ("staff_pos", "In-premise storefront"),
                ],
                default="storefront",
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="commercecheckoutsession",
            name="source",
            field=models.CharField(
                choices=[
                    ("storefront", "Hosted storefront"),
                    ("api", "API"),
                    ("connector", "External connector"),
                    ("whatsapp", "WhatsApp storefront"),
                    ("staff_pos", "In-premise storefront"),
                ],
                default="api",
                max_length=12,
            ),
        ),
    ]
