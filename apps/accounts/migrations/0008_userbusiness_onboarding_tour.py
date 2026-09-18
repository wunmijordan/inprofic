from django.db import migrations, models


CURRENT_TOUR_VERSION = 1


def mark_existing_memberships_introduced(apps, schema_editor):
    """Do not interrupt every existing staff member when the tour ships.

    Memberships created after this migration keep the model default (0) and
    therefore see the current onboarding tour once. Existing memberships are
    marked at the rollout version; they can still replay it from the sidebar.
    """
    UserBusiness = apps.get_model("accounts", "UserBusiness")
    UserBusiness.objects.filter(onboarding_tour_version=0).update(
        onboarding_tour_version=CURRENT_TOUR_VERSION
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_plan_limits_paid_trial_claims"),
    ]

    operations = [
        migrations.AddField(
            model_name="userbusiness",
            name="onboarding_tour_version",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Latest INPROFIC onboarding-tour version this membership completed or dismissed.",
            ),
        ),
        migrations.RunPython(mark_existing_memberships_introduced, migrations.RunPython.noop),
    ]
