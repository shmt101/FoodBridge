"""Notification helpers: who gets told what, and when."""
import logging
import threading
from contextlib import contextmanager
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse

from .models import Notification

_state = threading.local()
log = logging.getLogger(__name__)


@contextmanager
def notifications_suppressed():
    """Temporarily stop donation signals/workflow creating notifications (e.g. bulk demo seeding)."""
    previous = getattr(_state, "suppressed", False)
    _state.suppressed = True
    try:
        yield
    finally:
        _state.suppressed = previous


def is_suppressed():
    return getattr(_state, "suppressed", False)


def notify(users, kind, text, donation=None, link=""):
    """Create one notification per distinct user. None entries are ignored."""
    seen, rows = set(), []
    for user in users:
        if user is None or user.pk in seen:
            continue
        seen.add(user.pk)
        rows.append(Notification(user=user, kind=kind, text=text[:255], donation=donation, link=link))
    if rows:
        Notification.objects.bulk_create(rows)
        _email(rows, users)
    return len(rows)


def _email(rows, users):
    """Also email important notifications. Never lets a mail problem break the app."""
    kinds = getattr(settings, "EMAIL_NOTIFICATION_KINDS", set())
    by_pk = {u.pk: u for u in users if u is not None}
    messages = []
    for n in rows:
        user = by_pk.get(n.user_id if hasattr(n, "user_id") and n.user_id else n.user.pk)
        if n.kind not in kinds or user is None or not user.email or not getattr(user, "email_notifications", True):
            continue
        site = getattr(settings, "SITE_URL", "").rstrip("/")
        link = f"{site}{n.link or reverse('accounts:dashboard')}"
        body = (f"Hi {user.first_name or user.username},\n\n{n.text}\n\nOpen FoodBridge: {link}\n\n"
                f"You get these emails because email updates are on for your account. "
                f"Turn them off any time in your profile: {site}{reverse('accounts:profile')}\n")
        messages.append(mail.EmailMessage(f"FoodBridge: {n.get_kind_display()}", body,
                                          settings.DEFAULT_FROM_EMAIL, [user.email]))
    if not messages:
        return
    try:
        connection = mail.get_connection(fail_silently=False)
        connection.send_messages(messages)
    except Exception:  # noqa: BLE001 - email must never break a claim/delivery
        log.exception("Could not send %d notification email(s)", len(messages))


def _kg(quantity):
    return format(Decimal(str(quantity)).normalize(), "f")


def role_dashboard_link(user):
    User = get_user_model()
    return {
        User.Role.DONOR: reverse("donations:donor_dashboard"),
        User.Role.RECIPIENT: reverse("donations:recipient_dashboard"),
        User.Role.DRIVER: reverse("donations:driver_dashboard"),
    }.get(user.role, reverse("donations:admin_dashboard"))


# ---- account approval ------------------------------------------------------
def notify_admins_new_signup(user):
    User = get_user_model()
    admins = User.objects.filter(is_active=True).filter(role=User.Role.ADMIN) | \
        User.objects.filter(is_active=True, is_staff=True)
    notify(
        admins.distinct(), Notification.Kind.APPROVAL,
        f"New {user.get_role_display().lower()} sign-up awaiting approval: {user.display_name} (@{user.username}).",
        link=reverse("accounts:approvals"),
    )


def account_approved(user):
    notify([user], Notification.Kind.APPROVAL,
           "Your FoodBridge account has been approved. Welcome aboard!", link=role_dashboard_link(user))


def account_rejected(user, reason=""):
    text = "Your FoodBridge account request was not approved."
    if reason:
        text += f" Reason: {reason}"
    notify([user], Notification.Kind.APPROVAL, text, link=reverse("accounts:pending"))


# ---- donation status changes -------------------------------------------------
def donation_status_changed(donation, old_status):
    """Tell each person affected by a status change (never the person who did it)."""
    Status = type(donation).Status
    Kind = Notification.Kind
    food = f"'{donation.food_item}'"
    new = donation.status
    donor_link = reverse("donations:donor_dashboard")
    recipient_link = reverse("donations:recipient_dashboard")
    driver_link = reverse("donations:driver_dashboard")
    note = getattr(donation, "_transition_note", "")

    if new == Status.ASSIGNED and old_status == Status.PENDING:
        who = donation.recipient.display_name if donation.recipient else "A pantry"
        notify([donation.donor], Kind.CLAIMED,
               f"{who} has claimed your donation {food}. We're now finding a driver.",
               donation, donor_link)
    elif new == Status.ASSIGNED and old_status == Status.IN_TRANSIT:
        who = getattr(donation, "_previous_driver_name", "The driver")
        text = f"{who} can no longer collect {food}. We're finding another driver."
        if note:
            text += f" ({note})"
        notify([donation.donor, donation.recipient], Kind.RELEASED, text[:255], donation, donor_link)
    elif new == Status.IN_TRANSIT:
        driver = donation.driver.display_name if donation.driver else "A driver"
        notify([donation.donor], Kind.PICKED_UP,
               f"Your donation {food} has been picked up by {driver} and is on its way.",
               donation, donor_link)
        notify([donation.recipient], Kind.PICKED_UP,
               f"{food} has been picked up by {driver} and is on its way to you.",
               donation, recipient_link)
    elif new == Status.DELIVERED:
        notify([donation.donor], Kind.DELIVERED,
               f"Your donation {food} has been delivered. Thank you for helping!", donation, donor_link)
        notify([donation.recipient], Kind.DELIVERED, f"{food} has been delivered.", donation, recipient_link)
    elif new == Status.CANCELLED:
        by = donation.cancelled_by
        reason = donation.get_cancel_reason_display() if donation.cancel_reason else ""
        who = "The donor" if by and by.pk == donation.donor_id else (by.display_name if by else "An admin")
        text = f"{food} was cancelled by {who}."
        if reason:
            text += f" Reason: {reason}."
        if donation.cancel_note:
            text += f" \u201c{donation.cancel_note}\u201d"
        notify([u for u in (donation.donor, donation.recipient, donation.driver) if u and u != by],
               Kind.CANCELLED, text[:255], donation, donor_link)
    elif new == Status.EXPIRED:
        notify([donation.donor], Kind.EXPIRED,
               f"Your listing {food} expired before it was collected. You can list it again from your dashboard.",
               donation, donor_link)
        notify([donation.recipient, donation.driver], Kind.EXPIRED,
               f"{food} has expired and is no longer available.", donation, recipient_link)
    elif new == Status.PENDING and old_status == Status.ASSIGNED:
        who = getattr(donation, "_previous_recipient_name", "The pantry")
        text = f"{who} released their claim on {food}. We're finding another pantry."
        if note:
            text += f" ({note})"
        notify([donation.donor], Kind.RELEASED, text[:255], donation, donor_link)
    else:
        notify([donation.donor], Kind.UPDATE,
               f"Your donation {food} is now marked '{new}'.", donation, donor_link)
