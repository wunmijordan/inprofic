import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_recurring_adjustments_and_fixed_amounts(apps, schema_editor):
    PayrollStaffProfile = apps.get_model("accounts", "PayrollStaffProfile")
    PayrollRecurringAdjustment = apps.get_model("accounts", "PayrollRecurringAdjustment")
    PayslipCalculationLine = apps.get_model("accounts", "PayslipCalculationLine")

    for staff in PayrollStaffProfile.objects.all().iterator():
        if staff.recurring_allowances and staff.recurring_allowances > 0:
            PayrollRecurringAdjustment.objects.create(
                staff=staff,
                kind="allowance",
                name="Recurring allowance",
                amount=staff.recurring_allowances,
                active=True,
            )
        if staff.recurring_deductions and staff.recurring_deductions > 0:
            PayrollRecurringAdjustment.objects.create(
                staff=staff,
                kind="deduction",
                name="Recurring deduction",
                amount=staff.recurring_deductions,
                active=True,
            )

    for line in PayslipCalculationLine.objects.all().iterator():
        if line.method == "fixed":
            # 0021 froze the calculated line amount but did not yet persist the
            # configured fixed amount itself.  Use the historical issued amount
            # rather than today's live rule (which may already have changed) so
            # migrating never rewrites old payroll from current configuration.
            line.fixed_amount = line.amount or 0
            line.save(update_fields=["fixed_amount"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0021_payroll_calculation_rules"),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollRecurringAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("allowance", "Allowance / earning"), ("deduction", "Deduction")], max_length=12)),
                ("name", models.CharField(max_length=120)),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recurring_adjustments", to="accounts.payrollstaffprofile")),
            ],
            options={"ordering": ["kind", "name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="payrollrecurringadjustment",
            constraint=models.CheckConstraint(condition=models.Q(amount__gte=0), name="payroll_recurring_amount_nonnegative"),
        ),
        migrations.CreateModel(
            name="PayslipAdjustmentLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("allowance", "Allowance / earning"), ("deduction", "Deduction")], max_length=12)),
                ("name", models.CharField(max_length=120)),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("payslip", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="adjustment_lines", to="accounts.payslip")),
                ("recurring_adjustment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payslip_lines", to="accounts.payrollrecurringadjustment")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="payslipadjustmentline",
            constraint=models.CheckConstraint(condition=models.Q(amount__gte=0), name="payslip_adjustment_amount_nonnegative"),
        ),
        migrations.AddField(
            model_name="payslipcalculationline",
            name="fixed_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.CreateModel(
            name="PayslipRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.CharField(max_length=500)),
                ("previous_snapshot", models.JSONField(default=dict)),
                ("edited_at", models.DateTimeField(auto_now_add=True)),
                ("edited_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payroll_payslip_revisions", to=settings.AUTH_USER_MODEL)),
                ("payslip", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="accounts.payslip")),
            ],
            options={"ordering": ["-edited_at", "-id"]},
        ),
        migrations.RunPython(seed_recurring_adjustments_and_fixed_amounts, migrations.RunPython.noop),
    ]
