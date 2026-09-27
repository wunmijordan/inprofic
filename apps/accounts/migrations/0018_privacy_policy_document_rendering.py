from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0017_platform_privacy_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformprivacypolicy",
            name="render_mode",
            field=models.CharField(
                choices=[
                    ("html", "Editable web policy"),
                    ("pdf", "Uploaded PDF"),
                    ("docx", "Uploaded Word document"),
                ],
                default="html",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="platformprivacypolicy",
            name="rendered_document_html",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Safe rendered snapshot used for an uploaded Word policy document.",
            ),
        ),
        migrations.AlterField(
            model_name="platformprivacypolicy",
            name="source_file",
            field=models.FileField(
                blank=True,
                help_text="Optional HTML, TXT, Markdown, PDF or Word (.docx) source retained with the current policy.",
                upload_to="platform/privacy/",
                validators=[django.core.validators.FileExtensionValidator(["html", "htm", "txt", "md", "markdown", "pdf", "docx"])],
            ),
        ),
    ]
