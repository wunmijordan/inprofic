from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.contrib.auth.models
import django.contrib.auth.validators
import django.utils.timezone

class Migration(migrations.Migration):
    initial = True
    replaces = [
        ('expenses', '0001_initial'),
        ('expenses', '0002_alter_expensepayment_business_and_more'),
    ]
    dependencies = [("core", "0001_baseline"), ("inventory", "0001_baseline"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='Expense', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('date', models.DateField()), ('category', models.CharField(choices=[('utilities', 'Utilities'), ('rent', 'Rent / Premises'), ('maintenance', 'Maintenance / Repairs'), ('transport', 'Transport / Delivery'), ('labour', 'Labour / Wages'), ('marketing', 'Marketing'), ('bank', 'Bank / Payment Fees'), ('other', 'Other')], default='other', max_length=20)), ('description', models.CharField(max_length=180)), ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=14)), ('vendor', models.CharField(blank=True, max_length=120)), ('notes', models.TextField(blank=True)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expenses_created', to=settings.AUTH_USER_MODEL)), ('raw_material', models.ForeignKey(blank=True, help_text='Optional: link the expense to a specific inventory item when it is not a purchase order.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='misc_expenses', to='inventory.rawmaterial'))], options={'ordering': ['-date', '-id']}),
        migrations.RemoveField(model_name='expense', name='raw_material'),
        migrations.AddField(model_name='expense', name='payment_status', field=models.CharField(choices=[('paid', 'Paid'), ('unpaid', 'Unpaid')], default='paid', max_length=10)),
        migrations.AddField(model_name='expense', name='payment_method', field=models.CharField(choices=[('Cash', 'Cash'), ('Card', 'Card'), ('Transfer', 'Transfer')], default='Transfer', max_length=10)),
        migrations.AddField(model_name='expense', name='account', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='expenses', to='core.cashaccount')),
        migrations.CreateModel(name='ExpensePayment', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('date', models.DateField()), ('amount', models.DecimalField(decimal_places=2, max_digits=16)), ('payment_method', models.CharField(choices=[('Cash', 'Cash'), ('Card', 'Card'), ('Transfer', 'Transfer')], default='Transfer', max_length=10)), ('reference', models.CharField(blank=True, default='', max_length=80)), ('notes', models.CharField(blank=True, default='', max_length=255)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='expenses_expensepayment_set', to='core.business')), ('created_by', models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expenses_expensepayment_created', to=settings.AUTH_USER_MODEL)), ('expense', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='expenses.expense')), ('account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='expense_payments', to='core.cashaccount'))], options={'ordering': ['-date', '-id']}),
        migrations.AlterField(model_name='expensepayment', name='business', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
        migrations.AlterField(model_name='expensepayment', name='created_by', field=models.ForeignKey(blank=True, help_text='Person who created this record.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created', to=settings.AUTH_USER_MODEL)),
    ]
