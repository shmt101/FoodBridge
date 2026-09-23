from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user with a role field driving RBAC across the site."""

    class Role(models.TextChoices):
        DONOR = "DONOR", "Donor"
        RECIPIENT = "RECIPIENT", "Recipient"
        DRIVER = "DRIVER", "Driver"
        ADMIN = "ADMIN", "Admin / Auditor"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.DONOR)

    # Profile-manager fields, common to every role.
    phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    organisation_name = models.CharField(
        max_length=150, blank=True,
        help_text="Business name (donor), pantry/org name (recipient), or leave blank.",
    )
    bio = models.TextField(blank=True, help_text="Short description shown on your profile.")
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)

    def is_donor(self):
        return self.role == self.Role.DONOR

    def is_recipient(self):
        return self.role == self.Role.RECIPIENT

    def is_driver(self):
        return self.role == self.Role.DRIVER

    def is_admin_role(self):
        return self.role == self.Role.ADMIN or self.is_staff

    @property
    def display_name(self):
        """Friendly name used in notifications and messages."""
        return self.organisation_name or self.get_full_name() or self.username

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"
