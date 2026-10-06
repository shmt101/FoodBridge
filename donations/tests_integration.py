"""
Integration tests for FoodBridge.

Unlike tests.py (many small, isolated unit/view tests - one thing checked per test), this
module runs a handful of LARGE scenarios that walk a request through every app together
the way a real session would: accounts (signup/approval), donations (listing/matching/
workflow), inbox (notifications/messages), and reporting, all in one continuous story.
Assertions check the system's actual end state after each step, not an isolated unit.

Run just this module:
    python manage.py test donations.tests_integration -v 2

Run everything (this module plus the existing unit/view suites):
    python manage.py test
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from donations import workflow
from donations.models import Donation, Feedback, Offer
from inbox.models import Notification

User = get_user_model()
PW = "pass12345"
SIGNUP_PW = "S0mething-long-77"


def make_user(username, role, area="", address="", org="", approved=True, **extra):
    user = User.objects.create_user(
        username=username, password=PW, role=role, first_name=username.title(),
        organisation_name=org, area=area, address=address, **extra,
    )
    if approved:
        user.approval_status = User.Approval.APPROVED
        user.save()
    return user


def signup_payload(username, role, **extra):
    data = {
        "username": username, "first_name": username.title(), "last_name": "Test",
        "email": f"{username}@example.com", "role": role,
        "password1": SIGNUP_PW, "password2": SIGNUP_PW, "agree_terms": "on",
    }
    data.update(extra)
    return data


@override_settings(OFFER_WAVE_SIZE=2, OFFER_MAX_WAVES=3, OFFER_TIMEOUT_MINUTES=30,
                    OFFER_PROCESS_THROTTLE_SECONDS=0, ALERT_RADIUS_KM=100)
class FullDonationLifecycleIntegrationTest(TestCase):
    """The system's main story, start to finish, across every app:

    donor signs up -> admin approves -> donor completes their profile and lists food with
    a photo -> system offers it to the nearest pantry and alerts nearby drivers -> pantry
    claims it -> system offers pickup to the nearest driver -> driver accepts, delivers ->
    everyone rates it -> the numbers show up correctly in the admin dashboard and the
    auditor's Partnership Activity Report, including a CSV export.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            "boss", password=PW, role="ADMIN", is_staff=True, email="boss@example.com")
        self.auditor = User.objects.create_user("checker", password=PW, role="AUDITOR")

    def test_full_lifecycle_across_every_app(self):
        # ---- 1. Donor signs up and lands in the approval queue, not the dashboard ----
        resp = self.client.post(reverse("accounts:signup"), signup_payload(
            "freshgrocer", "DONOR", organisation_name="Fresh Grocer Co."))
        self.assertRedirects(resp, reverse("accounts:pending"))
        donor = User.objects.get(username="freshgrocer")
        self.assertFalse(donor.is_approved)
        self.assertTrue(
            Notification.objects.filter(user=self.admin, kind="approval", text__icontains="freshgrocer").exists(),
            "admin should be notified of the new signup",
        )
        resp = self.client.get(reverse("donations:donor_dashboard"), follow=True)
        self.assertRedirects(resp, reverse("accounts:pending"))

        # ---- 2. Admin approves the donor ----
        self.client.logout()
        self.client.login(username="boss", password=PW)
        resp = self.client.post(reverse("accounts:approvals"), {"user_id": donor.pk, "action": "approve"})
        self.assertEqual(resp.status_code, 302)
        donor.refresh_from_db()
        self.assertTrue(donor.is_approved)
        self.assertTrue(Notification.objects.filter(user=donor, text__icontains="approved").exists())

        # ---- 3. Donor lists food ----
        self.client.logout()
        self.client.login(username="freshgrocer", password=SIGNUP_PW)

        pantry = make_user("hopekitchen", User.Role.RECIPIENT, area="nsw-auburn",
                            address="2 A St", org="Hope Kitchen", email="hope@example.com")
        driver = make_user("ronald", User.Role.DRIVER, area="nsw-homebush",
                            address="3 C St", email="ronald@example.com")

        exp = (timezone.localtime() + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M")
        resp = self.client.post(reverse("donations:donor_dashboard"), {
            "action": "list", "food_item": "Mixed vegetables", "food_category": "produce",
            "quantity_kg": "15", "storage": "chilled", "date_type": "use_by",
            "allergen_note": "", "pickup_area": "nsw-parramatta", "pickup_address": "10 Church St",
            "expires_at": exp, "notes": "", "safety_confirmed": "on",
        }, follow=True)
        self.assertContains(resp, "Donation listed")
        donation = Donation.objects.get(food_item="Mixed vegetables")
        self.assertEqual(donation.donor, donor)
        self.assertTrue(donation.safety_confirmed)

        # the nearest pantry gets a time-limited offer; nearby drivers get a heads-up
        self.assertTrue(donation.offers.filter(user=pantry, role="RECIPIENT", status="offered").exists())
        self.assertTrue(Notification.objects.filter(user=pantry, kind="offer", donation=donation).exists())
        self.assertTrue(Notification.objects.filter(user=driver, kind="new_donation", donation=donation).exists())

        # ---- 4. Pantry claims it ----
        self.client.logout()
        self.client.login(username="hopekitchen", password=PW)
        resp = self.client.post(reverse("donations:recipient_dashboard"), {
            "action": "claim", "donation_id": donation.pk,
        }, follow=True)
        self.assertContains(resp, "Claimed")
        donation.refresh_from_db()
        self.assertEqual(donation.status, "Assigned")
        self.assertEqual(donation.recipient, pantry)
        self.assertTrue(Notification.objects.filter(user=donor, kind="claimed").exists())

        # claiming closes the recipient offer and opens a driver offer
        self.assertTrue(donation.offers.filter(user=driver, role="DRIVER", status="offered").exists())

        # ---- 5. Driver accepts and delivers ----
        self.client.logout()
        self.client.login(username="ronald", password=PW)
        resp = self.client.post(reverse("donations:driver_dashboard"), {
            "action": "accept", "donation_id": donation.pk,
        }, follow=True)
        self.assertContains(resp, "now delivering")
        donation.refresh_from_db()
        self.assertEqual(donation.status, "In transit")
        self.assertEqual(donation.driver, driver)
        self.assertTrue(Notification.objects.filter(user=donor, kind="picked_up").exists())
        self.assertTrue(Notification.objects.filter(user=pantry, kind="picked_up").exists())

        resp = self.client.post(reverse("donations:driver_dashboard"), {
            "action": "delivered", "donation_id": donation.pk,
        }, follow=True)
        self.assertContains(resp, "delivered")
        donation.refresh_from_db()
        self.assertEqual(donation.status, "Delivered")
        self.assertTrue(Notification.objects.filter(user=donor, kind="delivered").exists())
        self.assertTrue(Notification.objects.filter(user=pantry, kind="delivered").exists())

        # full timeline recorded in order
        kinds = list(donation.events.values_list("kind", flat=True))
        self.assertEqual(kinds, ["listed", "offered", "claimed", "offered", "picked_up", "delivered"])

        # ---- 6. Everyone rates the delivery ----
        self.client.logout()
        self.client.login(username="hopekitchen", password=PW)
        self.client.post(reverse("donations:rate", args=[donation.pk]),
                         {"rating": "5", "comment": "Great quality"})
        self.client.logout()
        self.client.login(username="ronald", password=PW)
        self.client.post(reverse("donations:rate", args=[donation.pk]), {"rating": "4", "comment": ""})
        self.assertEqual(Feedback.objects.filter(donation=donation).count(), 2)

        # a reported problem notifies admins
        self.client.logout()
        self.client.login(username="freshgrocer", password=SIGNUP_PW)
        self.client.post(reverse("donations:rate", args=[donation.pk]), {
            "rating": "2", "comment": "Box was a bit damaged", "is_issue": "on",
        })
        self.assertTrue(Notification.objects.filter(
            user=self.admin, kind="issue", text__icontains="damaged").exists())

        # ---- 7. The admin sees it, and can now also reach reporting/CSV alongside the auditor ----
        self.client.logout()
        self.client.login(username="boss", password=PW)
        page = self.client.get(reverse("donations:admin_dashboard"))
        self.assertEqual(page.context["counts"]["delivered"], 1)
        self.assertEqual(self.client.get(reverse("donations:partnership_report")).status_code, 200)

        self.client.logout()
        self.client.login(username="checker", password=PW)
        self.assertEqual(self.client.get(reverse("donations:admin_dashboard")).status_code, 403)
        report = self.client.get(reverse("donations:partnership_report"), {"range": "all"})
        self.assertContains(report, "Fresh Grocer Co.")
        self.assertContains(report, "Hope Kitchen")
        self.assertContains(report, "15.0")
        csv_resp = self.client.get(reverse("donations:partnership_report"),
                                   {"export": "partnerships", "range": "all"})
        self.assertEqual(csv_resp["Content-Type"], "text/csv")
        self.assertIn("Fresh Grocer Co.", csv_resp.content.decode())

        # public map shows the area, never the street address
        self.client.logout()
        map_data = self.client.get(reverse("donations:map_data")).json()
        self.assertNotIn("Church St", str(map_data))


@override_settings(OFFER_WAVE_SIZE=1, OFFER_MAX_WAVES=2, OFFER_TIMEOUT_MINUTES=30,
                    OFFER_PROCESS_THROTTLE_SECONDS=0)
class ReassignmentAndExpiryIntegrationTest(TestCase):
    """A messier, more realistic story: a claim gets released, a driver backs out, an
    offer times out and the pool opens up, and a separate uncollected listing expires -
    checking the system keeps re-matching correctly through all of it."""

    def setUp(self):
        self.donor = make_user("corner", User.Role.DONOR, area="nsw-parramatta", address="1 A St")
        self.near = make_user("near_pantry", User.Role.RECIPIENT, area="nsw-auburn", address="2 B St")
        self.far = make_user("far_pantry", User.Role.RECIPIENT, area="nsw-newcastle", address="3 C St")
        self.driver_a = make_user("driver_a", User.Role.DRIVER, area="nsw-homebush", address="4 D St")
        self.driver_b = make_user("driver_b", User.Role.DRIVER, area="nsw-gosford", address="5 E St")

    def test_release_withdraw_timeout_and_expiry_all_reassign_correctly(self):
        donation = Donation.objects.create(
            donor=self.donor, food_item="Bread", quantity_kg=Decimal("8"),
            pickup_area="nsw-parramatta", expires_at=timezone.now() + timedelta(hours=8),
        )
        self.assertTrue(donation.offers.filter(user=self.near, status="offered").exists())
        self.assertFalse(donation.offers.filter(user=self.far).exists())

        workflow.claim(donation.pk, self.near)
        workflow.release_claim(donation.pk, self.near, "no_storage", "Freezer broke")
        donation.refresh_from_db()
        self.assertEqual((donation.status, donation.recipient), ("Pending", None))
        self.assertIn("No storage", Notification.objects.get(user=self.donor, kind="released").text)
        self.assertFalse(donation.offers.filter(user=self.near, status="offered").exists())
        self.assertTrue(donation.offers.filter(user=self.far, status="offered").exists())

        workflow.claim(donation.pk, self.far)
        self.assertTrue(donation.offers.filter(user=self.driver_a, role="DRIVER", status="offered").exists())
        later = timezone.now() + timedelta(minutes=31)
        stats = workflow.process_timeouts(now=later)
        self.assertEqual(stats["offers_timed_out"], 1)
        donation.refresh_from_db()
        self.assertTrue(donation.driver_pool_open)

        workflow.accept_pickup(donation.pk, self.driver_b)
        workflow.withdraw_pickup(donation.pk, self.driver_b, "vehicle", "Van broke down")
        donation.refresh_from_db()
        self.assertEqual((donation.status, donation.driver), ("Assigned", None))
        self.assertTrue(donation.driver_pool_open)

        workflow.accept_pickup(donation.pk, self.driver_a)
        workflow.mark_delivered(donation.pk, self.driver_a)
        self.assertEqual(Donation.objects.get(pk=donation.pk).status, "Delivered")

        expiring = Donation.objects.create(
            donor=self.donor, food_item="Milk", quantity_kg=Decimal("3"),
            pickup_area="nsw-parramatta", expires_at=timezone.now() + timedelta(hours=1),
        )
        workflow.process_timeouts(now=timezone.now() + timedelta(hours=3))
        expiring.refresh_from_db()
        self.assertEqual(expiring.status, "Expired")
        self.assertTrue(Notification.objects.filter(user=self.donor, kind="expired", donation=expiring).exists())
        with self.assertRaises(workflow.WorkflowError):
            workflow.claim(expiring.pk, self.near)


class EmailAndSecurityIntegrationTest(TestCase):
    """Cuts across accounts + donations + email: password reset end to end, login
    throttling, and the approval gate all enforced together in one realistic session."""

    def setUp(self):
        self.donor = make_user("locked_out", User.Role.DONOR, email="lockedout@example.com")

    def test_password_reset_then_login_throttle_then_successful_login(self):
        self.client.post(reverse("accounts:password_reset"), {"email": "lockedout@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        link = [w for w in mail.outbox[0].body.split() if "/accounts/reset/" in w][0]
        path = "/" + link.split("://", 1)[1].split("/", 1)[1]
        confirm_page = self.client.get(path, follow=True)
        post_url = confirm_page.redirect_chain[-1][0] if confirm_page.redirect_chain else path
        self.client.post(post_url, {"new_password1": "Brand-New-Pass-1", "new_password2": "Brand-New-Pass-1"})

        for _ in range(5):
            self.client.post(reverse("accounts:login"), {"username": "locked_out", "password": "wrong"})
        resp = self.client.post(reverse("accounts:login"),
                                {"username": "locked_out", "password": "Brand-New-Pass-1"})
        self.assertEqual(resp.status_code, 429)

        make_user("someone_else", User.Role.DONOR)
        self.assertEqual(self.client.post(reverse("accounts:login"),
                                          {"username": "someone_else", "password": PW}).status_code, 302)

    def test_unapproved_user_is_gated_everywhere_except_public_pages(self):
        self.client.post(reverse("accounts:signup"), signup_payload("brandnew", "DONOR"))
        for name in ("donations:donor_dashboard", "inbox:home", "accounts:dashboard",
                     "donations:partnership_report"):
            resp = self.client.get(reverse(name), follow=True)
            self.assertEqual(resp.redirect_chain[-1][0], reverse("accounts:pending"), name)
        for name in ("home", "donations:live", "accounts:profile", "about"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)


class AdminUserManagementIntegrationTest(TestCase):
    """Admin creates an Auditor account through the in-app form (not /admin/), the
    Auditor logs in and lands on the report page, and account lifecycle actions
    (deactivate / role change) all take effect immediately."""

    def setUp(self):
        self.superuser = User.objects.create_superuser("root", "root@example.com", PW)
        self.donor = make_user("regular", User.Role.DONOR, org="Acme Co")

    def test_create_auditor_login_and_manage_users_end_to_end(self):
        self.client.login(username="root", password=PW)
        resp = self.client.post(reverse("accounts:add_user"), {
            "username": "newauditor", "first_name": "New", "role": "AUDITOR",
            "password1": SIGNUP_PW, "password2": SIGNUP_PW,
        }, follow=True)
        self.assertContains(resp, "Created the Auditor account")
        self.client.logout()

        self.assertTrue(self.client.login(username="newauditor", password=SIGNUP_PW))
        self.assertRedirects(self.client.get(reverse("accounts:dashboard")),
                             reverse("donations:partnership_report"))
        self.client.logout()

        self.client.login(username="root", password=PW)
        self.client.post(reverse("accounts:manage_users"), {"user_id": self.donor.pk, "action": "deactivate"})
        self.donor.refresh_from_db()
        self.assertFalse(self.donor.is_active)
        self.client.logout()
        self.assertFalse(self.client.login(username="regular", password=PW))

        self.client.login(username="root", password=PW)
        self.client.post(reverse("accounts:manage_users"), {"user_id": self.donor.pk, "action": "activate"})
        self.client.post(reverse("accounts:manage_users"), {
            "user_id": self.donor.pk, "action": "role", "role": "DRIVER",
        })
        self.donor.refresh_from_db()
        self.assertTrue(self.donor.is_active)
        self.assertEqual(self.donor.role, "DRIVER")
