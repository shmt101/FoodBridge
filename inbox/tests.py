from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from donations.models import Donation

from .models import Message, Notification
from .services import notifications_suppressed

User = get_user_model()


def make_user(username, role, **extra):
    extra.setdefault("approval_status", User.Approval.APPROVED)
    return User.objects.create_user(
        username=username, password="pass12345", role=role,
        first_name=extra.pop("first_name", username.title()), **extra,
    )


class NotificationFlowTests(TestCase):
    """Donor A lists -> pantry claims -> driver A picks up -> delivered."""

    def setUp(self):
        self.donor_a = make_user("donora", User.Role.DONOR, organisation_name="Corner Bakery")
        self.donor_b = make_user("donorb", User.Role.DONOR)
        self.pantry = make_user("pantry1", User.Role.RECIPIENT, organisation_name="Hope Kitchen", area="nsw-auburn")
        self.driver_a = make_user("drivera", User.Role.DRIVER, first_name="Dana",
                                  area="nsw-homebush", address="1 Test St")
        self.driver_b = make_user("driverb", User.Role.DRIVER)

    def texts(self, user):
        return list(Notification.objects.filter(user=user).values_list("text", flat=True))

    def test_unread_json_includes_recent_items_scoped_to_the_caller_only(self):
        Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=Decimal("12.00"))
        self.client.login(username="drivera", password="pass12345")
        data = self.client.get(reverse("inbox:unread")).json()
        self.assertEqual(data["notifications"], 1)
        self.assertEqual(len(data["recent_notifications"]), 1)
        self.assertIn("Bread", data["recent_notifications"][0]["text"])
        self.assertIn("url", data["recent_notifications"][0])
        driver_a_id = data["recent_notifications"][0]["id"]

        # driver_b got their own, separate notification for the same listing - confirm
        # it's genuinely a different row, and that driver_b's feed never shows driver_a's.
        self.client.login(username="driverb", password="pass12345")
        data_b = self.client.get(reverse("inbox:unread")).json()
        self.assertNotEqual(driver_a_id, data_b["recent_notifications"][0]["id"])

    def test_unread_json_includes_recent_messages_with_sender_name(self):
        Message.objects.create(sender=self.donor_a, recipient=self.pantry, body="Still available?")
        self.client.login(username="pantry1", password="pass12345")
        data = self.client.get(reverse("inbox:unread")).json()
        self.assertEqual(data["messages"], 1)
        self.assertIn("Still available?", data["recent_messages"][0]["text"])

    def test_admin_only_notifications_never_appear_for_other_roles(self):
        admin = User.objects.create_user("boss", password="pass12345", role="ADMIN", is_staff=True)
        Notification.objects.create(user=admin, kind="approval", text="A new donor signed up")
        for other in (self.donor_a, self.pantry, self.driver_a):
            self.client.login(username=other.username, password="pass12345")
            data = self.client.get(reverse("inbox:unread")).json()
            self.assertEqual(data["notifications"], 0)
            self.assertEqual(data["recent_notifications"], [])

    def test_new_listing_alerts_drivers_and_offers_recipients_but_not_donors(self):
        Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=Decimal("12.00"))
        for u in (self.driver_a, self.driver_b, self.pantry):
            self.assertEqual(Notification.objects.filter(user=u).count(), 1)
        self.assertIn("Bread", self.texts(self.driver_a)[0])
        self.assertIn("12 kg", self.texts(self.driver_a)[0])
        # The donor gets no "offer"/"new listing" chatter about their own donation - only
        # the one live-tracking heads-up every lister gets.
        self.assertEqual(
            Notification.objects.filter(user=self.donor_a).values_list("kind", flat=True)[0],
            "live_tracking",
        )
        self.assertEqual(Notification.objects.filter(user=self.donor_b).count(), 0)

    def test_full_lifecycle_notifies_the_right_people(self):
        d = Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=5)
        Notification.objects.all().delete()

        # Pantry claims -> donor told; pantry (actor) not told. (Drivers are now told through
        # targeted pickup offers from donations.workflow, not a broadcast on every status change.)
        d.recipient, d.status = self.pantry, Donation.Status.ASSIGNED
        d.save()
        self.assertIn("Hope Kitchen has claimed", self.texts(self.donor_a)[0])
        self.assertEqual(Notification.objects.filter(kind="ready").count(), 0)
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
        from django.utils import timezone
        from datetime import timedelta
        self.client.post(reverse("donations:donor_dashboard"), {
            "food_item": "Soup", "quantity_kg": "8", "pickup_address": "1 Main St", "notes": "",
            "pickup_area": "nsw-parramatta", "safety_confirmed": "on",
            "food_category": "bakery", "storage": "ambient", "date_type": "use_by",
            "expires_at": (timezone.localtime() + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M"),
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
        # notifications now deep-link to the page where you act on them
        self.assertRedirects(resp, reverse("donations:driver_dashboard"), fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)

    def test_inbox_page_lists_notifications_and_badge_counts(self):
        Donation.objects.create(donor=self.donor_a, food_item="Bread", quantity_kg=5)
        self.client.login(username="drivera", password="pass12345")
        page = self.client.get(reverse("inbox:home"))
        self.assertContains(page, "New listing near you")
        self.assertEqual(page.context["unread_total"], 1)
        self.client.post(reverse("inbox:mark_all_read"))
        self.assertEqual(self.client.get(reverse("inbox:home")).context["unread_total"], 0)
        self.assertEqual(self.client.get(reverse("inbox:unread")).json()["total"], 0)


class MessagingTests(TestCase):
    def setUp(self):
        self.a = make_user("alice", User.Role.DONOR, first_name="Alice")
        self.b = make_user("bob", User.Role.DRIVER, first_name="Bob")
        self.c = make_user("carol", User.Role.RECIPIENT, first_name="Carol")
        # Direct messaging is only allowed between people linked by a shared donation
        # (or with an Admin) - this one donation links every pair of a/b/c for the tests
        # below. Suppressed so it doesn't add stray notifications these tests don't expect.
        with notifications_suppressed():
            Donation.objects.create(donor=self.a, recipient=self.c, driver=self.b,
                                    food_item="Bread", quantity_kg=5, status=Donation.Status.DELIVERED)

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

    def test_nav_shows_separate_badges_for_notifications_and_messages(self):
        Message.objects.create(sender=self.b, recipient=self.a, body="hi")
        Message.objects.create(sender=self.c, recipient=self.a, body="hello")
        Notification.objects.create(user=self.a, text="Something happened", kind="info")
        self.client.login(username="alice", password="pass12345")
        page = self.client.get(reverse("donations:live"))  # any page sharing the app nav
        html = page.content.decode()
        self.assertIn('nav-badge-notif', html)
        self.assertIn('nav-badge-msg', html)
        # each badge shows its own count, not the combined total
        self.assertIn('bi-bell-fill"></i>1', html)
        self.assertIn('bi-chat-fill"></i>2', html)

    def test_nav_hides_a_badge_entirely_when_that_count_is_zero(self):
        Notification.objects.create(user=self.a, text="Something happened", kind="info")
        self.client.login(username="alice", password="pass12345")
        page = self.client.get(reverse("donations:live"))
        html = page.content.decode()
        msg_pos = html.index('class="nav-badge nav-badge-msg')
        self.assertIn('d-none', html[msg_pos:msg_pos + 40])

    def test_inbox_has_separate_notification_and_message_tabs(self):
        self.client.login(username="alice", password="pass12345")
        page = self.client.get(reverse("inbox:home"))
        self.assertContains(page, 'data-dash-tab="notifications"')
        self.assertContains(page, 'data-dash-tab="messages"')
        self.assertContains(page, 'data-dash-panel="notifications"')
        self.assertContains(page, 'data-dash-panel="messages"')
        # no unread anything yet -> notifications tab is the default
        self.assertNotContains(page, 'data-dash-panel="notifications" hidden')
        self.assertContains(page, 'data-dash-panel="messages" hidden')

    def test_inbox_defaults_to_whichever_tab_has_more_unread(self):
        Message.objects.create(sender=self.b, recipient=self.a, body="hi")
        Message.objects.create(sender=self.c, recipient=self.a, body="hello")
        self.client.login(username="alice", password="pass12345")
        page = self.client.get(reverse("inbox:home"))
        self.assertEqual(page.context["unread_messages"], 2)
        self.assertEqual(page.context["unread_notifications"], 0)
        self.assertContains(page, 'data-dash-panel="notifications" hidden')
        self.assertNotContains(page, 'data-dash-panel="messages" hidden')


class MessagingLinkageTests(TestCase):
    """Direct messaging is restricted: only people linked by a shared donation (donor,
    recipient, driver, in any combination) may message each other directly. An Admin
    can message - and broadcast to - anyone; nobody else can broadcast."""

    def setUp(self):
        self.admin = User.objects.create_user("boss", password="pass12345", role="ADMIN", is_staff=True)
        self.donor = make_user("d1", User.Role.DONOR)
        self.pantry = make_user("r1", User.Role.RECIPIENT)
        self.driver = make_user("dv1", User.Role.DRIVER)
        self.stranger = make_user("s1", User.Role.RECIPIENT)  # shares no donation with anyone
        with notifications_suppressed():
            Donation.objects.create(donor=self.donor, recipient=self.pantry, driver=self.driver,
                                    food_item="Rice", quantity_kg=4, status=Donation.Status.DELIVERED)

    def login(self, u):
        self.client.logout()
        self.client.login(username=u.username, password="pass12345")

    def test_unrelated_users_cannot_open_or_post_a_conversation(self):
        self.login(self.donor)
        self.assertEqual(self.client.get(reverse("inbox:conversation", args=[self.stranger.pk])).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("inbox:conversation", args=[self.stranger.pk]), {"body": "hi"}).status_code,
            403,
        )
        self.assertEqual(Message.objects.count(), 0)

    def test_new_message_form_only_offers_linked_people_and_admins(self):
        self.login(self.donor)
        page = self.client.get(reverse("inbox:new"))
        choices = set(page.context["form"].fields["recipient"].queryset)
        self.assertEqual(choices, {self.pantry, self.driver, self.admin})
        self.assertNotIn(self.stranger, choices)

    def test_linked_users_can_message_each_other(self):
        self.login(self.pantry)
        resp = self.client.post(reverse("inbox:conversation", args=[self.driver.pk]), {"body": "On your way?"})
        self.assertRedirects(resp, reverse("inbox:conversation", args=[self.driver.pk]))
        self.assertEqual(Message.objects.filter(sender=self.pantry, recipient=self.driver).count(), 1)

    def test_anyone_can_message_an_admin_even_without_a_shared_donation(self):
        self.login(self.stranger)
        resp = self.client.post(reverse("inbox:conversation", args=[self.admin.pk]), {"body": "Need help"})
        self.assertRedirects(resp, reverse("inbox:conversation", args=[self.admin.pk]))

    def test_admin_can_message_anyone_unlinked(self):
        self.login(self.admin)
        resp = self.client.post(reverse("inbox:conversation", args=[self.stranger.pk]), {"body": "Welcome!"})
        self.assertRedirects(resp, reverse("inbox:conversation", args=[self.stranger.pk]))

    def test_only_admin_can_broadcast(self):
        for u in (self.donor, self.pantry, self.driver, self.stranger):
            self.login(u)
            self.assertEqual(self.client.get(reverse("inbox:broadcast")).status_code, 403)

    def test_admin_broadcast_reaches_every_other_active_account(self):
        self.login(self.admin)
        resp = self.client.post(reverse("inbox:broadcast"), {"body": "Site maintenance tonight"})
        self.assertEqual(resp.status_code, 200)
        others = {self.donor, self.pantry, self.driver, self.stranger}
        sent = Message.objects.filter(body="Site maintenance tonight", is_broadcast=True)
        self.assertEqual(set(sent.values_list("recipient", flat=True)), {u.pk for u in others})
        self.assertFalse(sent.filter(recipient=self.admin).exists())
        # a broadcast recipient can now read it and reply to the admin
        self.login(self.stranger)
        thread = self.client.get(reverse("inbox:conversation", args=[self.admin.pk]))
        self.assertContains(thread, "Site maintenance tonight")


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

    def test_landing_also_shows_the_live_showcase_box(self):
        donor = make_user("showcasedonor", User.Role.DONOR, area="nsw-newtown")
        Donation.objects.create(donor=donor, food_item="Bread", quantity_kg=5,
                                food_category="bakery", pickup_area="nsw-newtown", safety_confirmed=True)
        resp = self.client.get("/")
        self.assertContains(resp, 'id="showcaseBox"')
        self.assertContains(resp, "What's live on FoodBridge")
        html = resp.content.decode()
        self.assertIn("Bakery", html)  # the live category actually appears in the embedded JSON

    def test_home_nav_is_search_box_plus_home_and_login_signup_text_links(self):
        nav = self.nav(reverse("home"))
        self.assertIn('type="search"', nav)              # search text box
        self.assertIn(">Home</a>", nav)
        self.assertIn(">Login</a>", nav)
        self.assertIn(">Sign up</a>", nav)
        self.assertNotIn("About", nav)                   # About is not in the nav any more
        self.assertNotIn("btn-nav", nav)                 # text links, not boxed buttons
        self.assertNotIn(">Search</a>", nav)

    def test_home_is_single_screen_with_about_and_watch_video_buttons(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertNotIn("Search Donations", html)
        self.assertIn("Watch Video", html)
        self.assertIn(f'href="{reverse("about")}"', html)
        self.assertNotIn('id="about"', html)
        self.assertNotIn('id="team"', html)
        self.assertEqual(self.client.get(reverse("about")).status_code, 200)

    def test_showcase_box_uses_a_real_photo_when_one_exists_for_that_category(self):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = BytesIO()
        Image.new("RGB", (30, 30), color=(10, 20, 30)).save(buf, format="JPEG")
        photo = SimpleUploadedFile("bread.jpg", buf.getvalue(), content_type="image/jpeg")

        donor = make_user("photoshowdonor", User.Role.DONOR, area="nsw-newtown")
        d = Donation.objects.create(donor=donor, food_item="Bread", quantity_kg=5,
                                    food_category="bakery", pickup_area="nsw-newtown", safety_confirmed=True)
        d.photo = photo
        d.save()
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn(d.photo.url, html)

    def test_showcase_box_falls_back_to_icon_when_no_photo_exists(self):
        donor = make_user("noPhotoDonor", User.Role.DONOR, area="nsw-newtown")
        Donation.objects.create(donor=donor, food_item="Rice", quantity_kg=5,
                                food_category="packaged", pickup_area="nsw-newtown", safety_confirmed=True)
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("bi-box-seam-fill", html)  # the packaged-category fallback icon

    def test_home_hero_shows_the_showcase_box_instead_of_the_static_illustration(self):
        donor = make_user("showcasedonor2", User.Role.DONOR, area="nsw-newtown")
        Donation.objects.create(donor=donor, food_item="Bread", quantity_kg=5,
                                food_category="bakery", pickup_area="nsw-newtown", safety_confirmed=True)
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn('id="showcaseBox"', html)
        self.assertNotIn("foodbridge-hero.png", html)
        self.assertNotIn("showcase-section", html)  # the separate section below is gone on this page
        self.assertIn("Bakery", html)

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
        make_user("searcher", User.Role.RECIPIENT)
        self.client.login(username="searcher", password="pass12345")
        resp = self.client.get(reverse("donations:live"), {"q": "sourdough"})
        self.assertContains(resp, "Sourdough")

    def test_portal_nav_has_live_dashboard_inbox_profile(self):
        make_user("u2", User.Role.DONOR)
        self.client.login(username="u2", password="pass12345")
        html = self.client.get(reverse("accounts:profile")).content.decode()
        header = html.split("<header", 1)[1].split("</header>", 1)[0]
        for label in ("Live Donations", "Dashboard", "Inbox", "Profile"):
            self.assertIn(label, header)


class PersonalizedHomeTests(TestCase):
    def test_anonymous_visitor_sees_the_generic_marketing_hero(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("Rescuing surplus food", html)
        self.assertNotIn("Welcome back", html)

    def test_pending_user_sees_a_status_specific_hero(self):
        make_user("newbie", User.Role.DONOR, approval_status=User.Approval.PENDING)
        self.client.login(username="newbie", password="pass12345")
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("Welcome, <em>Newbie</em>", html)
        self.assertIn("reviewing your account", html)
        self.assertNotIn("Rescuing surplus food", html)

    def test_donor_sees_their_own_stats_and_dashboard_link(self):
        donor = make_user("donor1", User.Role.DONOR, first_name="Priya")
        from donations.models import Donation
        Donation.objects.create(donor=donor, food_item="Bread", quantity_kg=4)
        self.client.login(username="donor1", password="pass12345")
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("Welcome back", html)
        self.assertIn("Priya", html)
        self.assertIn("1 active listing", html)
        self.assertIn(reverse("donations:donor_dashboard"), html)

    def test_admin_sees_pending_approval_count(self):
        User.objects.create_user("boss", password="pass12345", role="ADMIN", is_staff=True)
        make_user("waiting", User.Role.DONOR, approval_status=User.Approval.PENDING)
        self.client.login(username="boss", password="pass12345")
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("1 account", html)
        self.assertIn(reverse("accounts:approvals"), html)

    def test_auditor_sees_report_shortcut(self):
        User.objects.create_user("checker", password="pass12345", role="AUDITOR")
        self.client.login(username="checker", password="pass12345")
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("Welcome back", html)
        self.assertIn(reverse("donations:partnership_report"), html)
