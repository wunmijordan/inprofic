from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.contrib.auth.models
import django.contrib.auth.validators
import django.utils.timezone

def seed_customers(apps, schema_editor):
    Customer = apps.get_model('sales', 'Customer')
    Sale = apps.get_model('sales', 'Sale')
    for sale in Sale.objects.exclude(customer='').exclude(customer='Walk-in'):
        name = (sale.customer or '').strip()
        if not name:
            continue
        customer, _ = Customer.objects.get_or_create(business_id=sale.business_id, name=name, defaults={'created_by_id': sale.created_by_id, 'active': True})
        if sale.customer_master_id is None:
            sale.customer_master_id = customer.pk
            sale.save(update_fields=['customer_master'])

class Migration(migrations.Migration):
    initial = True
    replaces = [
        ('sales', '0001_initial'),
        ('sales', '0002_customer_master'),
    ]
    dependencies = [("core", "0001_baseline"), ("inventory", "0001_baseline"), ("production", "0001_foundation"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='Sale', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('date', models.DateField()), ('customer', models.CharField(blank=True, default='Walk-in', max_length=120)), ('payment_method', models.CharField(choices=[('Cash', 'Cash'), ('Card', 'Card'), ('Transfer', 'Transfer')], default='Cash', max_length=10)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business'))], options={'ordering': ['-date', '-id']}),
        migrations.CreateModel(name='SaleItem', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('qty', models.DecimalField(decimal_places=2, max_digits=12)), ('price', models.DecimalField(decimal_places=2, max_digits=12)), ('finished_good', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='inventory.finishedgood')), ('sale', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='sales.sale'))], options={'abstract': False}),
        migrations.AddField(model_name='sale', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='sale', name='order_type', field=models.CharField(choices=[('walkin', 'Walk-in (from stock)'), ('customer_order', 'Customer order (needs production)')], default='walkin', max_length=15)),
        migrations.AddField(model_name='sale', name='status', field=models.CharField(choices=[('pending', 'Pending'), ('fulfilled', 'Fulfilled')], default='fulfilled', help_text='Walk-in sales are fulfilled immediately. Customer orders start pending and flip to fulfilled automatically when their linked production completes.', max_length=10)),
        migrations.RemoveField(model_name='sale', name='order_type'),
        migrations.RemoveField(model_name='sale', name='status'),
        migrations.RemoveField(model_name='saleitem', name='qty'),
        migrations.AddField(model_name='sale', name='linked_order', field=models.ForeignKey(blank=True, help_text='Set automatically if this sale was created from a completed customer order.', null=True, on_delete=django.db.models.deletion.SET_NULL, to='production.order')),
        migrations.AddField(model_name='sale', name='source', field=models.CharField(choices=[('walkin', 'Physical store'), ('customer_order', 'Customer order')], default='walkin', max_length=15)),
        migrations.AddField(model_name='saleitem', name='batch_qty', field=models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AddField(model_name='saleitem', name='discount', field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=12)),
        migrations.AddField(model_name='saleitem', name='piece_qty', field=models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AlterField(model_name='saleitem', name='price', field=models.DecimalField(decimal_places=2, default=0, help_text="Snapshot of the product's selling price at sale time — set automatically.", max_digits=12)),
        migrations.AlterField(model_name='sale', name='source', field=models.CharField(choices=[('walkin', 'Physical Store'), ('customer_order', 'Customer Order')], default='walkin', max_length=15)),
        migrations.AlterField(model_name='sale', name='source', field=models.CharField(choices=[('walkin', 'Physical Store'), ('distribution_order', 'Distribution Order'), ('online_order', 'Online Order')], default='walkin', max_length=20)),
        migrations.AlterField(model_name='sale', name='linked_order', field=models.ForeignKey(blank=True, help_text='Set automatically if this sale was created from a completed distribution or online order.', null=True, on_delete=django.db.models.deletion.SET_NULL, to='production.order')),
        migrations.AddField(model_name='saleitem', name='unit_cost', field=models.DecimalField(blank=True, decimal_places=6, help_text='Product cost per unit captured at the time of sale.', max_digits=16, null=True)),
        migrations.AddField(model_name='sale', name='transaction_type', field=models.CharField(choices=[('paid', 'Paid'), ('unpaid', 'Unpaid')], default='paid', max_length=10)),
        migrations.AddField(model_name='sale', name='unpaid_description', field=models.CharField(blank=True, default='', help_text='Required when unpaid.', max_length=255)),
        migrations.CreateModel(name='CustomerPayment', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('date', models.DateField()), ('customer', models.CharField(max_length=120)), ('amount', models.DecimalField(decimal_places=2, max_digits=16)), ('payment_method', models.CharField(choices=[('Cash', 'Cash'), ('Card', 'Card'), ('Transfer', 'Transfer')], default='Cash', max_length=10)), ('reference', models.CharField(blank=True, default='', max_length=80)), ('notes', models.CharField(blank=True, default='', max_length=255)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales_customerpayment_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_customerpayment_created', to=settings.AUTH_USER_MODEL)), ('sale', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='sales.sale'))], options={'ordering': ['-date', '-id']}),
        migrations.AlterField(model_name='customerpayment', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='customerpayment', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='sale', name='account', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='sales', to='core.cashaccount')),
        migrations.AddField(model_name='customerpayment', name='account', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='customer_payments', to='core.cashaccount')),
        migrations.AlterField(model_name='sale', name='transaction_type', field=models.CharField(choices=[('paid', 'Paid'), ('partial', 'Partially Paid'), ('unpaid', 'Unpaid')], default='paid', help_text='Payment state. Physical-store unpaid sales are non-cash issues; customer-order sales are receivables until Finance records payment.', max_length=10)),
        migrations.CreateModel(name='Customer', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('name', models.CharField(max_length=160)), ('phone', models.CharField(blank=True, default='', max_length=40)), ('email', models.EmailField(blank=True, default='', max_length=254)), ('address', models.TextField(blank=True, default='')), ('region', models.CharField(blank=True, default='', max_length=100)), ('customer_group', models.CharField(blank=True, default='', max_length=100)), ('credit_limit', models.DecimalField(decimal_places=2, default=0, max_digits=16)), ('payment_terms_days', models.PositiveIntegerField(default=0, help_text='Expected payment period in days for credit sales.')), ('active', models.BooleanField(default=True)), ('notes', models.TextField(blank=True, default='')), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales_customer_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_customer_created', to=settings.AUTH_USER_MODEL))], options={'ordering': ['name']}),
        migrations.AddConstraint(model_name='customer', constraint=models.UniqueConstraint(fields=('business', 'name'), name='unique_customer_per_business')),
        migrations.AddField(model_name='sale', name='customer_master', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_records', to='sales.customer')),
        migrations.AddField(model_name='customerpayment', name='customer_master', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payments', to='sales.customer')),
        migrations.RunPython(seed_customers, migrations.RunPython.noop),
    ]
