from django.conf import settings
from django.db import models
from django.urls import reverse


class Donation(models.Model):
    class Status(models.TextChoices):
        PENDING = "Pending", "Pending"
        ASSIGNED = "Assigned", "Assigned"
        IN_TRANSIT = "In transit", "In transit"
        DELIVERED = "Delivered", "Delivered"
        CANCELLED = "Cancelled", "Cancelled"

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
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    date_listed = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_listed"]

    def __str__(self):
        return f"{self.food_item} ({self.quantity_kg} kg) - {self.status}"

    def get_absolute_url(self):
        return reverse("donations:detail", args=[self.pk])

    @property
    def donor_name(self):
        return self.donor.organisation_name or self.donor.get_full_name() or self.donor.username
