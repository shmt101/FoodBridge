from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from donations.models import Donation

from .models import Message, Notification
from .services import notifications_suppressed

User = get_user_model()


def make_user(username, role, **extra):
    return User.objects.create_user(
        username=username, password="pass12345", role=role,
        first_name=extra.pop("first_name", username.title()), **extra,
    )


class NotificationFlowTests(TestCase):
    """Donor A lists -> pantry claims -> driver A picks up -> delivered."""

    def setUp(self):
        self.donor_a = make_user("donora", User.Role.DONOR, organisation_name="Corner Bakery")
        self.donor_b = make_user("donorb", User.Role.DONOR)
        self.pantry = make_user("pantry1", User.Role.RECIPIENT, organisation_name="Hope Kitchen")
        self.driver_a = make_user("drivera", User.Role.DRIVER, first_name="Dana")
        self.driver_b = make_user("driverb", User.Role.DRIVER)

    def texts(self, user):
        return list(Notification.objects.filter(user=user).values_list("text", flat=True))

    def test_new_listing_notifies_drivers_and_recipients_but_not_donors(self):
        Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=Decimal("12.00"))
        for u in (self.driver_a, self.driver_b, self.pantry):
            self.assertEqual(Notification.objects.filter(user=u).count(), 1)
        self.assertIn("Bread", self.texts(self.driver_a)[0])
        self.assertIn("12 kg", self.texts(self.driver_a)[0])
        self.assertEqual(Notification.objects.filter(user__in=[self.donor_a, self.donor_b]).count(), 0)

    def test_full_lifecycle_notifies_the_right_people(self):
        d = Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=5)
        Notification.objects.all().delete()

        # Pantry claims -> donor told, drivers told it's ready for pickup, pantry (actor) not told
        d.recipient, d.status = self.pantry, Donation.Status.ASSIGNED
        d.save()
        self.assertIn("Hope Kitchen has claimed", self.texts(self.donor_a)[0])
        self.assertEqual(Notification.objects.filter(user=self.driver_a, kind="ready").count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.driver_b, kind="ready").count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.pantry).count(), 0)
        self.assertEqual(Notification.objects.filter(user=self.donor_b).count(), 0)

        # Driver A picks it up -> donor A (and pantry) told, naming the driver
        Notification.objects.all().delete()
        d.driver, d.status = self.driver_a, Donation.Status.IN_TRANSIT
        d.save()
        donor_texts = self.texts(self.donor_a)
        self.assertEqual(len(donor_texts), 1)
        self.assertIn("picked up by Dana", donor_texts[0])
        self.assertEqual(Notification.objects.filter(user=self.pantry, kind="picked_up").count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.driver_a).count(), 0)

        # Delivered -> donor and pantry told
        Notification.objects.all().delete()
        d.status = Donation.Status.DELIVERED
        d.save()
        self.assertEqual(Notification.objects.filter(user=self.donor_a, kind="delivered").count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.pantry, kind="delivered").count(), 1)

    def test_saving_without_a_status_change_creates_nothing(self):
        d = Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=5)
        Notification.objects.all().delete()
        d.notes = "Ring the back bell"
        d.save()
        d.save(update_fields=["notes"])
        self.assertEqual(Notification.objects.count(), 0)

    def test_cancellation_notifies_everyone_involved(self):
        d = Donation.objects.create(
            donor=self.donor_a, recipient=self.pantry, driver=self.driver_a,
            food_item="Bread", quantity_kg=5, status=Donation.Status.IN_TRANSIT,
        )
        Notification.objects.all().delete()
        d.status = Donation.Status.CANCELLED
        d.save()
        got = set(Notification.objects.values_list("user__username", flat=True))
        self.assertEqual(got, {"donora", "pantry1", "drivera"})

    def test_suppressed_context_creates_no_notifications(self):
        with notifications_suppressed():
            Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=5)
        self.assertEqual(Notification.objects.count(), 0)

    def test_dashboard_flow_end_to_end_via_views(self):
        self.client.login(username="donora", password="pass12345")
        self.client.post(reverse("donations:donor_dashboard"), {
            "food_item": "Soup", "quantity_kg": "8", "pickup_address": "1 Main St", "notes": "",
        })
        donation = Donation.objects.get(food_item="Soup")
        self.client.logout()

        self.client.login(username="pantry1", password="pass12345")
        self.client.post(reverse("donations:recipient_dashboard"), {"donation_id": donation.pk})
        self.client.logout()

        self.client.login(username="drivera", password="pass12345")
        self.client.post(reverse("donations:driver_dashboard"), {"donation_id": donation.pk, "action": "accept"})
        self.client.logout()

        self.assertTrue(any("picked up by Dana" in t for t in self.texts(self.donor_a)))

    def test_notification_click_marks_read_and_is_private(self):
        Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=5)
        n = Notification.objects.get(user=self.driver_a)
        self.client.login(username="driverb", password="pass12345")
        self.assertEqual(self.client.get(reverse("inbox:notification_open", args=[n.pk])).status_code, 404)
        self.client.login(username="drivera", password="pass12345")
        resp = self.client.get(reverse("inbox:notification_open", args=[n.pk]))
        self.assertRedirects(resp, reverse("accounts:dashboard"), fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)

    def test_inbox_page_lists_notifications_and_badge_counts(self):
        Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=5)
        self.client.login(username="drivera", password="pass12345")
        page = self.client.get(reverse("inbox:home"))
        self.assertContains(page, "New donation listed")
        self.assertEqual(page.context["unread_total"], 1)
        self.client.post(reverse("inbox:mark_all_read"))
        self.assertEqual(self.client.get(reverse("inbox:home")).context["unread_total"], 0)
        self.assertEqual(self.client.get(reverse("inbox:unread")).json()["total"], 0)


