"""Notification helpers: who gets told what, when a donation changes."""
import threading
from contextlib import contextmanager
from decimal import Decimal

from django.contrib.auth import get_user_model

from .models import Notification

_state = threading.local()


@contextmanager
def notifications_suppressed():
    """Temporarily stop donation signals creating notifications (e.g. bulk demo seeding)."""
    previous = getattr(_state, "suppressed", False)
    _state.suppressed = True
    try:
        yield
    finally:
        _state.suppressed = previous


def is_suppressed():
    return getattr(_state, "suppressed", False)


def notify(users, kind, text, donation=None):
    """Create one notification per distinct user. None entries are ignored."""
    seen, rows = set(), []
    for user in users:
        if user is None or user.pk in seen:
            continue
        seen.add(user.pk)
        rows.append(Notification(user=user, kind=kind, text=text[:255], donation=donation))
    if rows:
        Notification.objects.bulk_create(rows)
    return len(rows)


def _kg(quantity):
    return format(Decimal(str(quantity)).normalize(), "f")


def _active(role):
    User = get_user_model()
    return User.objects.filter(role=role, is_active=True)


def donation_listed(donation):
    """A brand-new donation: tell drivers (heads-up) and recipients (they can claim it)."""
    User = get_user_model()
    what = f"'{donation.food_item}' ({_kg(donation.quantity_kg)} kg) from {donation.donor_name}"
    notify(
        _active(User.Role.DRIVER), Notification.Kind.NEW_DONATION,
        f"New donation listed: {what}. You can accept it once a pantry has claimed it.",
        donation,
    )
    notify(
        _active(User.Role.RECIPIENT), Notification.Kind.NEW_DONATION,
        f"New donation available to claim: {what}.",
        donation,
    )


def donation_status_changed(donation, old_status):
    """Tell each person affected by a status change (never the person who did it)."""
    User = get_user_model()
    Status = type(donation).Status
    Kind = Notification.Kind
    food = f"'{donation.food_item}'"
    new = donation.status

    if new == Status.ASSIGNED:
        who = donation.recipient.display_name if donation.recipient else "A pantry"
        notify([donation.donor], Kind.CLAIMED,
               f"{who} has claimed your donation {food}. A driver will collect it soon.", donation)
        notify(_active(User.Role.DRIVER), Kind.READY,
               f"Ready for pickup: {food} ({_kg(donation.quantity_kg)} kg), claimed by {who}. "
               f"Accept it from your dashboard.", donation)
    elif new == Status.IN_TRANSIT:
        driver = donation.driver.display_name if donation.driver else "A driver"
        notify([donation.donor], Kind.PICKED_UP,
               f"Your donation {food} has been picked up by {driver} and is on its way.", donation)
        notify([donation.recipient], Kind.PICKED_UP,
               f"{food} has been picked up by {driver} and is on its way to you.", donation)
    elif new == Status.DELIVERED:
        notify([donation.donor], Kind.DELIVERED,
               f"Your donation {food} has been delivered. Thank you for helping!", donation)
        notify([donation.recipient], Kind.DELIVERED, f"{food} has been delivered.", donation)
    elif new == Status.CANCELLED:
        notify([donation.donor, donation.recipient, donation.driver], Kind.CANCELLED,
               f"{food} has been cancelled.", donation)
    else:
        notify([donation.donor], Kind.UPDATE,
               f"Your donation {food} is now marked '{new}'.", donation)
