"""Donation workflow: matching offers, timeouts, expiry, claims and cancellations.

Everything that changes a donation's state goes through this module so the rules are
enforced in one place (views stay thin, and the rules are unit-tested).

Life of a listing
-----------------
1. A donor lists food with an expiry time.                       -> start_matching()
2. The nearest approved pantries get a time-limited *offer*; nearby drivers get a
   heads-up alert.  Only offered pantries can claim it while their offers are live.
3. If nobody accepts before the timeout, the next-nearest wave is offered.  After
   OFFER_MAX_WAVES (or when nobody is left) the listing opens to every eligible pantry.
4. When a pantry claims it, the same offer/timeout process runs for drivers (nearest
   first).  Only drivers with a complete profile (area + address) can take a pickup.
5. Listings that reach their expiry time are closed automatically (process_timeouts()).
"""
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from accounts.areas import area_label, distance_km
from inbox import services as notes
from inbox.models import Notification

from .models import Donation, DonationEvent, Offer

Status = Donation.Status
Kind = Notification.Kind

LIVE_TRACKING_TEXT = "Live Tracking will soon be possible and is under production!"


class WorkflowError(Exception):
    """A rule was broken; the message is safe to show to the user."""


def _cfg(name, default):
    return getattr(settings, name, default)


def _timeout():
    return timedelta(minutes=_cfg("OFFER_TIMEOUT_MINUTES", 30))


def _now():
    return timezone.now()


def _kg(donation):
    from decimal import Decimal
    return format(Decimal(str(donation.quantity_kg)).normalize(), "f")


def _what(donation):
    where = f", {donation.area_display}" if donation.area_display else ""
    return f"'{donation.food_item}' ({_kg(donation)} kg) from {donation.donor_name}{where}"


def log(donation, kind, actor=None, note=""):
    return DonationEvent.objects.create(donation=donation, actor=actor, kind=kind, note=note[:255])


def _role_users(role):
    User = get_user_model()
    return User.objects.filter(role=role, is_active=True, approval_status=User.Approval.APPROVED)


def _stage(role):
    """(pool flag on Donation, dashboard link) for a role."""
    if role == Offer.Role.RECIPIENT:
        return "recipient_pool_open", reverse("donations:recipient_dashboard")
    return "driver_pool_open", reverse("donations:driver_dashboard")


def _ranked(donation, role, exclude):
    """Eligible users for a role, nearest to the pickup first (unknown distance last)."""
    users = _role_users(role).exclude(pk__in=exclude)
    scored = []
    for u in users:
        if role == Offer.Role.DRIVER and not u.can_pick_up:
            continue
        d = distance_km(u.area, donation.effective_area)
        scored.append((d if d is not None else 1e9, u.pk, u, d))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [(u, d) for _, _, u, d in scored]


def _refused(donation, role):
    """Users who explicitly said no (declined / released / withdrew) - never re-offer or re-alert them."""
    ids = set(donation.offers.filter(role=role, status=Offer.Status.DECLINED).values_list("user_id", flat=True))
    kind = DonationEvent.Kind.RELEASED if role == Offer.Role.RECIPIENT else DonationEvent.Kind.WITHDRAWN
    ids |= set(donation.events.filter(kind=kind).exclude(actor__isnull=True).values_list("actor_id", flat=True))
    return ids


def _already_tried(donation, role):
    """Users who already had their turn: they responded, timed out, or have a live/accepted offer.

    An offer that was merely *withdrawn* (someone else got there first) doesn't count -
    that user never had the chance to refuse it.
    """
    ids = set(donation.offers.filter(role=role).exclude(status=Offer.Status.WITHDRAWN)
              .values_list("user_id", flat=True))
    return ids | _refused(donation, role)


def _stage_is_waiting(donation, role):
    if role == Offer.Role.RECIPIENT:
        return donation.status == Status.PENDING and donation.recipient_id is None
    return donation.status == Status.ASSIGNED and donation.driver_id is None


