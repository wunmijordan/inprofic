from django.db import migrations, models


class Migration(migrations.Migration):
    """Drop the plan link from extra-staff batches (see 0026 for the data step).

    Kept separate from 0026 so the data update and the table change are not in
    one transaction (PostgreSQL rejects altering a table with pending trigger
    events from earlier updates in the same transaction).
    """

    dependencies = [
        ("accounts", "0026_payroll_batches_one_per_size"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="payrollstaffbatch",
            name="unique_payroll_batch_plan_staff",
        ),
        migrations.AlterModelOptions(
            name="payrollstaffbatch",
            options={"ordering": ["staff_count", "id"], "verbose_name_plural": "payroll staff batches"},
        ),
        migrations.RemoveField(
            model_name="payrollstaffbatch",
            name="plan",
        ),
        migrations.AddConstraint(
            model_name="payrollstaffbatch",
            constraint=models.UniqueConstraint(
                condition=models.Q(("active", True)),
                fields=("staff_count",),
                name="unique_active_payroll_batch_staff",
                violation_error_message="An active extra-staff batch of this size already exists. Retire it first to change its price.",
            ),
        ),
    ]
