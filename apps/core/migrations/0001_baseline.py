from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion
import django.core.validators
from django.core.validators import FileExtensionValidator
import core.models

PREVIOUS_BACKGROUND = '#4D1C25'
PREVIOUS_ACCENT = '#8F172D'
NEW_BACKGROUND = '#050733'
NEW_ACCENT = '#D14900'
def update_brand_palette(apps, schema_editor):
    Business = apps.get_model('core', 'Business')
    Business.objects.filter(background_color__iexact=PREVIOUS_BACKGROUND).update(background_color=NEW_BACKGROUND)
    Business.objects.filter(accent_color__iexact=PREVIOUS_ACCENT).update(accent_color=NEW_ACCENT)

class Migration(migrations.Migration):
    initial = True
    replaces = [
        ('core', '0001_initial'),
        ('core', '0002_finance_audit'),
        ('core', '0003_business_accent_color_and_more'),
        ('core', '0004_business_background_color'),
        ('core', '0005_add_wholesale_retail_verticals'),
        ('core', '0006_update_default_brand_palette'),
        ('core', '0007_business_storefront_logo'),
    ]
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='Business', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('name', models.CharField(default='My Business', max_length=120)), ('currency_symbol', models.CharField(default='₦', max_length=5)), ('slug', models.SlugField(default='main', max_length=60, unique=True))], options={'verbose_name_plural': 'businesses'}),
        migrations.CreateModel(name='CashAccount', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('name', models.CharField(max_length=80)), ('account_type', models.CharField(choices=[('cash', 'Cash'), ('bank', 'Bank / Transfer'), ('card', 'Card / POS'), ('other', 'Other')], default='cash', max_length=10)), ('opening_balance', models.DecimalField(decimal_places=2, default=0, max_digits=16)), ('active', models.BooleanField(default=True)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='core_cashaccount_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='core_cashaccount_created', to=settings.AUTH_USER_MODEL))], options={'ordering': ['name'], 'constraints': [models.UniqueConstraint(fields=('business', 'name'), name='unique_cash_account_per_business')]}),
        migrations.CreateModel(name='FinancialTransaction', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('date', models.DateField()), ('transaction_type', models.CharField(choices=[('income', 'Money In'), ('outflow', 'Money Out')], max_length=10)), ('amount', models.DecimalField(decimal_places=2, max_digits=16)), ('category', models.CharField(max_length=80)), ('description', models.CharField(max_length=255)), ('payment_method', models.CharField(blank=True, default='', max_length=20)), ('reference', models.CharField(blank=True, default='', max_length=80)), ('reversed', models.BooleanField(default=False)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='core_financialtransaction_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='core_financialtransaction_created', to=settings.AUTH_USER_MODEL)), ('account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='transactions', to='core.cashaccount')), ('reversal_of', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reversal_entries', to='core.financialtransaction'))], options={'ordering': ['-date', '-id']}),
        migrations.CreateModel(name='AuditLog', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('action', models.CharField(max_length=30)), ('model_name', models.CharField(max_length=80)), ('object_id', models.CharField(blank=True, default='', max_length=80)), ('description', models.CharField(max_length=255)), ('metadata', models.JSONField(blank=True, default=dict)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='core_auditlog_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='core_auditlog_created', to=settings.AUTH_USER_MODEL))], options={'ordering': ['-created_at', '-id']}),
        migrations.AlterField(model_name='auditlog', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='auditlog', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name='cashaccount', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='cashaccount', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name='financialtransaction', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='financialtransaction', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='business', name='accent_color', field=models.CharField(default='#8F172D', max_length=7, validators=[django.core.validators.RegexValidator('^#[0-9A-Fa-f]{6}$', 'Use a six-digit hex colour such as #8F172D.')])),
        migrations.AddField(model_name='business', name='restaurant_table_service', field=models.BooleanField(default=True, help_text='For restaurant businesses, capture a table/reference for dine-in sales.')),
        migrations.AddField(model_name='business', name='tagline', field=models.CharField(blank=True, default='', max_length=100)),
        migrations.AddField(model_name='business', name='vertical', field=models.CharField(choices=[('bakery', 'Bakery'), ('restaurant', 'Restaurant / food service'), ('general', 'General production')], default='bakery', max_length=20)),
        migrations.AlterField(model_name='business', name='accent_color', field=models.CharField(default='#8F172D', help_text='Used for primary buttons, links, headings, and action highlights.', max_length=7, validators=[django.core.validators.RegexValidator('^#[0-9A-Fa-f]{6}$', 'Use a six-digit hex colour such as #8F172D.')])),
        migrations.AddField(model_name='business', name='background_color', field=models.CharField(default='#4D1C25', help_text='Used for persistent branded backgrounds such as the navigation area.', max_length=7, validators=[django.core.validators.RegexValidator('^#[0-9A-Fa-f]{6}$', 'Use a six-digit hex colour such as #4D1C25.')])),
        migrations.AlterField(model_name='business', name='vertical', field=models.CharField(choices=[('bakery', 'Bakery'), ('restaurant', 'Restaurant / food service'), ('general', 'General production'), ('wholesale', 'Wholesale / distribution'), ('retail', 'Retail store')], default='bakery', max_length=20)),
        migrations.AlterField(model_name='business', name='accent_color', field=models.CharField(default=NEW_ACCENT, help_text='Used for primary buttons, links, headings, and action highlights.', max_length=7, validators=[django.core.validators.RegexValidator('^#[0-9A-Fa-f]{6}$', 'Use a six-digit hex colour such as #D14900.')])),
        migrations.AlterField(model_name='business', name='background_color', field=models.CharField(default=NEW_BACKGROUND, help_text='Used for persistent branded backgrounds such as the navigation area.', max_length=7, validators=[django.core.validators.RegexValidator('^#[0-9A-Fa-f]{6}$', 'Use a six-digit hex colour such as #050733.')])),
        migrations.RunPython(update_brand_palette, migrations.RunPython.noop),
        migrations.AddField(model_name='business', name='storefront_logo', field=models.ImageField(blank=True, help_text='Optional logo shown beside the business name on the public storefront only.', upload_to=core.models.business_storefront_logo_upload_to, validators=[FileExtensionValidator(['jpeg', 'jpg', 'png', 'webp'])])),
    ]
