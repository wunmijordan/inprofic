from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

def seed_business_order_numbers(apps, schema_editor):
    Order = apps.get_model('production', 'Order')
    Sequence = apps.get_model('production', 'OrderNumberSequence')
    by_business_max = {}
    for order in Order.objects.all().order_by('business_id', 'id'):
        order.order_number = order.id
        order.save(update_fields=['order_number'])
        by_business_max[order.business_id] = max(by_business_max.get(order.business_id, 0), order.id)
    for business_id, max_number in by_business_max.items():
        Sequence.objects.update_or_create(business_id=business_id, defaults={'next_number': max_number + 1})
def backfill_single_offcut_allocations(apps, schema_editor):
    ProductionBatch = apps.get_model('production', 'ProductionBatch')
    Allocation = apps.get_model('production', 'ProductionOffcutAllocation')
    for batch in ProductionBatch.objects.filter(planned_surplus_customer_units__gt=0, planned_surplus_customer__isnull=False).iterator():
        Allocation.objects.create(business_id=batch.business_id, created_by_id=batch.created_by_id, batch_id=batch.pk, customer_id=batch.planned_surplus_customer_id, channel=batch.planned_surplus_customer_channel or 'distribution', quantity=batch.planned_surplus_customer_units, sale_id=batch.planned_surplus_sale_id)

class Migration(migrations.Migration):
    replaces = [
        ('production', '0011_order_reversal_offcut_customer'),
        ('production', '0012_shared_production_runs'),
        ('production', '0013_alter_productionrun_business_and_more'),
        ('production', '0014_business_order_numbering'),
        ('production', '0015_alter_ordernumbersequence_business_and_more'),
        ('production', '0016_productionoffcutallocation'),
        ('production', '0017_alter_productionoffcutallocation_business_and_more'),
        ('production', '0018_order_is_market_stock'),
        ('production', '0019_productionbatch_excess_market_stock_units'),
    ]
    dependencies = [("production", "0002_flow"), ("sales", "0002_current"), ("inventory", "0001_baseline"), ("core", "0001_baseline"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AlterField(model_name='order', name='status', field=models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('completed', 'Completed'), ('rejected', 'Rejected'), ('reversed', 'Reversed')], default='pending', max_length=10)),
        migrations.AddField(model_name='order', name='reversed_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='order', name='reversed_reason', field=models.CharField(blank=True, default='', max_length=255)),
        migrations.AddField(model_name='order', name='reversed_by', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reversed_production_orders', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='productionbatch', name='is_reversed', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='productionbatch', name='planned_surplus_customer_channel', field=models.CharField(blank=True, default='', max_length=20)),
        migrations.AddField(model_name='productionbatch', name='planned_surplus_customer_units', field=models.DecimalField(decimal_places=2, default=0, max_digits=14)),
        migrations.AddField(model_name='productionbatch', name='planned_surplus_customer', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='planned_offcut_batches', to='sales.customer')),
        migrations.AddField(model_name='productionbatch', name='planned_surplus_sale', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='planned_offcut_batches', to='sales.sale')),
        migrations.CreateModel(name='ProductionRun', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('date', models.DateField()), ('run_number', models.CharField(max_length=60)), ('status', models.CharField(choices=[('draft', 'Draft'), ('approved', 'Approved / in production'), ('completed', 'Completed')], default='draft', max_length=12)), ('notes', models.TextField(blank=True, default='')), ('approved_date', models.DateField(blank=True, null=True)), ('completed_date', models.DateField(blank=True, null=True)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='production_productionrun_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='production_productionrun_created', to=settings.AUTH_USER_MODEL))], options={'ordering': ['-date', '-id']}),
        migrations.CreateModel(name='ProductionRunOrder', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='production_run_links', to='production.order')), ('production_run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='order_links', to='production.productionrun'))], options={'ordering': ['order_id']}),
        migrations.CreateModel(name='ProductionRunMaterial', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('planned_quantity', models.DecimalField(decimal_places=4, default=0, max_digits=14)), ('actual_quantity', models.DecimalField(decimal_places=4, default=0, max_digits=14)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='production_productionrunmaterial_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='production_productionrunmaterial_created', to=settings.AUTH_USER_MODEL)), ('production_run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shared_materials', to='production.productionrun')), ('raw_material', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='shared_production_runs', to='inventory.rawmaterial'))], options={'ordering': ['raw_material__name']}),
        migrations.AddField(model_name='productionrun', name='orders', field=models.ManyToManyField(related_name='production_runs', through='production.ProductionRunOrder', to='production.order')),
        migrations.AddField(model_name='productionbatch', name='production_run', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='production_batches', to='production.productionrun')),
        migrations.AddConstraint(model_name='productionrun', constraint=models.UniqueConstraint(fields=('business', 'run_number'), name='unique_production_run_number_per_business')),
        migrations.AddConstraint(model_name='productionrunorder', constraint=models.UniqueConstraint(fields=('order',), name='order_in_at_most_one_production_run')),
        migrations.AddConstraint(model_name='productionrunmaterial', constraint=models.UniqueConstraint(fields=('production_run', 'raw_material'), name='unique_shared_material_per_production_run')),
        migrations.AlterField(model_name='productionrun', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='productionrun', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name='productionrunmaterial', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='productionrunmaterial', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.CreateModel(name='OrderNumberSequence', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('next_number', models.PositiveBigIntegerField(default=1)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='production_ordernumbersequence_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='production_ordernumbersequence_created', to=settings.AUTH_USER_MODEL))]),
        migrations.AddField(model_name='order', name='order_number', field=models.PositiveBigIntegerField(editable=False, null=True)),
        migrations.RunPython(seed_business_order_numbers, migrations.RunPython.noop),
        migrations.AlterField(model_name='order', name='order_number', field=models.PositiveBigIntegerField(editable=False)),
        migrations.AddConstraint(model_name='ordernumbersequence', constraint=models.UniqueConstraint(fields=('business',), name='unique_order_number_sequence_per_business')),
        migrations.AddConstraint(model_name='order', constraint=models.UniqueConstraint(fields=('business', 'order_number'), name='unique_order_number_per_business')),
        migrations.AlterField(model_name='ordernumbersequence', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='ordernumbersequence', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.CreateModel(name='ProductionOffcutAllocation', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('channel', models.CharField(choices=[('distribution', 'Distribution'), ('online', 'Online')], max_length=20)), ('quantity', models.DecimalField(decimal_places=2, max_digits=14)), ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='offcut_allocations', to='production.productionbatch')), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='production_productionoffcutallocation_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='production_productionoffcutallocation_created', to=settings.AUTH_USER_MODEL)), ('customer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='planned_offcut_allocations', to='sales.customer')), ('sale', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='planned_offcut_allocations', to='sales.sale'))], options={'ordering': ['id']}),
        migrations.RunPython(backfill_single_offcut_allocations, migrations.RunPython.noop),
        migrations.AlterField(model_name='productionoffcutallocation', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='productionoffcutallocation', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='order', name='is_market_stock', field=models.BooleanField(default=False, help_text='Distribution orders only: produce without assigning a customer and retain the completed goods as available stock for future sales.')),
        migrations.AddField(model_name='productionbatch', name='excess_market_stock_units', field=models.DecimalField(decimal_places=2, default=0, help_text='Additional saleable output from an unassigned Distribution order retained in Market Stock.', max_digits=14)),
    ]
