from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from donations.models import Donation

from . import services


@receiver(pre_save, sender=Donation, dispatch_uid="inbox_remember_donation_status")
def remember_previous_status(sender, instance, update_fields=None, **kwargs):
    """Stash the status the row had in the database, so post_save can spot a change."""
    instance._previous_status = None
    if instance.pk and (update_fields is None or "status" in update_fields):
        instance._previous_status = (
            sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        )


@receiver(post_save, sender=Donation, dispatch_uid="inbox_notify_on_donation_save")
def notify_on_donation_save(sender, instance, created, raw=False, **kwargs):
    if raw or services.is_suppressed():
        return
    if created:
        if instance.status == Donation.Status.PENDING:
            services.donation_listed(instance)
        return
    previous = getattr(instance, "_previous_status", None)
    if previous and previous != instance.status:
        services.donation_status_changed(instance, previous)
