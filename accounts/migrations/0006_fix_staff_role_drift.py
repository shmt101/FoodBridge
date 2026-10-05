from django.db import migrations


def fix_staff_role_drift(apps, schema_editor):
    """One-off correction: any account with is_staff/is_superuser whose role field was
    never updated (stuck at the default 'DONOR') gets corrected to 'ADMIN', matching what
    the app has treated them as everywhere else all along. Going forward, User.save()
    keeps this in sync automatically - this migration only needs to run once."""
    User = apps.get_model("accounts", "User")
    User.objects.filter(
        is_staff=True, role__in=["DONOR", "RECIPIENT", "DRIVER"],
    ).update(role="ADMIN")
    User.objects.filter(
        is_superuser=True, role__in=["DONOR", "RECIPIENT", "DRIVER"],
    ).update(role="ADMIN")


def noop_reverse(apps, schema_editor):
    pass  # not reversible - we don't know what the role was before the drift


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_alter_user_role")]
    operations = [migrations.RunPython(fix_staff_role_drift, noop_reverse)]
