"""Admin approval workflow for new Donor / Recipient / Driver accounts."""
from django.utils import timezone

from inbox import services as notes

from .models import User


def approve_user(user, admin):
    if user.is_admin_role():
        return user
    user.approval_status = User.Approval.APPROVED
    user.approved_at = timezone.now()
    user.approved_by = admin
    user.rejection_reason = ""
    user.save()
    notes.account_approved(user)
    return user


def reject_user(user, admin, reason=""):
    if user.is_admin_role():
        return user
    user.approval_status = User.Approval.REJECTED
    user.approved_at = None
    user.approved_by = admin
    user.rejection_reason = (reason or "").strip()[:255]
    user.save()
    notes.account_rejected(user, user.rejection_reason)
    return user