class MessagingTests(TestCase):
    def setUp(self):
        self.a = make_user("alice", User.Role.DONOR, first_name="Alice")
        self.b = make_user("bob", User.Role.DRIVER, first_name="Bob")
        self.c = make_user("carol", User.Role.RECIPIENT, first_name="Carol")

    def test_send_message_and_recipient_sees_unread_then_read(self):
        self.client.login(username="alice", password="pass12345")
        resp = self.client.post(reverse("inbox:new"), {"recipient": self.b.pk, "body": "Pickup at 3pm?"})
        self.assertRedirects(resp, reverse("inbox:conversation", args=[self.b.pk]))
        self.client.logout()

        self.client.login(username="bob", password="pass12345")
        home = self.client.get(reverse("inbox:home"))
        self.assertEqual(home.context["unread_messages"], 1)
        self.assertContains(home, "Pickup at 3pm?")

        thread = self.client.get(reverse("inbox:conversation", args=[self.a.pk]))
        self.assertContains(thread, "Pickup at 3pm?")
        self.assertEqual(self.client.get(reverse("inbox:unread")).json()["messages"], 0)

        self.client.post(reverse("inbox:conversation", args=[self.a.pk]), {"body": "Yes, see you then"})
        self.assertEqual(Message.objects.filter(sender=self.b, recipient=self.a).count(), 1)

    def test_conversations_are_private(self):
        Message.objects.create(sender=self.a, recipient=self.b, body="secret between a and b")
        self.client.login(username="carol", password="pass12345")
        page = self.client.get(reverse("inbox:conversation", args=[self.a.pk]))
        self.assertNotContains(page, "secret between a and b")
        self.assertNotContains(self.client.get(reverse("inbox:home")), "secret between a and b")

    def test_cannot_message_self_or_send_blank(self):
        self.client.login(username="alice", password="pass12345")
        self.assertRedirects(self.client.get(reverse("inbox:conversation", args=[self.a.pk])), reverse("inbox:home"))
        resp = self.client.post(reverse("inbox:new"), {"recipient": self.a.pk, "body": "hi me"})
        self.assertEqual(resp.status_code, 200)   # form re-rendered with an error
        resp = self.client.post(reverse("inbox:new"), {"recipient": self.b.pk, "body": "   "})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Message.objects.count(), 0)

    def test_login_required(self):
        for name, args in (("inbox:home", []), ("inbox:new", []), ("inbox:conversation", [self.a.pk])):
            resp = self.client.get(reverse(name, args=args))
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/accounts/login/", resp["Location"])
        self.assertEqual(self.client.get(reverse("inbox:unread")).status_code, 401)


