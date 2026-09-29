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

    class Category(models.TextChoices):
        PRODUCE = "produce", "Fresh produce"
        BAKERY = "bakery", "Bakery"
        DAIRY = "dairy", "Dairy & eggs"
        MEAT_FISH = "meat_fish", "Meat & fish"
        PREPARED = "prepared", "Prepared meals"
        PACKAGED = "packaged", "Packaged / pantry"
        OTHER = "other", "Other"

    class Storage(models.TextChoices):
        AMBIENT = "ambient", "Room temperature"
        CHILLED = "chilled", "Keep chilled"
        FROZEN = "frozen", "Keep frozen"

    class DateType(models.TextChoices):
        USE_BY = "use_by", "Use by"
        BEST_BEFORE = "best_before", "Best before"

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
    food_category = models.CharField(max_length=12, choices=Category.choices, default=Category.OTHER)
    storage = models.CharField(max_length=8, choices=Storage.choices, default=Storage.AMBIENT)
    date_type = models.CharField(max_length=12, choices=DateType.choices, default=DateType.USE_BY)
    allergen_note = models.CharField(max_length=150, blank=True, help_text="e.g. contains nuts, gluten, dairy")
    safety_confirmed = models.BooleanField(
        default=False, help_text="Donor confirmed the food is safe, correctly stored and within its date.")
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


class Feedback(models.Model):
    """A participant's rating of a delivered donation, optionally flagging a problem."""

    donation = models.ForeignKey(Donation, on_delete=models.CASCADE, related_name="feedback")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="feedback_given")
    rating = models.PositiveSmallIntegerField(help_text="1 (poor) to 5 (excellent)")
    comment = models.CharField(max_length=500, blank=True)
    is_issue = models.BooleanField(default=False, help_text="Something went wrong and an admin should look.")
    resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["donation", "author"], name="one_feedback_per_person")]

    def __str__(self):
        return f"{self.author_id} on #{self.donation_id}: {self.rating}/5"
