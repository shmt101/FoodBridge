import random
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from donations.models import Donation

FOOD_ITEMS = [
    "Bread and pastries", "Mixed vegetables", "Sandwiches and wraps", "Canned goods",
    "Fresh fruit boxes", "Dairy and yoghurt", "Rice and pasta", "Frozen meals",
    "Soup and stock", "Bakery cakes", "Salad packs", "Eggs",
]
ORG_NAMES_DONOR = ["Corner Bakery", "Fresh Grocer Co.", "CityDeli", "Community Pantry Donation Drive",
                   "Harborview Cafe", "Green Leaf Grocers", "Sunrise Bakehouse", "Metro Supermarket"]
ORG_NAMES_RECIPIENT = ["Westside Community Pantry", "Hope Kitchen", "Riverside Shelter",
                       "St. Mary's Food Relief", "Northside Youth Centre"]


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
            fake = Faker()
        except ImportError:
            self.stderr.write("Install faker first: pip install faker --break-system-packages")
            return

        User = get_user_model()

        def make_users(role, count, org_pool):
            users = []
            for i in range(count):
                username = f"{role.lower()}{i+1}"
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults=dict(
                        email=f"{username}@example.com",
                        first_name=fake.first_name(),
                        last_name=fake.last_name(),
                        role=role,
                        phone=fake.phone_number()[:30],
                        address=fake.address().replace("\n", ", "),
                        organisation_name=random.choice(org_pool) if org_pool else "",
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

        if not User.objects.filter(role=User.Role.ADMIN).exists():
            admin = User.objects.create_user(
                username="auditor", email="auditor@example.com",
                password="foodbridge123", role=User.Role.ADMIN, is_staff=True,
            )
            self.stdout.write(f"Created auditor/admin login: auditor / foodbridge123")

        statuses = list(Donation.Status.choices)
        created_count = 0
        for _ in range(options["donations"]):
            status = random.choices(
                [s[0] for s in statuses],
                weights=[35, 20, 15, 25, 5],  # Pending, Assigned, In transit, Delivered, Cancelled
            )[0]
            donor = random.choice(donors)
            recipient = random.choice(recipients) if status != Donation.Status.PENDING else None
            driver = random.choice(drivers) if status in (
                Donation.Status.IN_TRANSIT, Donation.Status.DELIVERED
            ) else None

            donation = Donation.objects.create(
                donor=donor,
                recipient=recipient,
                driver=driver,
                food_item=random.choice(FOOD_ITEMS),
                quantity_kg=Decimal(random.randrange(2, 30)),
                pickup_address=fake.address().replace("\n", ", "),
                notes=fake.sentence() if random.random() > 0.5 else "",
                status=status,
            )
            if status == Donation.Status.DELIVERED:
                donation.delivered_at = timezone.now() - timezone.timedelta(days=random.randint(0, 20))
                donation.save(update_fields=["delivered_at"])
            created_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(donors)} donors, {len(recipients)} recipients, "
            f"{len(drivers)} drivers, and {created_count} donations."
        ))
