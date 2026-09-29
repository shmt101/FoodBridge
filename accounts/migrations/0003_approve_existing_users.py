from django.db import migrations
from django.utils import timezone


def approve_existing(apps, schema_editor):
    """Accounts that existed before the approval gate keep working: mark them approved."""
    User = apps.get_model("accounts", "User")
    User.objects.filter(approval_status="pending").update(
        approval_status="approved", approved_at=timezone.now(),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_user_approval_status_user_approved_at_and_more"),
    ]

    operations = [
        migrations.RunPython(approve_existing, migrations.RunPython.noop),
    ]
