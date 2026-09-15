import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.models import User
from .forms import DonationForm, DonationSearchForm
from .models import Donation


def live_donations(request):
    """Public live donations page — real ORM queries, replaces the old
    static generate_donations_page.py snapshot. Backs the search/filter bar."""
    form = DonationSearchForm(request.GET or None)
    donations = Donation.objects.exclude(status=Donation.Status.CANCELLED).select_related("donor")

    if form.is_valid():
        q = form.cleaned_data.get("q")
        status = form.cleaned_data.get("status")
        if q:
            from django.db.models import Q
            donations = donations.filter(
                Q(food_item__icontains=q) | Q(donor__organisation_name__icontains=q)
                | Q(donor__first_name__icontains=q) | Q(donor__last_name__icontains=q)
            )
        if status:
            donations = donations.filter(status=status)

    total_kg = sum(d.quantity_kg for d in donations if d.status != Donation.Status.CANCELLED)
    delivered_count = donations.filter(status=Donation.Status.DELIVERED).count()

    return render(request, "donations/live_donations.html", {
        "form": form,
        "donations": donations,
        "total_kg": total_kg,
        "delivered_count": delivered_count,
    })


@role_required(User.Role.DONOR)
def donor_dashboard(request):
    if request.method == "POST":
        form = DonationForm(request.POST)
        if form.is_valid():
            donation = form.save(commit=False)
            donation.donor = request.user
            donation.save()
            messages.success(request, "Donation listed.")
            return redirect("donations:donor_dashboard")
    else:
        form = DonationForm()

    my_donations = Donation.objects.filter(donor=request.user)
    return render(request, "donations/donor_dashboard.html", {
        "form": form, "donations": my_donations,
    })


@role_required(User.Role.RECIPIENT)
def recipient_dashboard(request):
    if request.method == "POST":
        donation = get_object_or_404(Donation, pk=request.POST.get("donation_id"))
        if donation.status == Donation.Status.PENDING and donation.recipient_id is None:
            donation.recipient = request.user
            donation.status = Donation.Status.ASSIGNED
            donation.save()
            messages.success(request, f"Claimed '{donation.food_item}'. A driver can now pick it up.")
        return redirect("donations:recipient_dashboard")

    available = Donation.objects.filter(status=Donation.Status.PENDING, recipient__isnull=True)
    my_claims = Donation.objects.filter(recipient=request.user)
    return render(request, "donations/recipient_dashboard.html", {
        "available": available, "my_claims": my_claims,
    })


@role_required(User.Role.DRIVER)
def driver_dashboard(request):
    if request.method == "POST":
        donation = get_object_or_404(Donation, pk=request.POST.get("donation_id"))
        action = request.POST.get("action")
        if action == "accept" and donation.status == Donation.Status.ASSIGNED and donation.driver_id is None:
            donation.driver = request.user
            donation.status = Donation.Status.IN_TRANSIT
            donation.save()
            messages.success(request, f"You're now delivering '{donation.food_item}'.")
        elif action == "delivered" and donation.driver_id == request.user.id:
            from django.utils import timezone
            donation.status = Donation.Status.DELIVERED
            donation.delivered_at = timezone.now()
            donation.save()
            messages.success(request, f"Marked '{donation.food_item}' as delivered. Thank you!")
        return redirect("donations:driver_dashboard")

    needs_driver = Donation.objects.filter(status=Donation.Status.ASSIGNED, driver__isnull=True)
    my_deliveries = Donation.objects.filter(driver=request.user).exclude(status=Donation.Status.DELIVERED)
    completed = Donation.objects.filter(driver=request.user, status=Donation.Status.DELIVERED)[:10]
    return render(request, "donations/driver_dashboard.html", {
        "needs_driver": needs_driver, "my_deliveries": my_deliveries, "completed": completed,
    })


@login_required
def admin_dashboard(request):
    if not request.user.is_admin_role():
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    donations = Donation.objects.select_related("donor", "recipient", "driver")
    return render(request, "donations/admin_dashboard.html", {
        "donations": donations,
        "counts": {
            "pending": donations.filter(status=Donation.Status.PENDING).count(),
            "in_progress": donations.filter(status__in=[Donation.Status.ASSIGNED, Donation.Status.IN_TRANSIT]).count(),
            "delivered": donations.filter(status=Donation.Status.DELIVERED).count(),
            "donors": User.objects.filter(role=User.Role.DONOR).count(),
            "recipients": User.objects.filter(role=User.Role.RECIPIENT).count(),
            "drivers": User.objects.filter(role=User.Role.DRIVER).count(),
        },
    })


@login_required
def export_delivered_csv(request):
    """CSV report of delivered food, for food auditors."""
    if not request.user.is_admin_role():
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="foodbridge_delivered_report.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Food item", "Quantity (kg)", "Donor", "Recipient", "Driver",
        "Pickup address", "Date listed", "Delivered at",
    ])
    delivered = Donation.objects.filter(status=Donation.Status.DELIVERED).select_related(
        "donor", "recipient", "driver"
    )
    for d in delivered:
        writer.writerow([
            d.food_item, d.quantity_kg, d.donor_name,
            d.recipient.get_full_name() if d.recipient else "",
            d.driver.get_full_name() if d.driver else "",
            d.pickup_address,
            d.date_listed.strftime("%Y-%m-%d %H:%M"),
            d.delivered_at.strftime("%Y-%m-%d %H:%M") if d.delivered_at else "",
        ])
    return response
