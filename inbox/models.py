from django.conf import settings
from django.db import models


class Message(models.Model):
    """A one-to-one, in-app message between two users."""

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_messages",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_messages",
    )
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["recipient", "read_at"])]

    def __str__(self):
        return f"{self.sender} -> {self.recipient}: {self.body[:40]}"


class Notification(models.Model):
    """A system-generated alert about something that relates to one user."""

    class Kind(models.TextChoices):
        NEW_DONATION = "new_donation", "New donation"
        CLAIMED = "claimed", "Donation claimed"
        READY = "ready", "Ready for pickup"
        PICKED_UP = "picked_up", "Picked up"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        UPDATE = "update", "Status update"

    ICONS = {
        "new_donation": "bi-basket2",
        "claimed": "bi-hand-thumbs-up",
        "ready": "bi-truck",
        "picked_up": "bi-box-seam",
        "delivered": "bi-check-circle",
        "cancelled": "bi-x-circle",
        "update": "bi-info-circle",
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.UPDATE)
    text = models.CharField(max_length=255)
    donation = models.ForeignKey(
        "donations.Donation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notifications",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at"])]

    @property
    def icon(self):
        return self.ICONS.get(self.kind, "bi-bell")

    @property
    def is_unread(self):
        return self.read_at is None

    def __str__(self):
        return f"{self.user}: {self.text[:50]}"
