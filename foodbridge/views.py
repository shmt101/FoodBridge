from django.db.models import Count
from django.shortcuts import render
from django.utils import timezone

from . import media_library

# Representative styling for each food category on the Home page's "what's live right
# now" box, used as a fallback when nobody in that category has uploaded a photo yet.
CATEGORY_SHOWCASE = {
    "produce": {"label": "Fresh produce", "icon": "bi-basket2-fill", "gradient": "linear-gradient(135deg,#22c55e,#065f46)"},
    "bakery": {"label": "Bakery", "icon": "bi-cup-hot-fill", "gradient": "linear-gradient(135deg,#f59e0b,#92400e)"},
    "dairy": {"label": "Dairy & eggs", "icon": "bi-egg-fill", "gradient": "linear-gradient(135deg,#fbbf24,#a16207)"},
    "meat_fish": {"label": "Meat & fish", "icon": "bi-fish", "gradient": "linear-gradient(135deg,#f87171,#7f1d1d)"},
    "prepared": {"label": "Prepared meals", "icon": "bi-egg-fried", "gradient": "linear-gradient(135deg,#fb923c,#9a3412)"},
    "packaged": {"label": "Packaged & pantry", "icon": "bi-box-seam-fill", "gradient": "linear-gradient(135deg,#22d3ee,#155e63)"},
    "other": {"label": "Surplus food", "icon": "bi-bag-heart-fill", "gradient": "linear-gradient(135deg,#a78bfa,#3730a3)"},
}


def _live_showcase_cards():
    """Which categories currently have an open listing, with a live count each - feeds
    the Home page's auto-rotating 'what's live right now' box. Prefers a real donor
    photo (the most recently listed one in that category that has one); falls back to
    the icon-on-gradient tile for a category where nobody's uploaded a photo yet."""
    from donations.models import Donation
    Status = Donation.Status
    open_qs = Donation.objects.exclude(status__in=[Status.CANCELLED, Status.EXPIRED])
    counts = dict(open_qs.values_list("food_category").annotate(n=Count("id")).order_by())

    photos = {}
    for d in open_qs.order_by("-date_listed").only("food_category", "photo"):
        if d.food_category not in photos and d.photo:
            photos[d.food_category] = d.photo.url

    cards = []
    for key, meta in CATEGORY_SHOWCASE.items():
        n = counts.get(key, 0)
        if n:
            card = {"key": key, "count": n, **meta}
            if key in photos:
                card["photo"] = photos[key]
            cards.append(card)
    return cards


def landing(request):
    """The root splash page ("/"). Separate from the Home page ("/home/") - this one has
    no nav bar by design - but shares the same live showcase data."""
    return render(request, "landing.html", {"live_showcase_cards": _live_showcase_cards()})


def home(request):
    """The public Home page. Shows the marketing hero to visitors; shows a short,
    role-specific status card to anyone logged in instead."""
    user = request.user
    context = {"live_showcase_cards": _live_showcase_cards()}

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
