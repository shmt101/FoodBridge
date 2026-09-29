from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Donation


@receiver(post_save, sender=Donation, dispatch_uid="donations_start_matching")
def start_matching_on_create(sender, instance, created, raw=False, **kwargs):
    """A newly listed donation is immediately offered to nearby pantries + alerts sent."""
    if raw or not created:
        return
    from . import workflow
    workflow.start_matching(instance)
