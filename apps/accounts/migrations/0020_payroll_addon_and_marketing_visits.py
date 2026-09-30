import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_query_pattern_indexes"),
        ("core", "0007_query_pattern_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollAddonTier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("staff_limit", models.PositiveIntegerField(help_text="Maximum active payroll staff covered by this tier.")),
                ("monthly_price", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payroll_addon_tiers", to="accounts.subscriptionplan")),
            ],
            options={"ordering": ["plan__monthly_price", "staff_limit", "id"]},
        ),
        migrations.AddConstraint(
            model_name="payrolladdontier",
            constraint=models.UniqueConstraint(fields=("plan", "staff_limit"), name="unique_payroll_tier_plan_staff"),
        ),
        migrations.CreateModel(
            name="BusinessPayrollAddon",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("active", models.BooleanField(default=True)),
                ("paid_until", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="payroll_addon", to="core.business")),
                ("tier", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="business_addons", to="accounts.payrolladdontier")),
            ],
            options={"ordering": ["business__name"]},
        ),
        migrations.CreateModel(
            name="PayrollStaffProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("full_name", models.CharField(max_length=160)),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                ("whatsapp_number", models.CharField(blank=True, default="", max_length=30)),
                ("job_title", models.CharField(blank=True, default="", max_length=120)),
                ("pay_frequency", models.CharField(choices=[("monthly", "Monthly"), ("weekly", "Weekly")], default="monthly", max_length=12)),
                ("base_pay", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("recurring_allowances", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("recurring_deductions", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("active", models.BooleanField(default=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="accounts_payrollstaffprofile_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="accounts_payrollstaffprofile_created", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(blank=True, help_text="Optional existing INPROFIC user linked to this payroll profile.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payroll_staff_profiles", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["full_name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="payrollstaffprofile",
            constraint=models.UniqueConstraint(condition=models.Q(("user__isnull", False)), fields=("business", "user"), name="unique_payroll_user_per_business"),
        ),
        migrations.CreateModel(
            name="Payslip",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("base_pay", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("allowances", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("deductions", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("net_pay", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("notes", models.CharField(blank=True, default="", max_length=500)),
                ("share_enabled", models.BooleanField(default=True)),
                ("issued_at", models.DateTimeField(auto_now_add=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="accounts_payslip_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="accounts_payslip_created", to=settings.AUTH_USER_MODEL)),
                ("staff", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payslips", to="accounts.payrollstaffprofile")),
            ],
            options={
                "ordering": ["-period_end", "-issued_at", "-id"],
                "indexes": [models.Index(fields=["business", "-period_end"], name="payslip_biz_period_idx")],
            },
        ),
        migrations.AddField(
            model_name="subscriptionpayment",
            name="purpose",
            field=models.CharField(choices=[("subscription", "Plan subscription"), ("payroll_addon", "Payroll add-on")], default="subscription", max_length=20),
        ),
        migrations.AddField(
            model_name="subscriptionpayment",
            name="payroll_tier",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="subscription_payments", to="accounts.payrolladdontier"),
        ),
        migrations.AlterField(
            model_name="platformevent",
            name="event_type",
            field=models.CharField(choices=[("marketing_visit", "Marketing page visit"), ("signup_view", "Signup viewed"), ("registration_completed", "Registration completed"), ("login", "Login"), ("logout", "Logout"), ("module_view", "Module viewed"), ("subscription_started", "Subscription started"), ("subscription_trial_started", "Paid-plan trial started"), ("subscription_changed", "Subscription changed"), ("subscription_paid", "Subscription paid"), ("subscription_founder_grant", "Founder subscription grant")], db_index=True, max_length=40),
        ),
    ]
