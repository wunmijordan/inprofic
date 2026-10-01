import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0023_alter_payrollcalculationrule_business_and_more"),
        ("core", "0007_query_pattern_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("bulk", "Bulk payroll"),
                            ("single", "Single payslip"),
                        ],
                        default="bulk",
                        max_length=12,
                    ),
                ),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("pay_date", models.DateField()),
                ("staff_count", models.PositiveIntegerField(default=0)),
                (
                    "total_gross_pay",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=16,
                    ),
                ),
                (
                    "total_net_pay",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=16,
                    ),
                ),
                (
                    "total_employer_cost",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=16,
                    ),
                ),
                (
                    "notes",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=500,
                    ),
                ),
                ("posted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "business",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(app_label)s_%(class)s_set",
                        to="core.business",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Person who created this record.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-pay_date", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="payrollrun",
            index=models.Index(
                fields=["business", "-pay_date"],
                name="payrun_biz_paydate_idx",
            ),
        ),
        migrations.CreateModel(
            name="PayrollRunFunding",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=16,
                    ),
                ),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payroll_funding_lines",
                        to="core.cashaccount",
                    ),
                ),
                (
                    "finance_transaction",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payroll_funding_line",
                        to="core.financialtransaction",
                    ),
                ),
                (
                    "payroll_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="funding_lines",
                        to="accounts.payrollrun",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
        migrations.AddConstraint(
            model_name="payrollrunfunding",
            constraint=models.UniqueConstraint(
                fields=("payroll_run", "account"),
                name="unique_payrun_account",
            ),
        ),
        migrations.AddConstraint(
            model_name="payrollrunfunding",
            constraint=models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="payrun_funding_positive",
            ),
        ),
        migrations.AddField(
            model_name="payslip",
            name="payroll_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payslips",
                to="accounts.payrollrun",
            ),
        ),
        migrations.AddField(
            model_name="paysliprevision",
            name="finance_delta",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=16,
            ),
        ),
        migrations.AddField(
            model_name="paysliprevision",
            name="finance_transaction",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payroll_payslip_revisions",
                to="core.financialtransaction",
            ),
        ),
    ]
