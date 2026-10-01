from django.shortcuts import render
from django.utils import timezone

from . import media_library


def home(request):
    """The public Home page. Shows the marketing hero to visitors; shows a short,
    role-specific status card to anyone logged in instead."""
    user = request.user
    context = {}

    if not user.is_authenticated:
        return render(request, "index.html", context)

    context["personalized"] = True

    if not user.is_approved:
        context["home_kind"] = "pending"
        return render(request, "index.html", context)

    from accounts.models import User
    from donations.models import Donation, Offer

    if user.is_auditor:
        context["home_kind"] = "auditor"
    elif user.is_admin_role():
        context["home_kind"] = "admin"
        context["pending_count"] = (
            User.objects.filter(approval_status=User.Approval.PENDING)
            .exclude(role=User.Role.ADMIN).exclude(is_staff=True).count()
        )
    elif user.role == User.Role.DONOR:
        mine = Donation.objects.filter(donor=user)
        context["home_kind"] = "donor"
        context["stat_a"] = mine.filter(status__in=["Pending", "Assigned", "In transit"]).count()
        context["stat_b"] = round(sum(float(d.quantity_kg) for d in mine.filter(status="Delivered")), 1)
    elif user.role == User.Role.RECIPIENT:
        context["home_kind"] = "recipient"
        context["stat_a"] = Offer.objects.filter(
            user=user, role=Offer.Role.RECIPIENT, status=Offer.Status.OFFERED, expires_at__gt=timezone.now()
        ).count()
        context["stat_b"] = Donation.objects.filter(recipient=user, status__in=["Assigned", "In transit"]).count()
    elif user.role == User.Role.DRIVER:
        context["home_kind"] = "driver"
        context["driver_ready"] = user.can_pick_up
        context["stat_a"] = Offer.objects.filter(
            user=user, role=Offer.Role.DRIVER, status=Offer.Status.OFFERED, expires_at__gt=timezone.now()
        ).count()
    else:
        context["home_kind"] = "other"

    return render(request, "index.html", context)


def about(request):
    return render(request, "about.html", {
        "videos": media_library.VIDEOS,
        "audio": media_library.AUDIO,
        "benchmarks": media_library.BENCHMARKS,
        "partner_links": media_library.PARTNER_LINKS,
    })


def privacy(request):
    return render(request, "legal/privacy.html")


def terms(request):
    return render(request, "legal/terms.html")
