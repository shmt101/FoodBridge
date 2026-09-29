from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from accounts.areas import area_label


class Donation(models.Model):
    class Status(models.TextChoices):
        PENDING = "Pending", "Pending"
        ASSIGNED = "Assigned", "Assigned"
        IN_TRANSIT = "In transit", "In transit"
        DELIVERED = "Delivered", "Delivered"
        CANCELLED = "Cancelled", "Cancelled"
        EXPIRED = "Expired", "Expired"

    class CancelReason(models.TextChoices):
        # donor
        NO_LONGER_AVAILABLE = "unavailable", "Food is no longer available"
        SAFETY = "safety", "Quality or food-safety concern"
        MISTAKE = "mistake", "Listed by mistake"
        PICKUP_NOT_POSSIBLE = "no_pickup", "Pickup is no longer possible"
        FOUND_ELSEWHERE = "elsewhere", "Found another recipient"
        # recipient
        CANT_COLLECT = "cant_collect", "We can't receive it in time"
        NO_STORAGE = "no_storage", "No storage space available"
        NOT_NEEDED = "not_needed", "No longer needed"
        # driver
        VEHICLE = "vehicle", "Vehicle unavailable"
        TOO_FAR = "too_far", "Too far for me"
        SCHEDULE = "schedule", "Schedule conflict"
        # everyone
        OTHER = "other", "Other"

    donor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="donations_made", limit_choices_to={"role": "DONOR"},
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="donations_claimed", limit_choices_to={"role": "RECIPIENT"},
    )
    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="donations_delivered", limit_choices_to={"role": "DRIVER"},
    )

    food_item = models.CharField(max_length=150)
    quantity_kg = models.DecimalField(max_digits=8, decimal_places=2)
    pickup_address = models.CharField(max_length=255, blank=True)
    pickup_area = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # Expiry: after this moment an unclaimed / uncollected listing is closed automatically.
    expires_at = models.DateTimeField(null=True, blank=True)
    expiry_warning_sent = models.BooleanField(default=False)

    # Matching: while a pool is closed, only the users holding a live offer can act.
    recipient_pool_open = models.BooleanField(default=True)
    driver_pool_open = models.BooleanField(default=True)

    # Cancellation
    cancel_reason = models.CharField(max_length=20, choices=CancelReason.choices, blank=True)
    cancel_note = models.CharField(max_length=255, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="donations_cancelled",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    date_listed = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_listed"]
        indexes = [models.Index(fields=["status", "expires_at"])]

    def __str__(self):
        return f"{self.food_item} ({self.quantity_kg} kg) - {self.status}"

    def get_absolute_url(self):
        return reverse("donations:detail", args=[self.pk])

    @property
    def donor_name(self):
        return self.donor.organisation_name or self.donor.get_full_name() or self.donor.username

    # ---- location -------------------------------------------------------
    @property
    def effective_area(self):
        """Where the food is: the listing's own area, else the donor's profile area."""
        return self.pickup_area or (self.donor.area if self.donor_id else "")

    @property
    def area_display(self):
        return area_label(self.effective_area)

    # ---- expiry ---------------------------------------------------------
    @property
    def is_open(self):
        return self.status in (self.Status.PENDING, self.Status.ASSIGNED, self.Status.IN_TRANSIT)

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def expires_soon(self):
        return bool(
            self.expires_at and self.status in (self.Status.PENDING, self.Status.ASSIGNED)
            and timezone.now() < self.expires_at <= timezone.now() + timedelta(hours=3)
        )


class Offer(models.Model):
    """A time-limited offer of a donation to one specific pantry or driver."""

    class Role(models.TextChoices):
        RECIPIENT = "RECIPIENT", "Recipient"
        DRIVER = "DRIVER", "Driver"

    class Status(models.TextChoices):
        OFFERED = "offered", "Offered"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        TIMED_OUT = "timed_out", "Timed out"
        WITHDRAWN = "withdrawn", "Withdrawn"

    donation = models.ForeignKey(Donation, on_delete=models.CASCADE, related_name="offers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="donation_offers")
    role = models.CharField(max_length=10, choices=Role.choices)
    wave = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OFFERED)
    distance_km = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["donation_id", "wave", "distance_km"]
        indexes = [models.Index(fields=["status", "expires_at"]), models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.role} offer on #{self.donation_id} to {self.user_id}: {self.status}"

    @property
    def is_live(self):
        return self.status == self.Status.OFFERED and self.expires_at > timezone.now()


class DonationEvent(models.Model):
    """Audit trail / timeline entry for a donation."""

    class Kind(models.TextChoices):
        LISTED = "listed", "Listed"
        OFFERED = "offered", "Offered to nearby partners"
        DECLINED = "declined", "Offer declined"
        TIMED_OUT = "timed_out", "Offer timed out"
        OPENED = "opened", "Opened to all eligible partners"
        CLAIMED = "claimed", "Claimed by a pantry"
        RELEASED = "released", "Claim released"
        PICKED_UP = "picked_up", "Accepted for pickup"
        WITHDRAWN = "withdrawn", "Driver withdrew"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    donation = models.ForeignKey(Donation, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"#{self.donation_id} {self.kind}"
