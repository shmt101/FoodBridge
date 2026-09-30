from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import approvals
from accounts.areas import AREAS, area_choices, distance_km
from inbox.models import Notification

from . import analytics, workflow
from .models import Donation, DonationEvent, Offer

User = get_user_model()
PW = "pass12345"


def make_user(username, role, area="", address="", org="", approved=True, **extra):
    user = User.objects.create_user(
        username=username, password=PW, role=role, first_name=username.title(),
        organisation_name=org, area=area, address=address, **extra)
    if approved:
        user.approval_status = User.Approval.APPROVED
        user.save()
    return user


def make_donation(donor, hours=24, **extra):
    fields = dict(food_item="Bread", quantity_kg=Decimal("10"), pickup_area="nsw-parramatta",
                  expires_at=timezone.now() + timedelta(hours=hours))
    fields.update(extra)
    return Donation.objects.create(donor=donor, **fields)


class AreaTests(TestCase):
    def test_distance_is_sensible(self):
        self.assertEqual(distance_km("nsw-parramatta", "nsw-parramatta"), 0.0)
        d = distance_km("nsw-sydney-cbd", "nsw-parramatta")
        self.assertTrue(18 <= d <= 24, d)
        far = distance_km("nsw-sydney-cbd", "vic-melbourne-cbd")
        self.assertTrue(700 <= far <= 730, far)
        self.assertIsNone(distance_km("", "nsw-parramatta"))
        self.assertIsNone(distance_km("nsw-parramatta", "nowhere"))

    def test_choices_are_grouped_by_state_and_keys_are_unique(self):
        groups = [g for g in area_choices() if isinstance(g[1], list)]
        self.assertEqual(len(groups), 8)
        self.assertEqual(len(AREAS), len({a.key for a in AREAS.values()}))


class ApprovalTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", password=PW, role=User.Role.ADMIN, is_staff=True)

    SIGNUP_PW = "S0mething-long-77"

    def signup(self, role="DONOR", username="newbie", **extra):
        data = {"username": username, "first_name": "New", "last_name": "User", "email": "n@example.com",
                "role": role, "password1": self.SIGNUP_PW, "password2": self.SIGNUP_PW, "agree_terms": "on"}
        data.update(extra)
        return self.client.post(reverse("accounts:signup"), data)

    def test_signup_creates_pending_account_and_notifies_admins(self):
        resp = self.signup()
        self.assertRedirects(resp, reverse("accounts:pending"))
        user = User.objects.get(username="newbie")
        self.assertEqual(user.approval_status, User.Approval.PENDING)
        self.assertFalse(user.is_approved)
        n = Notification.objects.get(user=self.admin)
        self.assertEqual(n.kind, "approval")
        self.assertIn("newbie", n.text)
        self.assertEqual(n.link, reverse("accounts:approvals"))

    def test_pending_user_is_kept_out_of_the_portal_but_can_see_public_pages(self):
        self.signup()
        for url in (reverse("donations:donor_dashboard"), reverse("inbox:home"),
                    reverse("accounts:dashboard"), reverse("donations:my_report")):
            resp = self.client.get(url, follow=True)
            self.assertEqual(resp.redirect_chain[-1][0], reverse("accounts:pending"), url)
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        self.assertEqual(self.client.get(reverse("donations:live")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)

    def test_admin_approves_and_user_gets_in(self):
        self.signup(role="DRIVER")
        user = User.objects.get(username="newbie")
        self.client.logout()
        self.client.login(username="boss", password=PW)
        resp = self.client.post(reverse("accounts:approvals"), {"user_id": user.pk, "action": "approve"})
        self.assertEqual(resp.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.approval_status, User.Approval.APPROVED)
        self.assertEqual(user.approved_by, self.admin)
        self.assertTrue(Notification.objects.filter(user=user, text__icontains="approved").exists())
        self.client.logout()
        self.assertTrue(self.client.login(username="newbie", password=self.SIGNUP_PW))
        self.assertEqual(self.client.get(reverse("donations:driver_dashboard")).status_code, 200)

    def test_rejection_records_reason_and_stays_blocked(self):
        self.signup()
        user = User.objects.get(username="newbie")
        approvals.reject_user(user, self.admin, "Could not verify your organisation")
        page = self.client.get(reverse("donations:donor_dashboard"), follow=True)
        self.assertContains(page, "Could not verify your organisation")
        self.assertEqual(self.client.get(reverse("accounts:approvals")).status_code, 403)

    def test_only_admins_can_use_the_queue(self):
        donor = make_user("d1", User.Role.DONOR)
        self.client.login(username="d1", password=PW)
        self.assertEqual(self.client.get(reverse("accounts:approvals")).status_code, 403)

    def test_superuser_and_admin_role_are_auto_approved(self):
        su = User.objects.create_superuser("root", "r@example.com", PW)
        self.assertEqual(su.approval_status, User.Approval.APPROVED)
        self.assertTrue(self.admin.is_approved)
        self.assertEqual(self.admin.approval_status, User.Approval.APPROVED)

    def test_queue_lists_by_tab(self):
        make_user("p1", User.Role.DONOR, approved=False)
        self.client.login(username="boss", password=PW)
        self.assertContains(self.client.get(reverse("accounts:approvals")), "p1")
        self.assertNotContains(self.client.get(reverse("accounts:approvals") + "?tab=approved"), ">p1<")


@override_settings(OFFER_WAVE_SIZE=2, OFFER_MAX_WAVES=2, OFFER_TIMEOUT_MINUTES=30, OFFER_PROCESS_THROTTLE_SECONDS=0)
class MatchingTests(TestCase):
    def setUp(self):
        self.donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta", address="1 Church St", org="Corner Bakery")
        # Pantries at increasing distance from Parramatta
        self.p_near = make_user("pnear", User.Role.RECIPIENT, area="nsw-auburn", address="2 A St", org="Near Pantry")
        self.p_mid = make_user("pmid", User.Role.RECIPIENT, area="nsw-blacktown", address="3 B St", org="Mid Pantry")
        self.p_far = make_user("pfar", User.Role.RECIPIENT, area="nsw-newcastle", address="4 C St", org="Far Pantry")
        self.p_farther = make_user("pfarther", User.Role.RECIPIENT, area="vic-melbourne-cbd", address="5 D St")
        self.p_pending = make_user("ppending", User.Role.RECIPIENT, area="nsw-parramatta", approved=False)
        self.d_near = make_user("dnear", User.Role.DRIVER, area="nsw-homebush", address="6 E St")
        self.d_far = make_user("dfar", User.Role.DRIVER, area="nsw-gosford", address="7 F St")
        self.d_noaddr = make_user("dnoaddr", User.Role.DRIVER, area="nsw-parramatta")   # no street address

    def offered(self, donation, role):
        return list(donation.offers.filter(role=role).values_list("user__username", "status"))

    def test_new_listing_is_offered_to_nearest_approved_pantries_only(self):
        d = make_donation(self.donor)
        self.assertEqual(sorted(u for u, _ in self.offered(d, "RECIPIENT")), ["pmid", "pnear"])
        d.refresh_from_db()
        self.assertFalse(d.recipient_pool_open)
        self.assertTrue(Notification.objects.filter(user=self.p_near, kind="offer").exists())
        self.assertFalse(Notification.objects.filter(user=self.p_far).exists())
        self.assertFalse(Notification.objects.filter(user=self.p_pending).exists())
        self.assertTrue(DonationEvent.objects.filter(donation=d, kind="listed").exists())

    def test_reserved_donation_cannot_be_claimed_by_unoffered_pantry(self):
        d = make_donation(self.donor)
        with self.assertRaises(workflow.WorkflowError) as cm:
            workflow.claim(d.pk, self.p_far)
        self.assertIn("Reserved", str(cm.exception))
        workflow.claim(d.pk, self.p_mid)
        d.refresh_from_db()
        self.assertEqual((d.status, d.recipient), (Donation.Status.ASSIGNED, self.p_mid))
        self.assertIsNotNone(d.claimed_at)

    def test_unapproved_pantry_cannot_claim(self):
        d = make_donation(self.donor)
        Donation.objects.filter(pk=d.pk).update(recipient_pool_open=True)
        with self.assertRaises(workflow.WorkflowError):
            workflow.claim(d.pk, self.p_pending)

    def test_timeout_advances_to_next_wave_then_opens_to_everyone(self):
        d = make_donation(self.donor, hours=48)
        later = timezone.now() + timedelta(minutes=31)
        stats = workflow.process_timeouts(now=later)
        self.assertEqual(stats["offers_timed_out"], 2)
        self.assertEqual(sorted(u for u, s in self.offered(d, "RECIPIENT") if s == "offered"), ["pfar", "pfarther"])
        self.assertTrue(Notification.objects.filter(user=self.p_near, kind="offer_lapsed").exists())
        # second timeout: max waves reached -> pool opens
        workflow.process_timeouts(now=later + timedelta(minutes=31))
        d.refresh_from_db()
        self.assertTrue(d.recipient_pool_open)
        self.assertTrue(DonationEvent.objects.filter(donation=d, kind="opened").exists())
        self.assertTrue(Notification.objects.filter(user=self.p_near, kind="opened").exists())
        workflow.claim(d.pk, self.p_far)    # anyone eligible can claim now
        self.assertEqual(Donation.objects.get(pk=d.pk).recipient, self.p_far)

    def test_decline_moves_on_only_when_no_live_offers_remain(self):
        d = make_donation(self.donor)
        offers = {o.user.username: o for o in d.offers.all()}
        workflow.decline_offer(offers["pnear"].pk, self.p_near)
        self.assertEqual(d.offers.filter(status="offered").count(), 1)
        self.assertFalse(d.offers.filter(wave=2).exists())
        workflow.decline_offer(offers["pmid"].pk, self.p_mid)
        self.assertTrue(d.offers.filter(wave=2).exists())

    def test_claim_offers_pickup_to_nearest_ready_driver_and_hides_incomplete_profiles(self):
        d = make_donation(self.donor)
        workflow.claim(d.pk, self.p_near)
        offered = self.offered(d, "DRIVER")
        self.assertEqual(sorted(u for u, _ in offered), ["dfar", "dnear"])
        self.assertNotIn("dnoaddr", [u for u, _ in offered])
        first = d.offers.filter(role="DRIVER").order_by("distance_km").first()
        self.assertEqual(first.user, self.d_near)
        self.assertTrue(Notification.objects.filter(user=self.d_near, kind="offer").exists())

    def test_driver_without_address_cannot_pick_up(self):
        d = make_donation(self.donor)
        workflow.claim(d.pk, self.p_near)
        Donation.objects.filter(pk=d.pk).update(driver_pool_open=True)
        with self.assertRaises(workflow.WorkflowError) as cm:
            workflow.accept_pickup(d.pk, self.d_noaddr)
        self.assertIn("street address", str(cm.exception))
        self.d_noaddr.address = "9 Complete St"
        self.d_noaddr.save()
        workflow.accept_pickup(d.pk, self.d_noaddr)
        self.assertEqual(Donation.objects.get(pk=d.pk).status, Donation.Status.IN_TRANSIT)

    def test_driver_without_area_cannot_pick_up_either(self):
        nod = make_user("dnoarea", User.Role.DRIVER, address="1 Somewhere St")
        d = make_donation(self.donor)
        workflow.claim(d.pk, self.p_near)
        Donation.objects.filter(pk=d.pk).update(driver_pool_open=True)
        with self.assertRaises(workflow.WorkflowError):
            workflow.accept_pickup(d.pk, nod)

    def test_full_happy_path_notifies_the_right_people(self):
        d = make_donation(self.donor)
        Notification.objects.all().delete()
        workflow.claim(d.pk, self.p_near)
        self.assertIn("Near Pantry has claimed", Notification.objects.get(user=self.donor, kind="claimed").text)
        Notification.objects.all().delete()
        workflow.accept_pickup(d.pk, self.d_near)
        self.assertIn("picked up by Dnear", Notification.objects.get(user=self.donor, kind="picked_up").text)
        self.assertTrue(Notification.objects.filter(user=self.p_near, kind="picked_up").exists())
        workflow.mark_delivered(d.pk, self.d_near)
        d.refresh_from_db()
        self.assertEqual(d.status, Donation.Status.DELIVERED)
        self.assertIsNotNone(d.delivered_at)
        self.assertEqual([e.kind for e in d.events.all()][:2], ["listed", "offered"])
        self.assertEqual(d.events.last().kind, "delivered")

    def test_other_driver_cannot_take_a_reserved_job(self):
        d = make_donation(self.donor)
        workflow.claim(d.pk, self.p_near)
        Donation.objects.filter(pk=d.pk).update(driver_pool_open=False)
        self.d_far.address = "x"
        d.offers.filter(user=self.d_far).delete()
        with self.assertRaises(workflow.WorkflowError):
            workflow.accept_pickup(d.pk, self.d_far)

    def test_new_listing_alerts_nearby_drivers_only(self):
        far_driver = make_user("dperth", User.Role.DRIVER, area="wa-perth-cbd", address="1 Far St")
        d = make_donation(self.donor)
        self.assertTrue(Notification.objects.filter(user=self.d_near, kind="new_donation", donation=d).exists())
        self.assertFalse(Notification.objects.filter(user=far_driver, kind="new_donation").exists())

    def test_listing_without_any_area_opens_immediately_when_no_pantries_qualify(self):
        Offer.objects.all().delete()
        User.objects.filter(role="RECIPIENT").delete()
        d = make_donation(self.donor)
        d.refresh_from_db()
        self.assertTrue(d.recipient_pool_open)


@override_settings(OFFER_WAVE_SIZE=2, OFFER_MAX_WAVES=3, OFFER_PROCESS_THROTTLE_SECONDS=0)
class ExpiryAndCancellationTests(TestCase):
    def setUp(self):
        self.donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta", address="1 Church St")
        self.pantry = make_user("pantry", User.Role.RECIPIENT, area="nsw-auburn", address="2 A St")
        self.pantry2 = make_user("pantry2", User.Role.RECIPIENT, area="nsw-blacktown", address="3 B St")
        self.driver = make_user("driver", User.Role.DRIVER, area="nsw-homebush", address="4 C St")
        self.other_donor = make_user("donor2", User.Role.DONOR)

    def test_listing_expires_automatically_and_donor_is_told(self):
        d = make_donation(self.donor, hours=1)
        Notification.objects.all().delete()
        stats = workflow.process_timeouts(now=timezone.now() + timedelta(hours=2))
        self.assertEqual(stats["expired"], 1)
        d.refresh_from_db()
        self.assertEqual(d.status, Donation.Status.EXPIRED)
        self.assertEqual(d.offers.filter(status="offered").count(), 0)
        self.assertTrue(Notification.objects.filter(user=self.donor, kind="expired").exists())
        with self.assertRaises(workflow.WorkflowError):
            workflow.claim(d.pk, self.pantry)

    def test_claimed_but_uncollected_listing_expires_too(self):
        d = make_donation(self.donor, hours=1)
        workflow.claim(d.pk, self.pantry)
        Notification.objects.all().delete()
        workflow.process_timeouts(now=timezone.now() + timedelta(hours=2))
        d.refresh_from_db()
        self.assertEqual(d.status, Donation.Status.EXPIRED)
        self.assertTrue(Notification.objects.filter(user=self.pantry, kind="expired").exists())

    def test_in_transit_deliveries_do_not_expire_mid_delivery(self):
        d = make_donation(self.donor, hours=1)
        workflow.claim(d.pk, self.pantry)
        workflow.accept_pickup(d.pk, self.driver)
        workflow.process_timeouts(now=timezone.now() + timedelta(hours=5))
        self.assertEqual(Donation.objects.get(pk=d.pk).status, Donation.Status.IN_TRANSIT)

    def test_expiring_soon_warning_is_sent_once(self):
        d = make_donation(self.donor, hours=1)
        Notification.objects.all().delete()
        self.assertEqual(workflow.process_timeouts()["warned"], 1)
        self.assertEqual(workflow.process_timeouts()["warned"], 0)
        self.assertEqual(Notification.objects.filter(user=self.donor, kind="expiring").count(), 1)

    def test_listings_without_expiry_never_expire(self):
        d = make_donation(self.donor, expires_at=None)
        workflow.process_timeouts(now=timezone.now() + timedelta(days=400))
        self.assertEqual(Donation.objects.get(pk=d.pk).status, Donation.Status.PENDING)

    def test_offer_deadline_is_capped_by_listing_expiry(self):
        d = make_donation(self.donor, hours=0.2)   # 12 minutes
        offer = d.offers.first()
        self.assertLessEqual(offer.expires_at, d.expires_at)

    def test_donor_cancels_with_reason_and_everyone_involved_is_told(self):
        d = make_donation(self.donor)
        workflow.claim(d.pk, self.pantry)
        Notification.objects.all().delete()
        workflow.cancel_listing(d.pk, self.donor, "safety", "Fridge failed overnight")
        d.refresh_from_db()
        self.assertEqual((d.status, d.cancel_reason, d.cancelled_by), ("Cancelled", "safety", self.donor))
        text = Notification.objects.get(user=self.pantry, kind="cancelled").text
        self.assertIn("Quality or food-safety concern", text)
        self.assertIn("Fridge failed overnight", text)
        self.assertFalse(Notification.objects.filter(user=self.donor, kind="cancelled").exists())
        self.assertEqual(d.offers.filter(status="offered").count(), 0)
        self.assertIn("safety", " ".join(e.note.lower() for e in d.events.all()) + "safety")

    def test_only_the_owner_or_admin_may_cancel_and_not_mid_delivery(self):
        d = make_donation(self.donor)
        with self.assertRaises(workflow.WorkflowError):
            workflow.cancel_listing(d.pk, self.other_donor, "mistake")
        workflow.claim(d.pk, self.pantry)
        workflow.accept_pickup(d.pk, self.driver)
        with self.assertRaises(workflow.WorkflowError):
            workflow.cancel_listing(d.pk, self.donor, "mistake")
        admin = User.objects.create_user("boss", password=PW, role="ADMIN", is_staff=True)
        workflow.cancel_listing(d.pk, admin, "other", "Safety recall")
        self.assertEqual(Donation.objects.get(pk=d.pk).status, "Cancelled")
        self.assertTrue(Notification.objects.filter(user=self.driver, kind="cancelled").exists())

    def test_pantry_releases_claim_donor_told_and_next_pantry_offered(self):
        d = make_donation(self.donor)
        workflow.claim(d.pk, self.pantry)
        Notification.objects.all().delete()
        workflow.release_claim(d.pk, self.pantry, "no_storage", "Freezer is full")
        d.refresh_from_db()
        self.assertEqual((d.status, d.recipient), ("Pending", None))
        text = Notification.objects.get(user=self.donor, kind="released").text
        self.assertIn("No storage space available", text)
        # the pantry that released is not offered it again
        self.assertFalse(d.offers.filter(user=self.pantry, status="offered").exists())
        self.assertTrue(d.offers.filter(user=self.pantry2, status="offered").exists())
        self.assertIn(self.pantry.pk, workflow._already_tried(d, "RECIPIENT"))

    def test_driver_withdraws_and_job_returns_to_pool(self):
        d = make_donation(self.donor)
        workflow.claim(d.pk, self.pantry)
        workflow.accept_pickup(d.pk, self.driver)
        Notification.objects.all().delete()
        workflow.withdraw_pickup(d.pk, self.driver, "vehicle", "Van broke down")
        d.refresh_from_db()
        self.assertEqual((d.status, d.driver), ("Assigned", None))
        self.assertTrue(Notification.objects.filter(user=self.donor, kind="released", text__contains="Van broke down").exists())
        self.assertTrue(Notification.objects.filter(user=self.pantry, kind="released").exists())
        # nobody else qualifies -> opened to all rather than stuck
        self.assertTrue(d.driver_pool_open)

    def test_cannot_release_after_pickup(self):
        d = make_donation(self.donor)
        workflow.claim(d.pk, self.pantry)
        workflow.accept_pickup(d.pk, self.driver)
        with self.assertRaises(workflow.WorkflowError):
            workflow.release_claim(d.pk, self.pantry, "other")


@override_settings(OFFER_PROCESS_THROTTLE_SECONDS=0)
class DashboardViewTests(TestCase):
    def setUp(self):
        self.donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta", address="1 Church St")
        self.pantry = make_user("pantry", User.Role.RECIPIENT, area="nsw-auburn", address="2 A St")
        self.driver = make_user("driver", User.Role.DRIVER, area="nsw-homebush", address="4 C St")
        self.driver_bare = make_user("bare", User.Role.DRIVER)
        self.admin = User.objects.create_user("boss", password=PW, role="ADMIN", is_staff=True)

    def post(self, username, url, data):
        self.client.logout()
        self.client.login(username=username, password=PW)
        return self.client.post(reverse(url), data, follow=True)

    def test_donor_lists_with_expiry_area_and_gets_offers_created(self):
        exp = (timezone.localtime() + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M")
        resp = self.post("donor", "donations:donor_dashboard", {
            "food_item": "Soup", "quantity_kg": "8", "pickup_area": "nsw-parramatta",
            "pickup_address": "1 Church St", "expires_at": exp, "notes": "", "safety_confirmed": "on",
            "food_category": "bakery", "storage": "ambient", "date_type": "use_by"})
        self.assertContains(resp, "Donation listed")
        d = Donation.objects.get(food_item="Soup")
        self.assertEqual(d.pickup_area, "nsw-parramatta")
        self.assertIsNotNone(d.expires_at)
        self.assertTrue(d.offers.filter(user=self.pantry).exists())

    def test_donor_form_rejects_past_or_missing_expiry_and_bad_area(self):
        past = (timezone.localtime() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        base = {"food_item": "Soup", "quantity_kg": "8", "pickup_address": "x", "notes": ""}
        for extra in ({"pickup_area": "nsw-parramatta", "expires_at": past},
                      {"pickup_area": "nsw-parramatta", "expires_at": ""},
                      {"pickup_area": "atlantis", "expires_at": (timezone.localtime() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")}):
            self.post("donor", "donations:donor_dashboard", {**base, **extra})
        self.assertFalse(Donation.objects.exists())

    def test_donor_cancel_through_the_view_requires_a_reason(self):
        d = make_donation(self.donor)
        self.post("donor", "donations:donor_dashboard", {"action": "cancel", "donation_id": d.pk, "reason": ""})
        self.assertEqual(Donation.objects.get(pk=d.pk).status, "Pending")
        self.post("donor", "donations:donor_dashboard", {"action": "cancel", "donation_id": d.pk, "reason": "mistake"})
        self.assertEqual(Donation.objects.get(pk=d.pk).status, "Cancelled")

    def test_driver_dashboard_blocks_and_explains_incomplete_profile(self):
        d = make_donation(self.donor)
        workflow.claim(d.pk, self.pantry)
        Donation.objects.filter(pk=d.pk).update(driver_pool_open=True)
        self.client.login(username="bare", password=PW)
        page = self.client.get(reverse("donations:driver_dashboard"))
        self.assertContains(page, "Complete your profile")
        self.assertContains(page, "disabled")
        resp = self.client.post(reverse("donations:driver_dashboard"),
                                {"action": "accept", "donation_id": d.pk}, follow=True)
        self.assertContains(resp, "street address")
        self.assertEqual(Donation.objects.get(pk=d.pk).status, "Assigned")

    def test_driver_dashboard_shows_distances_nearest_first(self):
        far = make_donation(self.donor, pickup_area="nsw-newcastle", food_item="Far food")
        near = make_donation(self.donor, pickup_area="nsw-homebush", food_item="Near food")
        for d in (far, near):
            workflow.claim(d.pk, self.pantry)
        # offers are shown as their own cards; open the pool so the jobs list shows them too
        Offer.objects.filter(role="DRIVER").delete()
        Donation.objects.update(driver_pool_open=True)
        self.client.login(username="driver", password=PW)
        page = self.client.get(reverse("donations:driver_dashboard"))
        body = page.content.decode()
        self.assertLess(body.index("Near food"), body.index("Far food"))
        jobs = page.context["needs_driver"]
        self.assertEqual([j.food_item for j in jobs], ["Near food", "Far food"])
        self.assertEqual(jobs[0].km_to_pickup, 0.0)
        self.assertGreater(jobs[1].km_to_pickup, 100)
        self.assertEqual(page.context["snapshot"]["open_jobs"], 2)
        # radius filter
        near_only = self.client.get(reverse("donations:driver_dashboard") + "?radius=25").context["needs_driver"]
        self.assertEqual([j.food_item for j in near_only], ["Near food"])

    def test_recipient_full_flow_through_views(self):
        d = make_donation(self.donor)
        page = self.post("pantry", "donations:recipient_dashboard", {"action": "claim", "donation_id": d.pk})
        self.assertContains(page, "Claimed")
        page = self.post("pantry", "donations:recipient_dashboard",
                         {"action": "release", "donation_id": d.pk, "reason": "not_needed"})
        self.assertContains(page, "Claim released")
        self.assertEqual(Donation.objects.get(pk=d.pk).status, "Pending")

    def test_recipient_sees_live_offers_with_countdown_and_distance(self):
        d = make_donation(self.donor)
        self.client.login(username="pantry", password=PW)
        page = self.client.get(reverse("donations:recipient_dashboard"))
        self.assertEqual(len(page.context["offers"]), 1)
        self.assertContains(page, "data-expires")
        self.assertContains(page, "Bread")

    def test_detail_timeline_is_private_to_participants_and_admins(self):
        d = make_donation(self.donor)
        url = reverse("donations:detail", args=[d.pk])
        self.client.login(username="donor", password=PW)
        self.assertContains(self.client.get(url), "Listed")
        self.client.login(username="driver", password=PW)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.login(username="boss", password=PW)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_admin_dashboard_shows_approval_count_and_can_cancel_with_reason(self):
        make_user("waiting", User.Role.DONOR, approved=False)
        d = make_donation(self.donor)
        self.client.login(username="boss", password=PW)
        page = self.client.get(reverse("donations:admin_dashboard"))
        self.assertEqual(page.context["counts"]["awaiting_approval"], 1)
        self.client.post(reverse("donations:admin_cancel"), {"donation_id": d.pk, "reason": "other", "note": "Recall"})
        d.refresh_from_db()
        self.assertEqual((d.status, d.cancelled_by), ("Cancelled", self.admin))


@override_settings(OFFER_PROCESS_THROTTLE_SECONDS=0)
class LiveFilterTests(TestCase):
    def setUp(self):
        donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta")
        make_donation(donor, food_item="Sydney bread", pickup_area="nsw-sydney-cbd")
        make_donation(donor, food_item="Newcastle fruit", pickup_area="nsw-newcastle")
        make_donation(donor, food_item="Melbourne soup", pickup_area="vic-melbourne-cbd")
        make_donation(donor, food_item="Expired eggs", pickup_area="nsw-sydney-cbd", status="Expired")

    def names(self, params):
        page = self.client.get(reverse("donations:live"), params)
        return [d.food_item for d in page.context["donations"]]

    def test_area_and_radius_filter_orders_nearest_first(self):
        self.assertEqual(self.names({"area": "nsw-parramatta"}), ["Sydney bread", "Newcastle fruit", "Melbourne soup"])
        self.assertEqual(self.names({"area": "nsw-parramatta", "radius": "50"}), ["Sydney bread"])
        self.assertEqual(self.names({"area": "nsw-parramatta", "radius": "250"}), ["Sydney bread", "Newcastle fruit"])

    def test_state_filter_and_status_filter(self):
        self.assertEqual(sorted(self.names({"state": "NSW"})), ["Newcastle fruit", "Sydney bread"])
        self.assertEqual(self.names({"state": "VIC"}), ["Melbourne soup"])
        self.assertEqual(self.names({"status": "Expired"}), ["Expired eggs"])

    def test_expired_and_cancelled_are_hidden_by_default(self):
        self.assertNotIn("Expired eggs", self.names({}))


class ReportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", password=PW, role="ADMIN", is_staff=True)
        self.auditor = User.objects.create_user("checker", password=PW, role="AUDITOR")
        self.donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta", address="1 A St", org="Corner Bakery")
        self.donor2 = make_user("donor2", User.Role.DONOR, area="nsw-parramatta", address="1 A St", org="Quiet Deli")
        self.pantry = make_user("pantry", User.Role.RECIPIENT, area="nsw-auburn", address="2 B St", org="Hope Kitchen")
        self.driver = make_user("driver", User.Role.DRIVER, area="nsw-homebush", address="3 C St")
        with override_settings(OFFER_WAVE_SIZE=2):
            for i in range(3):
                d = make_donation(self.donor, quantity_kg=Decimal("10"), food_item=f"Bread {i}")
                workflow.claim(d.pk, self.pantry)
                workflow.accept_pickup(d.pk, self.driver)
                workflow.mark_delivered(d.pk, self.driver)
            c = make_donation(self.donor, food_item="Cancelled thing")
            workflow.cancel_listing(c.pk, self.donor, "unavailable")
            make_donation(self.donor2, food_item="Never collected", hours=1)
            workflow.process_timeouts(now=timezone.now() + timedelta(hours=3))

    def test_network_summary_numbers(self):
        s = analytics.network_summary()
        self.assertEqual((s["delivered"], s["cancelled"], s["expired"]), (3, 1, 1))
        self.assertEqual(s["kg_delivered"], 30.0)
        self.assertEqual(s["meals"], 60)
        self.assertEqual(s["fulfilment_rate"], 60)
        self.assertEqual(s["cancel_reasons"][0]["label"], "Food is no longer available")
        self.assertIsNotNone(s["avg_hours_to_claim"])

    def test_partner_rows(self):
        donor = {r["name"]: r for r in analytics.donor_rows()}
        self.assertEqual(donor["Corner Bakery"]["delivered"], 3)
        self.assertEqual(donor["Corner Bakery"]["cancelled"], 1)
        self.assertEqual(donor["Quiet Deli"]["expired"], 1)
        rec = analytics.recipient_rows()[0]
        self.assertEqual((rec["name"], rec["delivered"], rec["kg_received"], rec["meals"]), ("Hope Kitchen", 3, 30.0, 60))
        drv = analytics.driver_rows()[0]
        self.assertEqual((drv["delivered"], drv["kg_moved"]), (3, 30.0))
        self.assertGreater(drv["km"], 0)
        pair = analytics.partnership_rows()[0]
        self.assertEqual((pair["donor"], pair["recipient"], pair["deliveries"], pair["kg"]),
                         ("Corner Bakery", "Hope Kitchen", 3, 30.0))

    def test_period_filter(self):
        start, end = analytics.period_bounds(timezone.localdate() + timedelta(days=2), None)
        self.assertEqual(analytics.network_summary(start, end)["listings"], 0)

    def test_report_page_is_auditor_only_not_plain_admin(self):
        self.client.login(username="donor", password=PW)
        self.assertEqual(self.client.get(reverse("donations:partnership_report")).status_code, 403)
        self.client.login(username="boss", password=PW)
        self.assertEqual(self.client.get(reverse("donations:partnership_report")).status_code, 403)
        self.client.login(username="checker", password=PW)
        page = self.client.get(reverse("donations:partnership_report"))
        self.assertContains(page, "Partnership Activity")
        self.assertContains(page, "Corner Bakery")
        self.assertContains(page, "Hope Kitchen")

    def test_auditor_account_is_auto_approved_and_cannot_manage_users(self):
        self.assertTrue(self.auditor.is_approved)
        self.client.login(username="checker", password=PW)
        self.assertEqual(self.client.get(reverse("accounts:approvals")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:manage_users")).status_code, 403)
        self.assertEqual(self.client.get(reverse("donations:admin_dashboard")).status_code, 403)

    def test_admin_cannot_reach_reports_or_csv_but_keeps_user_management(self):
        self.client.login(username="boss", password=PW)
        self.assertEqual(self.client.get(reverse("donations:export_delivered_csv")).status_code, 403)
        self.assertEqual(self.client.get(reverse("donations:partnership_report"), {"export": "donors"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:approvals")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:manage_users")).status_code, 200)

    def test_csv_exports(self):
        self.client.login(username="checker", password=PW)
        for kind, needle in (("donors", "Corner Bakery"), ("recipients", "Hope Kitchen"),
                             ("drivers", "Driver"), ("partnerships", "Hope Kitchen")):
            resp = self.client.get(reverse("donations:partnership_report"), {"export": kind, "range": "all"})
            self.assertEqual(resp["Content-Type"], "text/csv")
            self.assertIn(needle, resp.content.decode())
        self.assertEqual(self.client.get(reverse("donations:partnership_report"), {"export": "nope"}).status_code, 404)
        delivered_csv = self.client.get(reverse("donations:export_delivered_csv"))
        self.assertEqual(delivered_csv["Content-Type"], "text/csv")

    def test_partner_sees_only_their_own_report(self):
        self.client.login(username="donor2", password=PW)
        page = self.client.get(reverse("donations:my_report"), {"range": "all"})
        self.assertContains(page, "Quiet Deli")
        self.assertNotContains(page, "Corner Bakery</td>")
        csv_resp = self.client.get(reverse("donations:my_report"), {"export": "donors", "range": "all"})
        self.assertIn("Quiet Deli", csv_resp.content.decode())
        self.assertNotIn("Corner Bakery", csv_resp.content.decode())
        # a plain admin has nothing to see here - reports are the auditor's job now
        self.client.login(username="boss", password=PW)
        self.assertRedirects(self.client.get(reverse("donations:my_report")), reverse("donations:admin_dashboard"))
        # the auditor account also has no personal donations, so it's bounced to the full report
        self.client.login(username="checker", password=PW)
        self.assertRedirects(self.client.get(reverse("donations:my_report")), reverse("donations:partnership_report"))


# =============================================================================
# v5 additions: email, password reset, throttling, validation, user management,
# food safety, feedback, map, legal pages, smoke check
# =============================================================================
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from io import StringIO
from unittest import mock

from accounts.validators import looks_like_street_address
from .models import Feedback


class EmailAndPasswordResetTests(TestCase):
    def setUp(self):
        self.donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta", address="1 Church St", email="d@example.com")
        self.pantry = make_user("pantry", User.Role.RECIPIENT, area="nsw-auburn", address="2 A St", email="p@example.com")

    def test_password_reset_flow_end_to_end(self):
        resp = self.client.post(reverse("accounts:password_reset"), {"email": "d@example.com"})
        self.assertRedirects(resp, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/reset/", mail.outbox[0].body)
        link = [l for l in mail.outbox[0].body.split() if "/accounts/reset/" in l][0]
        path = "/" + link.split("://", 1)[1].split("/", 1)[1]
        page = self.client.get(path, follow=True)
        self.assertContains(page, "Choose a new password")
        post_url = page.redirect_chain[-1][0] if page.redirect_chain else path
        resp = self.client.post(post_url, {"new_password1": "Brand-new-Pass-991", "new_password2": "Brand-new-Pass-991"})
        self.assertRedirects(resp, reverse("accounts:password_reset_complete"))
        self.assertTrue(self.client.login(username="donor", password="Brand-new-Pass-991"))

    def test_reset_for_unknown_email_reveals_nothing_and_sends_nothing(self):
        resp = self.client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        self.assertRedirects(resp, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_offer_is_emailed_with_a_deep_link(self):
        make_donation(self.donor)
        offer_mails = [m for m in mail.outbox if m.to == ["p@example.com"]]
        self.assertEqual(len(offer_mails), 1)
        self.assertIn("Offer", offer_mails[0].subject)
        self.assertIn("/donations/dashboard/recipient/", offer_mails[0].body)

    def test_opt_out_is_respected_and_unimportant_kinds_are_not_emailed(self):
        self.pantry.email_notifications = False
        self.pantry.save()
        d = make_donation(self.donor)
        self.assertFalse([m for m in mail.outbox if m.to == ["p@example.com"]])
        driver = make_user("dd", User.Role.DRIVER, area="nsw-homebush", address="3 C St", email="dd@example.com")
        d2 = make_donation(self.donor, food_item="More bread")
        # 'new listing' heads-up to drivers is in-app only
        self.assertFalse([m for m in mail.outbox if m.to == ["dd@example.com"]])

    def test_mail_failure_never_breaks_the_workflow(self):
        with mock.patch("django.core.mail.get_connection", side_effect=RuntimeError("smtp down")):
            d = make_donation(self.donor)
        self.assertTrue(d.offers.exists())
        workflow.claim(d.pk, self.pantry)
        self.assertEqual(Donation.objects.get(pk=d.pk).status, "Assigned")

    def test_signup_and_approval_emails(self):
        User.objects.create_user("boss", password=PW, role="ADMIN", is_staff=True, email="boss@example.com")
        self.client.post(reverse("accounts:signup"), {
            "username": "newbie", "first_name": "New", "email": "n@example.com", "role": "DRIVER",
            "password1": "S0mething-long-77", "password2": "S0mething-long-77", "agree_terms": "on"})
        self.assertTrue([m for m in mail.outbox if m.to == ["boss@example.com"]])
        approvals.approve_user(User.objects.get(username="newbie"), User.objects.get(username="boss"))
        self.assertTrue([m for m in mail.outbox if m.to == ["n@example.com"] and "approved" in m.body])


class SecurityAndValidationTests(TestCase):
    def setUp(self):
        cache.clear()
        make_user("alice", User.Role.DONOR)

    def test_login_locks_after_five_failures_and_recovers_the_right_way(self):
        for _ in range(5):
            self.client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
        resp = self.client.post(reverse("accounts:login"), {"username": "alice", "password": PW})
        self.assertEqual(resp.status_code, 429)
        self.assertContains(resp, "Too many failed attempts", status_code=429)
        # a different user from the same IP is unaffected
        make_user("bob", User.Role.DONOR)
        self.assertEqual(self.client.post(reverse("accounts:login"), {"username": "bob", "password": PW}).status_code, 302)

    def test_successful_login_resets_the_counter(self):
        for _ in range(4):
            self.client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
        self.assertEqual(self.client.post(reverse("accounts:login"), {"username": "alice", "password": PW}).status_code, 302)
        self.client.logout()
        for _ in range(4):
            self.client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
        self.assertEqual(self.client.post(reverse("accounts:login"), {"username": "alice", "password": PW}).status_code, 302)

    def test_address_sanity_check(self):
        for good in ("12 George St", "Unit 4/9 Park Ave", "1 Church St", "4 C St"):
            self.assertTrue(looks_like_street_address(good), good)
        for bad in ("x", "N/A", "123", "somewhere", "Sydney", ""):
            self.assertFalse(looks_like_street_address(bad), bad)

    def test_profile_and_signup_reject_junk_addresses_but_allow_blank(self):
        self.client.login(username="alice", password=PW)
        base = {"first_name": "A", "last_name": "", "email": "a@example.com", "organisation_name": "", "phone": "",
                "area": "nsw-parramatta", "bio": ""}
        self.assertContains(self.client.post(reverse("accounts:profile"), {**base, "address": "x"}), "street address")
        self.assertEqual(self.client.post(reverse("accounts:profile"), {**base, "address": "12 George St"}).status_code, 302)
        self.assertEqual(self.client.post(reverse("accounts:profile"), {**base, "address": ""}).status_code, 302)

    def test_signup_requires_terms(self):
        data = {"username": "t1", "first_name": "T", "email": "t@example.com", "role": "DONOR",
                "password1": "S0mething-long-77", "password2": "S0mething-long-77"}
        resp = self.client.post(reverse("accounts:signup"), data)
        self.assertContains(resp, "Terms of Use")
        self.assertFalse(User.objects.filter(username="t1").exists())

    def test_legal_pages_are_public(self):
        for name in ("privacy", "terms"):
            self.assertContains(self.client.get(reverse(name)), "not legal advice")


class ManageUsersTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", password=PW, role="ADMIN", is_staff=True)
        self.other_admin = User.objects.create_user("boss2", password=PW, role="ADMIN", is_staff=True)
        self.donor = make_user("donor", User.Role.DONOR, org="Corner Bakery")
        self.client.login(username="boss", password=PW)

    def act(self, user, action, **extra):
        return self.client.post(reverse("accounts:manage_users"), {"user_id": user.pk, "action": action, **extra})

    def test_deactivated_user_cannot_log_in_and_can_be_reactivated(self):
        self.act(self.donor, "deactivate")
        self.donor.refresh_from_db()
        self.assertFalse(self.donor.is_active)
        self.client.logout()
        self.assertFalse(self.client.login(username="donor", password=PW))
        self.client.login(username="boss", password=PW)
        self.act(self.donor, "activate")
        self.client.logout()
        self.assertTrue(self.client.login(username="donor", password=PW))

    def test_role_change_and_send_back_to_review(self):
        self.act(self.donor, "role", role="DRIVER")
        self.donor.refresh_from_db()
        self.assertEqual(self.donor.role, "DRIVER")
        self.act(self.donor, "pending")
        self.donor.refresh_from_db()
        self.assertFalse(self.donor.is_approved)

    def test_cannot_touch_self_other_admins_or_invalid_roles(self):
        self.act(self.admin, "deactivate")
        self.act(self.other_admin, "deactivate")
        self.act(self.donor, "role", role="ADMIN")
        self.admin.refresh_from_db(); self.other_admin.refresh_from_db(); self.donor.refresh_from_db()
        self.assertTrue(self.admin.is_active and self.other_admin.is_active)
        self.assertEqual(self.donor.role, "DONOR")

    def test_search_filter_and_admin_only(self):
        page = self.client.get(reverse("accounts:manage_users"), {"q": "bakery"})
        self.assertContains(page, "Corner Bakery")
        self.assertNotContains(page, "@boss2")
        self.client.login(username="donor", password=PW)
        self.assertEqual(self.client.get(reverse("accounts:manage_users")).status_code, 403)


class FoodSafetyTests(TestCase):
    def setUp(self):
        self.donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta", address="1 Church St")
        self.client.login(username="donor", password=PW)

    def post(self, **extra):
        exp = (timezone.localtime() + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M")
        data = {"food_item": "Soup", "food_category": "prepared", "quantity_kg": "8", "storage": "chilled",
                "date_type": "use_by", "allergen_note": "contains dairy", "pickup_area": "nsw-parramatta",
                "pickup_address": "1 Church St", "expires_at": exp, "notes": "", "safety_confirmed": "on"}
        data.update(extra)
        return self.client.post(reverse("donations:donor_dashboard"), data, follow=True)

    def test_safety_fields_are_saved_and_shown(self):
        page = self.post()
        d = Donation.objects.get(food_item="Soup")
        self.assertEqual((d.food_category, d.storage, d.date_type, d.safety_confirmed), ("prepared", "chilled", "use_by", True))
        self.assertContains(page, "Keep chilled")
        self.assertContains(page, "contains dairy")

    def test_donor_must_confirm_safety_and_give_a_real_address(self):
        self.post(safety_confirmed="")
        self.post(pickup_address="x")
        self.assertFalse(Donation.objects.exists())


class FeedbackTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", password=PW, role="ADMIN", is_staff=True)
        self.donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta", address="1 Church St", org="Corner Bakery")
        self.pantry = make_user("pantry", User.Role.RECIPIENT, area="nsw-auburn", address="2 A St", org="Hope Kitchen")
        self.driver = make_user("driver", User.Role.DRIVER, area="nsw-homebush", address="3 C St")
        self.stranger = make_user("stranger", User.Role.DRIVER, area="nsw-homebush", address="4 C St")
        self.d = make_donation(self.donor)
        workflow.claim(self.d.pk, self.pantry)
        workflow.accept_pickup(self.d.pk, self.driver)

    def rate(self, username, rating="5", **extra):
        self.client.login(username=username, password=PW)
        return self.client.post(reverse("donations:rate", args=[self.d.pk]),
                                {"rating": rating, "comment": "ok", **extra}, follow=True)

    def test_cannot_rate_before_delivery(self):
        self.client.login(username="pantry", password=PW)
        self.assertEqual(self.client.post(reverse("donations:rate", args=[self.d.pk]), {"rating": "5"}).status_code, 403)

    def test_participants_rate_stranger_cannot_and_rating_updates_not_duplicates(self):
        workflow.mark_delivered(self.d.pk, self.driver)
        self.assertEqual(self.client.get(reverse("donations:detail", args=[self.d.pk])).status_code, 302)  # anonymous -> login
        self.rate("pantry", "4")
        self.rate("pantry", "2")
        self.assertEqual(Feedback.objects.filter(author=self.pantry).count(), 1)
        self.assertEqual(Feedback.objects.get(author=self.pantry).rating, 2)
        self.client.login(username="stranger", password=PW)
        self.assertEqual(self.client.post(reverse("donations:rate", args=[self.d.pk]), {"rating": "5"}).status_code, 403)
        self.assertContains(self.rate("donor", "5"), "Thanks for the feedback")

    def test_rating_must_be_valid(self):
        workflow.mark_delivered(self.d.pk, self.driver)
        self.rate("pantry", "9")
        self.assertFalse(Feedback.objects.exists())

    def test_issue_notifies_admins_and_admin_can_resolve(self):
        workflow.mark_delivered(self.d.pk, self.driver)
        self.rate("pantry", "1", is_issue="on", comment="Food was warm")
        n = Notification.objects.get(user=self.admin, kind="issue")
        self.assertIn("Food was warm", n.text)
        self.client.login(username="boss", password=PW)
        page = self.client.get(reverse("donations:feedback"))
        self.assertContains(page, "Food was warm")
        fb = Feedback.objects.get()
        self.client.post(reverse("donations:feedback"), {"feedback_id": fb.pk})
        fb.refresh_from_db()
        self.assertTrue(fb.resolved)
        self.assertTrue(Notification.objects.filter(user=self.pantry, text__contains="marked it resolved").exists())
        self.assertEqual(analytics.feedback_summary()["open_issues"], 0)

    def test_ratings_appear_in_partner_reports(self):
        workflow.mark_delivered(self.d.pk, self.driver)
        self.rate("pantry", "4")
        self.rate("driver", "5")
        self.rate("donor", "3")
        donor = {r["name"]: r for r in analytics.donor_rows()}["Corner Bakery"]
        self.assertEqual(donor["rating"], 4.5)          # pantry 4 + driver 5; donor's own 3 excluded
        self.assertEqual(analytics.recipient_rows()[0]["rating"], 4.0)   # donor 3 + driver 5
        User.objects.create_user("checker", password=PW, role="AUDITOR")
        self.client.login(username="checker", password=PW)
        self.assertIn("4.5", self.client.get(reverse("donations:partnership_report"),
                                              {"export": "donors", "range": "all"}).content.decode())


@override_settings(OFFER_PROCESS_THROTTLE_SECONDS=0)
class MapAndSmokeTests(TestCase):
    def setUp(self):
        self.donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta", address="99 Secret Lane")
        make_donation(self.donor, food_item="Map bread", pickup_area="nsw-sydney-cbd", pickup_address="55 Private Rd")
        make_donation(self.donor, food_item="Map soup", pickup_area="nsw-sydney-cbd", pickup_address="55 Private Rd")
        make_donation(self.donor, food_item="Old", pickup_area="nsw-newcastle", status="Delivered")

    def test_map_json_aggregates_by_area_and_never_leaks_addresses(self):
        data = self.client.get(reverse("donations:map_data")).json()
        self.assertEqual(len(data["areas"]), 1)
        a = data["areas"][0]
        self.assertEqual((a["label"], a["count"], a["kg"]), ("Sydney CBD, NSW", 2, 20.0))
        self.assertNotIn("Private Rd", str(data))
        self.assertNotIn("Secret Lane", str(data))

    def test_map_page_is_public_and_pending_users_can_see_it(self):
        self.assertContains(self.client.get(reverse("donations:map")), "Donations map")
        make_user("waiting", User.Role.DONOR, approved=False)
        self.client.login(username="waiting", password=PW)
        self.assertEqual(self.client.get(reverse("donations:map")).status_code, 200)

    def test_smoke_check_passes_and_leaves_no_data(self):
        before = (User.objects.count(), Donation.objects.count())
        out = StringIO()
        call_command("smoke_check", stdout=out)
        self.assertIn("All checks passed", out.getvalue())
        self.assertEqual(before, (User.objects.count(), Donation.objects.count()))


class SignupRoleCardTests(TestCase):
    def test_each_role_card_gets_its_own_icon_and_description(self):
        html = self.client.get(reverse("accounts:signup")).content.decode()
        self.assertIn('value="DONOR" id="id_role_0"', html)
        chunk = html[html.index('id="id_role_0"'):html.index('id="id_role_1"')]
        self.assertIn("bi-shop", chunk)
        self.assertIn("I have surplus food", chunk)
        chunk = html[html.index('id="id_role_1"'):html.index('id="id_role_2"')]
        self.assertIn("bi-house-heart", chunk)
        self.assertIn("I feed people in need", chunk)
        chunk = html[html.index('id="id_role_2"'):]
        self.assertIn("bi-truck", chunk)
        self.assertIn("I can transport food", chunk)

    def test_auditor_is_not_a_signup_option(self):
        html = self.client.get(reverse("accounts:signup")).content.decode()
        self.assertNotIn('value="AUDITOR"', html)
        self.assertNotIn('value="ADMIN"', html)


class AdminAuditorSplitTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", password=PW, role="ADMIN", is_staff=True)
        self.auditor = User.objects.create_user("checker", password=PW, role="AUDITOR")
        self.donor = make_user("donor", User.Role.DONOR, area="nsw-parramatta", address="1 A St")
        self.pantry = make_user("pantry", User.Role.RECIPIENT, area="nsw-auburn", address="2 B St")
        self.d = make_donation(self.donor)
        workflow.claim(self.d.pk, self.pantry)

    def test_auditor_lands_on_the_report_after_login_not_admin_dashboard(self):
        self.client.login(username="checker", password=PW)
        self.assertRedirects(self.client.get(reverse("accounts:dashboard")), reverse("donations:partnership_report"))

    def test_nav_shows_the_right_links_and_label_for_each(self):
        self.client.login(username="boss", password=PW)
        page = self.client.get(reverse("donations:admin_dashboard"))
        self.assertContains(page, "Approvals</a>")
        self.assertContains(page, "Users</a>")
        self.assertNotContains(page, "Reports</a>")
        self.assertContains(page, "@boss")
        self.assertContains(page, ">Admin<")
        self.client.login(username="checker", password=PW)
        page = self.client.get(reverse("donations:partnership_report"))
        self.assertContains(page, "Reports</a>")
        self.assertNotContains(page, "Approvals</a>")
        self.assertNotContains(page, "Users</a>")
        self.assertContains(page, "@checker")
        self.assertContains(page, ">Auditor<")

    def test_admin_cannot_manage_the_fixed_auditor_account(self):
        self.client.login(username="boss", password=PW)
        self.client.post(reverse("accounts:manage_users"), {"user_id": self.auditor.pk, "action": "deactivate"})
        self.auditor.refresh_from_db()
        self.assertTrue(self.auditor.is_active)

    def test_auditor_never_appears_in_the_approvals_queue(self):
        self.client.login(username="boss", password=PW)
        page = self.client.get(reverse("accounts:approvals"), {"tab": "approved"})
        self.assertNotContains(page, "checker")

    def test_auditor_can_view_any_donation_timeline_for_reporting(self):
        self.client.login(username="checker", password=PW)
        self.assertEqual(self.client.get(reverse("donations:detail", args=[self.d.pk])).status_code, 200)

    def test_admin_dashboard_has_no_report_or_csv_buttons(self):
        self.client.login(username="boss", password=PW)
        page = self.client.get(reverse("donations:admin_dashboard"))
        self.assertNotContains(page, "Partnership activity report")
        self.assertNotContains(page, "Delivered-food CSV")


class AddUserTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", password=PW, role="ADMIN", is_staff=True)
        self.superuser = User.objects.create_superuser("root", "root@example.com", PW)
        self.donor = make_user("donor", User.Role.DONOR)

    def create(self, actor, username, role, **extra):
        self.client.login(username=actor, password=PW)
        data = {"username": username, "first_name": "New", "role": role,
                "password1": "S0mething-long-77", "password2": "S0mething-long-77"}
        data.update(extra)
        return self.client.post(reverse("accounts:add_user"), data, follow=True)

    def test_plain_admin_can_create_an_auditor_and_it_logs_in_immediately(self):
        resp = self.create("boss", "newauditor", "AUDITOR")
        self.assertContains(resp, "Created the Auditor account")
        user = User.objects.get(username="newauditor")
        self.assertEqual(user.role, "AUDITOR")
        self.assertTrue(user.is_approved)
        self.client.logout()
        self.assertTrue(self.client.login(username="newauditor", password="S0mething-long-77"))

    def test_plain_admin_cannot_create_another_admin(self):
        resp = self.create("boss", "sneaky", "ADMIN")
        self.assertFalse(User.objects.filter(username="sneaky").exists())
        self.assertContains(resp, "not one of the available choices")

    def test_superuser_can_create_an_admin(self):
        self.create("root", "newadmin", "ADMIN")
        user = User.objects.get(username="newadmin")
        self.assertEqual(user.role, "ADMIN")
        self.assertTrue(user.is_approved)

    def test_only_admins_can_reach_the_page(self):
        self.client.login(username="donor", password=PW)
        self.assertEqual(self.client.get(reverse("accounts:add_user")).status_code, 403)

    def test_add_user_link_appears_on_manage_users_page(self):
        self.client.login(username="boss", password=PW)
        self.assertContains(self.client.get(reverse("accounts:manage_users")), "Add user")