class DashboardGreetingTests(TestCase):
    def test_each_role_sees_hello_with_their_first_name(self):
        for username, role, url in (
            ("d", User.Role.DONOR, "donations:donor_dashboard"),
            ("r", User.Role.RECIPIENT, "donations:recipient_dashboard"),
            ("v", User.Role.DRIVER, "donations:driver_dashboard"),
        ):
            make_user(username, role, first_name="Priya")
            self.client.login(username=username, password="pass12345")
            self.assertContains(self.client.get(reverse(url)), "Hello, <span>Priya</span>!", html=False)
            self.client.logout()

    def test_admin_dashboard_greets_and_falls_back_to_username(self):
        User.objects.create_user("auditor", password="pass12345", role=User.Role.ADMIN, is_staff=True)
        self.client.login(username="auditor", password="pass12345")
        self.assertContains(self.client.get(reverse("donations:admin_dashboard")), "Hello, <span>auditor</span>!")

    def test_login_lands_on_greeting(self):
        make_user("sam", User.Role.DONOR, first_name="Sam")
        resp = self.client.post(reverse("accounts:login"), {"username": "sam", "password": "pass12345"}, follow=True)
        self.assertContains(resp, "Hello, <span>Sam</span>!")


class PublicPagesTests(TestCase):
    def nav(self, url):
        html = self.client.get(url).content.decode()
        return html.split("<nav", 1)[1].split("</nav>", 1)[0]

    def test_landing_has_no_navigation(self):
        html = self.client.get("/").content.decode()
        self.assertNotIn("<nav", html)
        self.assertContains(self.client.get("/"), "Explore FoodBridge")

    def test_home_nav_is_search_box_plus_home_and_login_signup_text_links(self):
        nav = self.nav(reverse("home"))
        self.assertIn('type="search"', nav)              # search text box
        self.assertIn(">Home</a>", nav)
        self.assertIn(">Login</a>", nav)
        self.assertIn(">Sign up</a>", nav)
        self.assertNotIn("About", nav)                   # About is not in the nav any more
        self.assertNotIn("btn-nav", nav)                 # text links, not boxed buttons
        self.assertNotIn(">Search</a>", nav)

    def test_home_is_single_screen_with_search_and_about_buttons(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("Search Donations", html)
        self.assertIn(f'href="{reverse("about")}"', html)
        self.assertNotIn('id="about"', html)
        self.assertNotIn('id="team"', html)
        self.assertEqual(self.client.get(reverse("about")).status_code, 200)

    def test_home_nav_when_logged_in(self):
        make_user("u1", User.Role.DONOR)
        self.client.login(username="u1", password="pass12345")
        nav = self.nav(reverse("home"))
        self.assertIn("Dashboard", nav)
        self.assertIn("Inbox", nav)
        self.assertIn("Log out", nav)
        self.assertNotIn(">Login</a>", nav)

    def test_search_box_submits_to_live_donations(self):
        Donation.objects.create(donor=make_user("dd", User.Role.DONOR), food_item="Sourdough", quantity_kg=3)
        resp = self.client.get(reverse("donations:live"), {"q": "sourdough"})
        self.assertContains(resp, "Sourdough")

    def test_portal_nav_has_live_dashboard_inbox_profile(self):
        make_user("u2", User.Role.DONOR)
        self.client.login(username="u2", password="pass12345")
        html = self.client.get(reverse("accounts:profile")).content.decode()
        header = html.split("<header", 1)[1].split("</header>", 1)[0]
        for label in ("Live Donations", "Dashboard", "Inbox", "Profile"):
            self.assertIn(label, header)