# --------------------------------------------------------------------------- matching
def _open_wave(donation, role):
    """Offer to the next-nearest group; open the pool to everyone once we run out."""
    flag, link = _stage(role)
    tried = _already_tried(donation, role)
    wave_no = (donation.offers.filter(role=role).aggregate(m=Max("wave"))["m"] or 0) + 1
    candidates = _ranked(donation, role, tried)

    if not candidates or wave_no > _cfg("OFFER_MAX_WAVES", 3):
        if not getattr(donation, flag):
            setattr(donation, flag, True)
            donation.save(update_fields=[flag, "date_updated"])
            log(donation, DonationEvent.Kind.OPENED, note=f"Open to all eligible {role.lower()}s")
        refused = _refused(donation, role)
        if role == Offer.Role.RECIPIENT:
            targets = list(_role_users(role).exclude(pk__in=refused))
            text = f"Now open to all pantries: {_what(donation)}. First to claim it gets it."
        else:
            targets = [u for u in _role_users(role).exclude(pk__in=refused) if u.can_pick_up]
            text = f"Ready for pickup, open to all drivers: {_what(donation)}."
        if targets:
            notes.notify(targets, Kind.OPENED, text, donation, link)
        return []

    chosen = candidates[:_cfg("OFFER_WAVE_SIZE", 2)]
    expires = _now() + _timeout()
    if donation.expires_at and donation.expires_at < expires:
        expires = donation.expires_at
    setattr(donation, flag, False)
    donation.save(update_fields=[flag, "date_updated"])
    offers = Offer.objects.bulk_create([
        Offer(donation=donation, user=u, role=role, wave=wave_no, distance_km=d, expires_at=expires)
        for u, d in chosen
    ])
    who = ", ".join(u.display_name for u, _ in chosen)
    log(donation, DonationEvent.Kind.OFFERED, note=f"Wave {wave_no}: offered to {who}")
    minutes = int(_timeout().total_seconds() // 60)
    verb = "claim" if role == Offer.Role.RECIPIENT else "collect"
    for (u, d), _offer in zip(chosen, offers):
        away = f" ({d:g} km away)" if d is not None else ""
        notes.notify(
            [u], Kind.OFFER,
            f"Offer for you: {_what(donation)}{away}. Accept within {minutes} min to {verb} it.",
            donation, link,
        )
    return offers


def start_matching(donation):
    """Called when a donation is created: offer to nearby pantries + alert nearby drivers."""
    if notes.is_suppressed() or donation.status != Status.PENDING:
        return
    log(donation, DonationEvent.Kind.LISTED, donation.donor,
        note="Listed" + (f" · expires {timezone.localtime(donation.expires_at):%d %b %H:%M}" if donation.expires_at else ""))
    _open_wave(donation, Offer.Role.RECIPIENT)
    _alert_nearby_drivers(donation)
    notes.notify([donation.donor], Kind.LIVE_TRACKING, LIVE_TRACKING_TEXT,
                 donation, reverse("donations:donor_dashboard"))


def _alert_nearby_drivers(donation):
    radius = _cfg("ALERT_RADIUS_KM", 50)
    targets = []
    for u in _role_users(Offer.Role.DRIVER):
        d = distance_km(u.area, donation.effective_area)
        if d is None or d <= radius:
            targets.append(u)
    if targets:
        notes.notify(
            targets, Kind.NEW_DONATION,
            f"New listing near you: {_what(donation)}. You'll get a pickup offer once a pantry claims it.",
            donation, reverse("donations:driver_dashboard"),
        )


# --------------------------------------------------------------------------- timeouts
def _expire(donation, now):
    donation.offers.filter(status=Offer.Status.OFFERED).update(
        status=Offer.Status.WITHDRAWN, responded_at=now)
    donation.status = Status.EXPIRED
    donation.save()   # signal -> notifications
    log(donation, DonationEvent.Kind.EXPIRED, note="Reached its expiry time before delivery")


def process_timeouts(now=None):
    """Sweep: expire old listings, time out stale offers (advancing waves), send expiry warnings."""
    if notes.is_suppressed():
        return {"expired": 0, "offers_timed_out": 0, "warned": 0}
    now = now or _now()
    stats = {"expired": 0, "offers_timed_out": 0, "warned": 0}
    with transaction.atomic():
        for d in Donation.objects.select_for_update().filter(
                status__in=[Status.PENDING, Status.ASSIGNED],
                expires_at__isnull=False, expires_at__lte=now):
            _expire(d, now)
            stats["expired"] += 1

        touched = {}
        stale = list(Offer.objects.select_for_update().filter(
            status=Offer.Status.OFFERED, expires_at__lte=now))
        for o in stale:
            o.status = Offer.Status.TIMED_OUT
            o.responded_at = now
            o.save(update_fields=["status", "responded_at"])
            log(o.donation, DonationEvent.Kind.TIMED_OUT, o.user, note=f"{o.user.display_name} didn't respond in time")
            notes.notify([o.user], Kind.OFFER_LAPSED,
                         f"Your offer for '{o.donation.food_item}' lapsed because it wasn't accepted in time.",
                         o.donation, _stage(o.role)[1])
            touched[(o.donation_id, o.role)] = o.donation
            stats["offers_timed_out"] += 1
        for (donation_id, role), _ in touched.items():
            d = Donation.objects.get(pk=donation_id)
            if _stage_is_waiting(d, role) and not d.offers.filter(role=role, status=Offer.Status.OFFERED).exists():
                _open_wave(d, role)

        soon = now + timedelta(hours=2)
        for d in Donation.objects.filter(
                status__in=[Status.PENDING, Status.ASSIGNED], expiry_warning_sent=False,
                expires_at__isnull=False, expires_at__gt=now, expires_at__lte=soon).select_related("donor"):
            notes.notify([d.donor], Kind.EXPIRING,
                         f"Your listing '{d.food_item}' expires at {timezone.localtime(d.expires_at):%H:%M} "
                         f"and hasn't been collected yet.", d, reverse("donations:donor_dashboard"))
            Donation.objects.filter(pk=d.pk).update(expiry_warning_sent=True)
            stats["warned"] += 1
    return stats


_last_run = {"t": 0.0}


def maybe_process_timeouts():
    """Cheap, throttled sweep called from page loads so no cron job is required."""
    throttle = _cfg("OFFER_PROCESS_THROTTLE_SECONDS", 15)
    if time.monotonic() - _last_run["t"] < throttle:
        return None
    _last_run["t"] = time.monotonic()
    return process_timeouts()


# --------------------------------------------------------------------------- recipient
def _live_offer(donation, user, role):
    return donation.offers.filter(
        user=user, role=role, status=Offer.Status.OFFERED, expires_at__gt=_now()).first()


def can_claim(user, donation):
    """(ok, reason) - can this user claim this donation right now?"""
    if user.role != get_user_model().Role.RECIPIENT or not user.is_approved:
        return False, "Only approved pantries can claim donations."
    if donation.status != Status.PENDING or donation.recipient_id:
        return False, "This donation has already been claimed."
    if donation.is_expired:
        return False, "This listing has expired."
    if not donation.recipient_pool_open and not _live_offer(donation, user, Offer.Role.RECIPIENT):
        return False, "Reserved for another nearby pantry for now. It opens to everyone if they don't respond in time."
    return True, ""


def claim(donation_id, user):
    with transaction.atomic():
        d = Donation.objects.select_for_update().select_related("donor").get(pk=donation_id)
        ok, reason = can_claim(user, d)
        if not ok:
            raise WorkflowError(reason)
        my_offer = _live_offer(d, user, Offer.Role.RECIPIENT)
        now = _now()
        d.recipient = user
        d.status = Status.ASSIGNED
        d.claimed_at = now
        d.save()   # signal -> donor notified
        if my_offer:
            my_offer.status = Offer.Status.ACCEPTED
            my_offer.responded_at = now
            my_offer.save(update_fields=["status", "responded_at"])
        d.offers.filter(role=Offer.Role.RECIPIENT, status=Offer.Status.OFFERED).update(
            status=Offer.Status.WITHDRAWN, responded_at=now)
        log(d, DonationEvent.Kind.CLAIMED, user, note=f"Claimed by {user.display_name}")
        d.driver_pool_open = True
        d.save(update_fields=["driver_pool_open", "date_updated"])
        _open_wave(d, Offer.Role.DRIVER)
        notes.notify([user], Kind.LIVE_TRACKING, LIVE_TRACKING_TEXT,
                     d, reverse("donations:recipient_dashboard"))
    return d


def decline_offer(offer_id, user):
    with transaction.atomic():
        offer = Offer.objects.select_for_update().select_related("donation").get(pk=offer_id, user=user)
        if offer.status != Offer.Status.OFFERED:
            raise WorkflowError("This offer is no longer open.")
        offer.status = Offer.Status.DECLINED
        offer.responded_at = _now()
        offer.save(update_fields=["status", "responded_at"])
        d = offer.donation
        log(d, DonationEvent.Kind.DECLINED, user, note=f"{user.display_name} declined")
        if _stage_is_waiting(d, offer.role) and not d.offers.filter(
                role=offer.role, status=Offer.Status.OFFERED).exists():
            _open_wave(d, offer.role)
    return offer


def release_claim(donation_id, user, reason, note=""):
    """A pantry gives a claim back (before a driver has collected it)."""
    with transaction.atomic():
        d = Donation.objects.select_for_update().get(pk=donation_id)
        if d.recipient_id != user.pk or d.status != Status.ASSIGNED:
            raise WorkflowError("You can only release a claim that hasn't been collected yet.")
        now = _now()
        d.offers.filter(role=Offer.Role.DRIVER, status=Offer.Status.OFFERED).update(
            status=Offer.Status.WITHDRAWN, responded_at=now)
        d.offers.filter(role=Offer.Role.RECIPIENT, user=user, status=Offer.Status.ACCEPTED).update(
            status=Offer.Status.WITHDRAWN)
        label = dict(Donation.CancelReason.choices).get(reason, reason)
        detail = label + (f" - {note}" if note else "")
        log(d, DonationEvent.Kind.RELEASED, user, note=detail)
        d._previous_recipient_name = user.display_name
        d._transition_note = detail
        d.recipient = None
        d.claimed_at = None
        d.status = Status.PENDING
        d.driver_pool_open = True
        d.save()   # signal -> donor told
        _open_wave(d, Offer.Role.RECIPIENT)
    return d


# --------------------------------------------------------------------------- driver
def can_pick_up(user, donation):
    if user.role != get_user_model().Role.DRIVER or not user.is_approved:
        return False, "Only approved drivers can pick up orders."
    if not user.profile_complete:
        return False, "Add your area and street address in your profile before picking up orders."
    if donation.status != Status.ASSIGNED or donation.driver_id:
        return False, "This order is no longer waiting for a driver."
    if donation.is_expired:
        return False, "This listing has expired."
    if not donation.driver_pool_open and not _live_offer(donation, user, Offer.Role.DRIVER):
        return False, "Reserved for a nearer driver for now. It opens to everyone if they don't respond in time."
    return True, ""


def accept_pickup(donation_id, user):
    with transaction.atomic():
        d = Donation.objects.select_for_update().get(pk=donation_id)
        ok, reason = can_pick_up(user, d)
        if not ok:
            raise WorkflowError(reason)
        my_offer = _live_offer(d, user, Offer.Role.DRIVER)
        now = _now()
        d.driver = user
        d.status = Status.IN_TRANSIT
        d.picked_up_at = now
        d.save()   # signal -> donor + pantry told
        if my_offer:
            my_offer.status = Offer.Status.ACCEPTED
            my_offer.responded_at = now
            my_offer.save(update_fields=["status", "responded_at"])
        d.offers.filter(role=Offer.Role.DRIVER, status=Offer.Status.OFFERED).update(
            status=Offer.Status.WITHDRAWN, responded_at=now)
        log(d, DonationEvent.Kind.PICKED_UP, user, note=f"Accepted by {user.display_name}")
        notes.notify([user], Kind.LIVE_TRACKING, LIVE_TRACKING_TEXT,
                     d, reverse("donations:driver_dashboard"))
    return d


def withdraw_pickup(donation_id, user, reason, note=""):
    """A driver hands a job back before delivering it."""
    with transaction.atomic():
        d = Donation.objects.select_for_update().get(pk=donation_id)
        if d.driver_id != user.pk or d.status != Status.IN_TRANSIT:
            raise WorkflowError("You can only withdraw from a delivery you're currently doing.")
        label = dict(Donation.CancelReason.choices).get(reason, reason)
        detail = label + (f" - {note}" if note else "")
        d.offers.filter(role=Offer.Role.DRIVER, user=user, status=Offer.Status.ACCEPTED).update(
            status=Offer.Status.WITHDRAWN)
        log(d, DonationEvent.Kind.WITHDRAWN, user, note=detail)
        d._previous_driver_name = user.display_name
        d._transition_note = detail
        d.driver = None
        d.picked_up_at = None
        d.status = Status.ASSIGNED
        d.driver_pool_open = True
        d.save()   # signal -> donor + pantry told
        _open_wave(d, Offer.Role.DRIVER)
    return d


def mark_delivered(donation_id, user):
    with transaction.atomic():
        d = Donation.objects.select_for_update().get(pk=donation_id)
        if d.driver_id != user.pk or d.status != Status.IN_TRANSIT:
            raise WorkflowError("Only the assigned driver can mark this delivered.")
        d.status = Status.DELIVERED
        d.delivered_at = _now()
        d.save()
        log(d, DonationEvent.Kind.DELIVERED, user, note="Delivered")
    return d


# --------------------------------------------------------------------------- cancellation
def cancel_listing(donation_id, actor, reason, note=""):
    """Donor (owner) or admin cancels a listing, recording why."""
    with transaction.atomic():
        d = Donation.objects.select_for_update().get(pk=donation_id)
        is_owner = d.donor_id == actor.pk
        if not (is_owner or actor.is_admin_role()):
            raise WorkflowError("You can't cancel someone else's listing.")
        if d.status not in (Status.PENDING, Status.ASSIGNED, Status.IN_TRANSIT):
            raise WorkflowError("This listing is already closed.")
        if d.status == Status.IN_TRANSIT and not actor.is_admin_role():
            raise WorkflowError("A driver is already on the way. Message the driver or ask an admin to cancel.")
        now = _now()
        d.offers.filter(status=Offer.Status.OFFERED).update(status=Offer.Status.WITHDRAWN, responded_at=now)
        d.cancel_reason = reason
        d.cancel_note = (note or "")[:255]
        d.cancelled_by = actor
        d.cancelled_at = now
        d.status = Status.CANCELLED
        d.save()   # signal -> everyone involved told, with the reason
        label = dict(Donation.CancelReason.choices).get(reason, reason)
        log(d, DonationEvent.Kind.CANCELLED, actor, note=label + (f" - {note}" if note else ""))
    return d
