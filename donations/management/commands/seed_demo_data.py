import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.areas import AREAS
from donations.models import Donation, DonationEvent
from inbox.services import notifications_suppressed

FOOD_ITEMS = [
    "Bread and pastries", "Mixed vegetables", "Sandwiches and wraps", "Canned goods",
    "Fresh fruit boxes", "Dairy and yoghurt", "Rice and pasta", "Frozen meals",
    "Soup and stock", "Bakery cakes", "Salad packs", "Eggs",
]
ORG_NAMES_DONOR = ["Corner Bakery", "Fresh Grocer Co.", "CityDeli", "Community Pantry Donation Drive",
                   "Harborview Cafe", "Green Leaf Grocers", "Sunrise Bakehouse", "Metro Supermarket"]
ORG_NAMES_RECIPIENT = ["Westside Community Pantry", "Hope Kitchen", "Riverside Shelter",
                       "St. Mary's Food Relief", "Northside Youth Centre"]
STREETS = ["George St", "Church St", "Victoria Rd", "Station St", "Park Ave", "High St", "Marion St", "Pacific Hwy"]
# Demo users cluster around Sydney so distances/matching look realistic.
SYDNEY_AREAS = [k for k, a in AREAS.items() if a.state == "NSW"]
CANCEL_REASONS = ["unavailable", "safety", "mistake", "no_pickup", "elsewhere", "other"]


