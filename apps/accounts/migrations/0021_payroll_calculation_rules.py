import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_existing_payslip_totals(apps, schema_editor):
    Payslip = apps.get_model("accounts", "Payslip")
    for payslip in Payslip.objects.all().iterator():
        payslip.manual_allowances = payslip.allowances
        payslip.manual_deductions = payslip.deductions
        payslip.gross_pay = (payslip.base_pay or 0) + (payslip.allowances or 0)
        payslip.employer_contributions = 0
        payslip.employer_cost = payslip.gross_pay
        payslip.save(update_fields=[
            "manual_allowances", "manual_deductions", "gross_pay",
            "employer_contributions", "employer_cost",
        ])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0020_payroll_addon_and_marketing_visits"),
        ("core", "0007_query_pattern_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollCalculationRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120)),
                ("category", models.CharField(choices=[("tax", "Tax"), ("pension", "Pension"), ("insurance", "Insurance"), ("levy", "Levy"), ("allowance", "Allowance / earning"), ("deduction", "Other deduction"), ("employer_contribution", "Employer contribution"), ("other", "Other")], default="other", max_length=24)),
                ("effect", models.CharField(choices=[("earning", "Add to staff gross pay"), ("employee_deduction", "Deduct from staff pay"), ("employer_contribution", "Employer contribution only")], default="employee_deduction", max_length=24)),
                ("method", models.CharField(choices=[("fixed", "Fixed amount"), ("percentage", "Percentage")], default="percentage", max_length=12)),
                ("basis", models.CharField(choices=[("base_pay", "Base pay"), ("gross_pay", "Gross pay")], default="gross_pay", max_length=12)),
                ("rate", models.DecimalField(decimal_places=4, default=0, help_text="Percentage rate, e.g. 8 for 8%.", max_digits=7)),
                ("fixed_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("threshold_amount", models.DecimalField(decimal_places=2, default=0, help_text="Optional exempt threshold. Percentage rules apply only to the basis amount above this value.", max_digits=14)),
                ("cap_amount", models.DecimalField(decimal_places=2, default=0, help_text="Optional maximum calculated amount. Leave at 0 for no cap.", max_digits=14)),
                ("statutory", models.BooleanField(default=False, help_text="Mark tax, pension or another rule that is statutory for this business.")),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=50)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="accounts_payrollcalculationrule_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="accounts_payrollcalculationrule_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["sort_order", "name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="payrollcalculationrule",
            constraint=models.UniqueConstraint(fields=("business", "name"), name="unique_payroll_rule_name_per_business"),
        ),
        migrations.AddConstraint(
            model_name="payrollcalculationrule",
            constraint=models.CheckConstraint(condition=models.Q(rate__gte=0, rate__lte=100), name="payroll_rule_rate_0_100"),
        ),
        migrations.AddConstraint(
            model_name="payrollcalculationrule",
            constraint=models.CheckConstraint(condition=models.Q(fixed_amount__gte=0), name="payroll_rule_fixed_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="payrollcalculationrule",
            constraint=models.CheckConstraint(condition=models.Q(threshold_amount__gte=0), name="payroll_rule_threshold_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="payrollcalculationrule",
            constraint=models.CheckConstraint(condition=models.Q(cap_amount__gte=0), name="payroll_rule_cap_nonnegative"),
        ),
        migrations.AddField(
            model_name="payslip",
            name="manual_allowances",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="payslip",
            name="manual_deductions",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="payslip",
            name="gross_pay",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="payslip",
            name="employer_contributions",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="payslip",
            name="employer_cost",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.RunPython(seed_existing_payslip_totals, migrations.RunPython.noop),
        migrations.CreateModel(
            name="PayslipCalculationLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("category", models.CharField(choices=[("tax", "Tax"), ("pension", "Pension"), ("insurance", "Insurance"), ("levy", "Levy"), ("allowance", "Allowance / earning"), ("deduction", "Other deduction"), ("employer_contribution", "Employer contribution"), ("other", "Other")], max_length=24)),
                ("effect", models.CharField(choices=[("earning", "Add to staff gross pay"), ("employee_deduction", "Deduct from staff pay"), ("employer_contribution", "Employer contribution only")], max_length=24)),
                ("method", models.CharField(choices=[("fixed", "Fixed amount"), ("percentage", "Percentage")], max_length=12)),
                ("basis", models.CharField(choices=[("base_pay", "Base pay"), ("gross_pay", "Gross pay")], max_length=12)),
                ("rate", models.DecimalField(decimal_places=4, default=0, max_digits=7)),
                ("threshold_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("cap_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("calculation_base", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("statutory", models.BooleanField(default=False)),
                ("sort_order", models.PositiveSmallIntegerField(default=50)),
                ("payslip", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="calculation_lines", to="accounts.payslip")),
                ("rule", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payslip_lines", to="accounts.payrollcalculationrule")),
            ],
            options={"ordering": ["sort_order", "id"]},
        ),
    ]
