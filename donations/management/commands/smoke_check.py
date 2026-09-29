import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from donations import workflow
from donations.models import Donation


class _Rollback(Exception):
    pass


class Command(BaseCommand):
    help = ("Post-deploy self-test. Checks migrations, database, email settings, then rehearses a full "
            "donation (list -> offer -> claim -> pickup -> deliver, plus cancel/expire) on throw-away "
            "data inside a transaction that is ALWAYS rolled back. Safe to run on the live database.")

    def handle(self, *args, **options):
        failures = []

        def check(label, ok, detail=""):
            mark = self.style.SUCCESS("PASS") if ok else self.style.ERROR("FAIL")
            self.stdout.write(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
            if not ok:
                failures.append(label)

        self.stdout.write(self.style.MIGRATE_HEADING("FoodBridge smoke check"))
        engine = connection.vendor
        check("Database reachable", True, engine)
        pending = MigrationExecutor(connection).migration_plan(
            MigrationExecutor(connection).loader.graph.leaf_nodes())
        check("All migrations applied", not pending, f"{len(pending)} pending" if pending else "")
        backend = settings.EMAIL_BACKEND.rsplit(".", 2)[-2]
        if "smtp" in backend:
            check("Email configured", True, "SMTP")
        else:
            self.stdout.write(f"  [{self.style.WARNING('WARN')}] Email is console-only (set EMAIL_HOST / "
                              f"EMAIL_HOST_USER / EMAIL_HOST_PASSWORD to send real email)")
        check("SITE_URL set", bool(settings.SITE_URL), settings.SITE_URL)

        User = get_user_model()
        tag = uuid.uuid4().hex[:6]
        try:
            with transaction.atomic():
                def mk(name, role, area, addr):
                    return User.objects.create_user(
                        username=f"smoke_{name}_{tag}", password=uuid.uuid4().hex, role=role,
                        first_name=name.title(), area=area, address=addr,
                        approval_status=User.Approval.APPROVED)
                donor = mk("donor", "DONOR", "nsw-parramatta", "1 Smoke St")
                pantry = mk("pantry", "RECIPIENT", "nsw-auburn", "2 Smoke St")
                driver = mk("driver", "DRIVER", "nsw-homebush", "3 Smoke St")
                bare = mk("bare", "DRIVER", "", "")

                d = Donation.objects.create(donor=donor, food_item="Smoke test bread", quantity_kg=5,
                                            pickup_area="nsw-parramatta",
                                            expires_at=timezone.now() + timedelta(hours=6))
                check("Listing offers nearest pantry", d.offers.filter(user=pantry).exists())
                workflow.claim(d.pk, pantry)
                check("Pantry can claim (row lock ok)", Donation.objects.get(pk=d.pk).status == "Assigned")
                Donation.objects.filter(pk=d.pk).update(driver_pool_open=True)
                try:
                    workflow.accept_pickup(d.pk, bare)
                    check("Driver without address is blocked", False)
                except workflow.WorkflowError:
                    check("Driver without address is blocked", True)
                workflow.accept_pickup(d.pk, driver)
                workflow.mark_delivered(d.pk, driver)
                check("Full delivery completes", Donation.objects.get(pk=d.pk).status == "Delivered")

                d2 = Donation.objects.create(donor=donor, food_item="Smoke expiry", quantity_kg=1,
                                             pickup_area="nsw-parramatta",
                                             expires_at=timezone.now() + timedelta(hours=1))
                stats = workflow.process_timeouts(now=timezone.now() + timedelta(hours=3))
                check("Expiry sweep closes overdue listing",
                      Donation.objects.get(pk=d2.pk).status == "Expired" and stats["expired"] >= 1)
                raise _Rollback()
        except _Rollback:
            pass
        except Exception as exc:  # noqa: BLE001
            check("Workflow rehearsal", False, f"{type(exc).__name__}: {exc}")
        left = User.objects.filter(username__endswith=f"_{tag}").count()
        check("Test data rolled back", left == 0, "no throw-away rows left" if left == 0 else f"{left} rows left!")

        if failures:
            self.stdout.write(self.style.ERROR(f"\n{len(failures)} check(s) failed: {', '.join(failures)}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("\nAll checks passed."))
