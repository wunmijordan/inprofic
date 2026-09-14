from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings

class Migration(migrations.Migration):
    replaces = [
        ('sales', '0003_saleitem_production_batch'),
        ('sales', '0004_customerproductprice'),
        ('sales', '0005_alter_customer_business_alter_customer_created_by_and_more'),
        ('sales', '0006_sale_service_mode_sale_table_reference'),
        ('sales', '0007_allow_blank_payment_method_for_receivables'),
    ]
    dependencies = [("sales", "0001_foundation"), ("production", "0002_flow"), ("inventory", "0001_baseline"), ("core", "0001_baseline"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name='saleitem', name='production_batch', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sale_items', to='production.productionbatch')),
        migrations.CreateModel(name='CustomerProductPrice', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('channel', models.CharField(choices=[('distribution', 'Distribution'), ('online', 'Online')], max_length=20)), ('price', models.DecimalField(decimal_places=2, max_digits=12)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales_customerproductprice_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_customerproductprice_created', to=settings.AUTH_USER_MODEL)), ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='product_prices', to='sales.customer')), ('finished_good', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='customer_prices', to='inventory.finishedgood'))], options={'ordering': ['finished_good__name', 'channel'], 'constraints': [models.UniqueConstraint(fields=('business', 'customer', 'finished_good', 'channel'), name='unique_customer_product_price')]}),
        migrations.AlterField(model_name='customer', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='customer', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name='customerproductprice', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='customerproductprice', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name='sale', name='unpaid_description', field=models.CharField(blank=True, default='', help_text='Reason for physical-store unpaid issue, or receivable note for customer orders.', max_length=255)),
        migrations.AddField(model_name='sale', name='service_mode', field=models.CharField(blank=True, choices=[('dine_in', 'Dine-in'), ('takeaway', 'Takeaway / pickup'), ('delivery', 'Delivery')], default='', help_text='Restaurant service context. Leave blank when restaurant service details do not apply.', max_length=12)),
        migrations.AddField(model_name='sale', name='table_reference', field=models.CharField(blank=True, default='', help_text='Optional restaurant table number, tab name, or service reference.', max_length=40)),
        migrations.AlterField(model_name='sale', name='payment_method', field=models.CharField(blank=True, choices=[('Cash', 'Cash'), ('Card', 'Card'), ('Transfer', 'Transfer')], default='Cash', help_text='Payment method actually received. May be blank while a sale remains an unpaid receivable.', max_length=10)),
    ]