class Command(BaseCommand):
    help = "Floods the database with demo Donor, Recipient, Driver users and Donations."

    def add_arguments(self, parser):
        parser.add_argument("--donations", type=int, default=40)
        parser.add_argument("--donors", type=int, default=6)
        parser.add_argument("--recipients", type=int, default=5)
        parser.add_argument("--drivers", type=int, default=4)

    def handle(self, *args, **options):
        try:
            from faker import Faker
            fake = Faker("en_AU")
        except ImportError:
            self.stderr.write("Install faker first: pip install faker --break-system-packages")
            return

        User = get_user_model()
        now = timezone.now()

        def make_users(role, count, org_pool):
            users = []
            for i in range(count):
                username = f"{role.lower()}{i+1}"
                area = random.choice(SYDNEY_AREAS)
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults=dict(
                        email=f"{username}@example.com",
                        first_name=fake.first_name(),
                        last_name=fake.last_name(),
                        role=role,
                        phone=fake.phone_number()[:30],
                        area=area,
                        address=f"{random.randint(1, 240)} {random.choice(STREETS)}, {AREAS[area].label}",
                        organisation_name=random.choice(org_pool) if org_pool else "",
                        approval_status=User.Approval.APPROVED,
                        approved_at=now,
                    ),
                )
                if created:
                    user.set_password("foodbridge123")
                    user.save()
                users.append(user)
            return users

        donors = make_users(User.Role.DONOR, options["donors"], ORG_NAMES_DONOR)
        recipients = make_users(User.Role.RECIPIENT, options["recipients"], ORG_NAMES_RECIPIENT)
        drivers = make_users(User.Role.DRIVER, options["drivers"], [])

        # Showcase accounts: a driver who can't pick up yet, and sign-ups waiting for approval.
        User.objects.get_or_create(username="driver_noaddress", defaults=dict(
            email="noaddress@example.com", first_name="Nadia", last_name="Newdriver", role=User.Role.DRIVER,
            approval_status=User.Approval.APPROVED, approved_at=now,
        ))[0].set_password("foodbridge123")
        for name, role, org in (("waiting_donor", User.Role.DONOR, "Bondi Beach Bakehouse"),
                                ("waiting_pantry", User.Role.RECIPIENT, "Newtown Neighbourhood Pantry"),
                                ("waiting_driver", User.Role.DRIVER, "")):
            user, created = User.objects.get_or_create(username=name, defaults=dict(
                email=f"{name}@example.com", first_name=name.split("_")[1].title(), last_name="Applicant",
                role=role, organisation_name=org, area=random.choice(SYDNEY_AREAS),
                approval_status=User.Approval.PENDING,
            ))
            if created:
                user.set_password("foodbridge123")
                user.save()
        for user in User.objects.filter(username="driver_noaddress"):
            user.set_password("foodbridge123")
            user.save()

        if not User.objects.filter(role=User.Role.ADMIN).exists():
            User.objects.create_user(
                username="auditor", email="auditor@example.com",
                password="foodbridge123", role=User.Role.ADMIN, is_staff=True,
            )
            self.stdout.write("Created auditor/admin login: auditor / foodbridge123")

        statuses = [s[0] for s in Donation.Status.choices]
        # Pending, Assigned, In transit, Delivered, Cancelled, Expired
        weights = [30, 15, 10, 30, 6, 9]
        created_count = 0
        with notifications_suppressed():
            for _ in range(options["donations"]):
                status = random.choices(statuses, weights=weights)[0]
                donor = random.choice(donors)
                recipient = random.choice(recipients) if status in ("Assigned", "In transit", "Delivered") else None
                driver = random.choice(drivers) if status in ("In transit", "Delivered") else None
                listed = now - timedelta(hours=random.randint(2, 24 * 20))
                if status in ("Pending", "Assigned"):
                    expires = now + timedelta(hours=random.randint(1, 36))
                elif status == "Expired":
                    expires = now - timedelta(hours=random.randint(1, 48))
                else:
                    expires = listed + timedelta(hours=random.randint(24, 72))
                area = donor.area if random.random() < 0.7 else random.choice(SYDNEY_AREAS)

                d = Donation.objects.create(
                    donor=donor, recipient=recipient, driver=driver,
                    food_item=random.choice(FOOD_ITEMS),
                    quantity_kg=Decimal(random.randrange(2, 30)),
                    pickup_area=area, pickup_address=f"{random.randint(1, 240)} {random.choice(STREETS)}",
                    notes=fake.sentence() if random.random() > 0.5 else "", status=status, expires_at=expires,
                    food_category=random.choice([c[0] for c in Donation.Category.choices]),
                    storage=random.choice([c[0] for c in Donation.Storage.choices]),
                    date_type=random.choice([c[0] for c in Donation.DateType.choices]),
                    allergen_note=random.choice(["", "", "contains gluten", "contains dairy", "may contain nuts"]),
                    safety_confirmed=True,
                )
                claimed = listed + timedelta(minutes=random.randint(10, 300))
                picked = claimed + timedelta(minutes=random.randint(15, 240))
                updates = {"date_listed": listed}
                events = [(DonationEvent.Kind.LISTED, donor, "Listed", listed)]
                if recipient:
                    updates["claimed_at"] = claimed
                    events.append((DonationEvent.Kind.CLAIMED, recipient, f"Claimed by {recipient.display_name}", claimed))
                if driver:
                    updates["picked_up_at"] = picked
                    events.append((DonationEvent.Kind.PICKED_UP, driver, f"Accepted by {driver.display_name}", picked))
                if status == "Delivered":
                    delivered = picked + timedelta(minutes=random.randint(20, 120))
                    updates["delivered_at"] = delivered
                    events.append((DonationEvent.Kind.DELIVERED, driver, "Delivered", delivered))
                if status == "Cancelled":
                    reason = random.choice(CANCEL_REASONS)
                    updates.update(cancel_reason=reason, cancelled_by=donor, cancelled_at=listed + timedelta(hours=1))
                    events.append((DonationEvent.Kind.CANCELLED, donor, dict(Donation.CancelReason.choices)[reason], listed + timedelta(hours=1)))
                if status == "Expired":
                    events.append((DonationEvent.Kind.EXPIRED, None, "Reached its expiry time before delivery", expires))
                Donation.objects.filter(pk=d.pk).update(**updates)
                for kind, actor, note, when in events:
                    ev = DonationEvent.objects.create(donation=d, actor=actor, kind=kind, note=note)
                    DonationEvent.objects.filter(pk=ev.pk).update(created_at=when)
                created_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(donors)} donors, {len(recipients)} recipients, {len(drivers)} drivers, "
            f"3 accounts awaiting approval, 1 driver without an address, and {created_count} donations."
        ))
