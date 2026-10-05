from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from .areas import area_label


class User(AbstractUser):
    """Custom user with a role field driving RBAC across the site."""

    class Role(models.TextChoices):
        DONOR = "DONOR", "Donor"
        RECIPIENT = "RECIPIENT", "Recipient"
        DRIVER = "DRIVER", "Driver"
        ADMIN = "ADMIN", "Admin"
        AUDITOR = "AUDITOR", "Auditor"

    class Approval(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.DONOR)

    # Admin approval gate: donors, recipients and drivers can't use the portal until approved.
    approval_status = models.CharField(
        max_length=10, choices=Approval.choices, default=Approval.PENDING, db_index=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="approvals_given",
    )
    rejection_reason = models.CharField(max_length=255, blank=True)

    # Profile-manager fields, common to every role.
    phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    area = models.CharField(
        max_length=40, blank=True,
        help_text="The area you operate from (used for nearby matching and distances).",
    )
    organisation_name = models.CharField(
        max_length=150, blank=True,
        help_text="Business name (donor), pantry/org name (recipient), or leave blank.",
    )
    email_notifications = models.BooleanField(
        default=True, help_text="Also email me important updates (offers, approvals, cancellations).",
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
    def is_auditor(self):
        """The Auditor role: read-only access to Partnership Activity Reports and CSV exports.
        Deliberately separate from is_admin_role() - an auditor cannot approve or manage users,
        and an admin cannot pull the network-wide reports."""
        return self.role == self.Role.AUDITOR

    # ---- approval -------------------------------------------------------
    @property
    def is_approved(self):
        return (self.is_superuser or self.is_admin_role() or self.is_auditor
                or self.approval_status == self.Approval.APPROVED)

    @property
    def is_rejected(self):
        return not self.is_approved and self.approval_status == self.Approval.REJECTED

    # ---- profile readiness ---------------------------------------------
    @property
    def profile_complete(self):
        """A usable location: both an area and a street address."""
        return bool(self.area and self.address.strip())

    @property
    def can_pick_up(self):
        """Drivers may only pick up orders once approved AND their location is on file."""
        return self.is_driver() and self.is_approved and self.profile_complete

    @property
    def area_display(self):
        return area_label(self.area)

    def save(self, *args, **kwargs):
        extra_fields = set()
        # Staff/superuser accounts are functionally Admins everywhere in the app (see
        # is_admin_role()) regardless of how they were created - Django's own /admin/,
        # createsuperuser, or our in-app flows. Without this, an account made outside our
        # "Add User" form keeps whatever role it defaulted to (Donor) while acting as an
        # Admin everywhere else, which is exactly the confusing state this prevents.
        # Auditor is left alone - it's a deliberate separate role, not an "unset" one.
        if (self.is_superuser or self.is_staff) and self.role in (
                self.Role.DONOR, self.Role.RECIPIENT, self.Role.DRIVER):
            self.role = self.Role.ADMIN
            extra_fields.add("role")

        # Admins / staff never wait in the approval queue.
        if (self.is_superuser or self.is_staff or self.role in (self.Role.ADMIN, self.Role.AUDITOR)) \
                and self.approval_status != self.Approval.APPROVED:
            self.approval_status = self.Approval.APPROVED
            self.approved_at = self.approved_at or timezone.now()
            extra_fields |= {"approval_status", "approved_at"}

        if extra_fields and kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | extra_fields
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        """Friendly name used in notifications and messages."""
        return self.organisation_name or self.get_full_name() or self.username

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"
