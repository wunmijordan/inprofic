from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid




def backfill_fulfilment_sources(apps, schema_editor):
    CommerceCheckoutItem = apps.get_model('commerce', 'CommerceCheckoutItem')
    CommerceIntakeItem = apps.get_model('commerce', 'CommerceIntakeItem')
    CommerceCheckoutItem.objects.filter(production_quantity__lte=0).update(fulfilment_source='stock')
    CommerceIntakeItem.objects.filter(production_quantity__lte=0).update(fulfilment_source='stock')


def collapse_out_for_delivery(apps, schema_editor):
    DeliveryAssignment = apps.get_model('commerce', 'DeliveryAssignment')
    DeliveryEvent = apps.get_model('commerce', 'DeliveryEvent')
    DeliveryAssignment.objects.filter(status='out_for_delivery').update(status='picked_up')
    DeliveryEvent.objects.filter(status='out_for_delivery').update(status='picked_up')


class Migration(migrations.Migration):
    dependencies = [
        ('commerce', '0016_storefront_attribution'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='storefrontproduct',
            name='estimated_ready_minutes',
            field=models.PositiveSmallIntegerField(default=30, help_text='Typical made-to-order readiness estimate shown to customers and used to validate delivery timing.'),
        ),
        migrations.AddField(
            model_name='commercecheckoutsession',
            name='estimated_ready_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commercecheckoutsession',
            name='requested_delivery_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commerceintake',
            name='estimated_ready_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commerceintake',
            name='requested_delivery_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commercecheckoutitem',
            name='estimated_ready_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commercecheckoutitem',
            name='fulfilment_source',
            field=models.CharField(choices=[('stock', 'From Physical Store stock'), ('made_to_order', 'Made to order'), ('procured', 'Procured to sell')], default='made_to_order', max_length=16),
        ),
        migrations.AddField(
            model_name='commerceintakeitem',
            name='estimated_ready_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commerceintakeitem',
            name='fulfilment_source',
            field=models.CharField(choices=[('stock', 'From Physical Store stock'), ('made_to_order', 'Made to order'), ('procured', 'Procured to sell')], default='made_to_order', max_length=16),
        ),
        migrations.RunPython(backfill_fulfilment_sources, migrations.RunPython.noop),
        migrations.CreateModel(
            name='DeliveryBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('public_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('status', models.CharField(choices=[('draft', 'Organising route'), ('routed', 'Route ready'), ('picked_up', 'Picked up · en route'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('routed_at', models.DateTimeField(blank=True, null=True)),
                ('picked_up_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
                ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
                ('driver', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='batches', to='commerce.deliverydriver')),
            ],
            options={'ordering': ['-created_at', '-id']},
        ),
        migrations.AddIndex(
            model_name='deliverybatch',
            index=models.Index(fields=['business', 'status', 'created_at'], name='delivery_batch_status_idx'),
        ),
        migrations.AddField(
            model_name='deliveryassignment',
            name='batch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assignments', to='commerce.deliverybatch'),
        ),
        migrations.AddField(
            model_name='deliveryassignment',
            name='batch_stop_minutes',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='deliveryassignment',
            name='batch_stop_sequence',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='DeliveryMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sender_type', models.CharField(choices=[('customer', 'Customer'), ('rider', 'Rider'), ('staff', 'Dispatch staff')], max_length=12)),
                ('body', models.CharField(max_length=500)),
                ('assignment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='commerce.deliveryassignment')),
                ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
                ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
                ('sender_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='commerce_delivery_messages', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['created_at', 'id']},
        ),
        migrations.RunPython(collapse_out_for_delivery, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='deliveryassignment',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending dispatch'), ('assigned', 'Driver assigned'), ('ready', 'Ready for pickup'), ('picked_up', 'Picked up · en route'), ('delivered', 'Delivered'), ('failed', 'Delivery failed'), ('returned', 'Returned'), ('cancelled', 'Cancelled')], default='pending', max_length=20),
        ),
        migrations.AlterField(
            model_name='deliveryevent',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending dispatch'), ('assigned', 'Driver assigned'), ('ready', 'Ready for pickup'), ('picked_up', 'Picked up · en route'), ('delivered', 'Delivered'), ('failed', 'Delivery failed'), ('returned', 'Returned'), ('cancelled', 'Cancelled')], max_length=20),
        ),
    ]
