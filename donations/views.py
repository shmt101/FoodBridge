import csv
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.areas import AREAS, STATES, area_label, distance_km
from accounts.decorators import admin_required, role_required
from accounts.models import User
from . import analytics, workflow
from inbox import services as notes
from inbox.models import Notification
from .forms import (DONOR_CANCEL_REASONS, DRIVER_WITHDRAW_REASONS, RECIPIENT_RELEASE_REASONS,
                    DonationForm, DonationSearchForm, FeedbackForm, ReasonForm)
from .models import Donation, Feedback, Offer

Status = Donation.Status


def _run(request, fn, *args, success=None):
    """Run a workflow action, turning rule violations into friendly messages."""
    try:
        result = fn(*args)
    except workflow.WorkflowError as exc:
        messages.error(request, str(exc))
        return None
    except Donation.DoesNotExist:
        messages.error(request, "That donation could not be found.")
        return None
    if success:
        messages.success(request, success(result) if callable(success) else success)
    return result


def _reason_response(request, reasons, fn, donation_id, success):
    form = ReasonForm(request.POST, reasons=reasons)
    if not form.is_valid():
        messages.error(request, "Please choose a reason.")
        return
    _run(request, fn, donation_id, request.user, form.cleaned_data["reason"],
         form.cleaned_data["note"], success=success)


# --------------------------------------------------------------------------- public
def live_donations(request):
    """Public live page with an area filter: pick where you are and see the nearest listings."""
    workflow.maybe_process_timeouts()
    form = DonationSearchForm(request.GET or None)
    donations = Donation.objects.select_related("donor", "recipient")
    chosen_area = ""
    radius = None
    status = ""

    if form.is_valid():
        q = form.cleaned_data.get("q")
        status = form.cleaned_data.get("status")
        state = form.cleaned_data.get("state")
        chosen_area = form.cleaned_data.get("area")
        radius = form.cleaned_data.get("radius")
        if q:
            donations = donations.filter(
                Q(food_item__icontains=q) | Q(donor__organisation_name__icontains=q)
                | Q(donor__first_name__icontains=q) | Q(donor__last_name__icontains=q))
        if state and not chosen_area:
            donations = donations.filter(
                Q(pickup_area__startswith=f"{state.lower()}-")
                | Q(pickup_area="", donor__area__startswith=f"{state.lower()}-"))
    if status:
        donations = donations.filter(status=status)
    else:
        donations = donations.exclude(status__in=[Status.CANCELLED, Status.EXPIRED])

    rows = list(donations)
    for d in rows:
        d.km_away = distance_km(chosen_area, d.effective_area) if chosen_area else None
    if chosen_area:
        if radius:
            rows = [d for d in rows if d.km_away is not None and d.km_away <= float(radius)]
        rows.sort(key=lambda d: (d.km_away is None, d.km_away or 0))

    total_kg = sum(d.quantity_kg for d in rows if d.status not in (Status.CANCELLED, Status.EXPIRED))
    delivered_count = sum(1 for d in rows if d.status == Status.DELIVERED)
    return render(request, "donations/live_donations.html", {
        "form": form, "donations": rows, "total_kg": total_kg, "delivered_count": delivered_count,
        "chosen_area": chosen_area, "chosen_area_label": area_label(chosen_area),
        "meals": analytics.meals_from_kg(total_kg),
        "my_area": request.user.area if request.user.is_authenticated else "",
        "distance_class": analytics.distance_class,
    })


@login_required
def donation_detail(request, pk):
    """Timeline for one donation - visible to the people involved and to admins."""
    d = get_object_or_404(Donation.objects.select_related("donor", "recipient", "driver"), pk=pk)
    u = request.user
    if not (u.is_admin_role() or u.pk in (d.donor_id, d.recipient_id, d.driver_id)):
        raise PermissionDenied("You can only view donations you're involved in.")
    mine = Feedback.objects.filter(donation=d, author=u).first()
    can_rate = d.status == Status.DELIVERED and u.pk in (d.donor_id, d.recipient_id, d.driver_id)
    initial = {"rating": str(mine.rating), "comment": mine.comment, "is_issue": mine.is_issue} if mine else None
    return render(request, "donations/donation_detail.html", {
        "d": d, "events": d.events.select_related("actor"),
        "offers": d.offers.select_related("user"),
        "leg_km": distance_km(d.effective_area, d.recipient.area) if d.recipient_id else None,
        "can_rate": can_rate, "my_feedback": mine, "feedback_form": FeedbackForm(initial=initial),
        "all_feedback": d.feedback.select_related("author") if u.is_admin_role() else None,
    })


@login_required
def submit_feedback(request, pk):
    """A participant rates a delivered donation and can flag a problem."""
    d = get_object_or_404(Donation, pk=pk)
    u = request.user
    if request.method != "POST" or d.status != Status.DELIVERED or u.pk not in (d.donor_id, d.recipient_id, d.driver_id):
        raise PermissionDenied("You can only rate deliveries you took part in.")
    form = FeedbackForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please choose a rating from 1 to 5.")
        return redirect("donations:detail", pk=pk)
    fb, created = Feedback.objects.update_or_create(
        donation=d, author=u,
        defaults={"rating": int(form.cleaned_data["rating"]), "comment": form.cleaned_data["comment"],
                  "is_issue": form.cleaned_data["is_issue"], "resolved": False, "resolved_by": None, "resolved_at": None},
    )
    if fb.is_issue:
        admins = User.objects.filter(is_active=True).filter(Q(role=User.Role.ADMIN) | Q(is_staff=True)).distinct()
        notes.notify(admins, Notification.Kind.ISSUE,
                     f"{u.display_name} reported a problem with '{d.food_item}': {fb.comment or 'no details given'}",
                     d, "/donations/feedback/")
    messages.success(request, "Thanks for the feedback." + (" An admin has been told about the problem." if fb.is_issue else ""))
    return redirect("donations:detail", pk=pk)


@admin_required
def feedback_list(request):
    """Admin: ratings and reported problems, with a resolve action."""
    if request.method == "POST":
        fb = get_object_or_404(Feedback, pk=request.POST.get("feedback_id"))
        fb.resolved = True
        fb.resolved_by = request.user
        fb.resolved_at = timezone.now()
        fb.save(update_fields=["resolved", "resolved_by", "resolved_at"])
        notes.notify([fb.author], Notification.Kind.UPDATE,
                     f"An admin looked at the problem you reported on '{fb.donation.food_item}' and marked it resolved.",
                     fb.donation, reverse("donations:detail", args=[fb.donation_id]))
        messages.success(request, "Marked as resolved.")
        return redirect(request.get_full_path())
    show = request.GET.get("show", "issues")
    rows = Feedback.objects.select_related("donation", "author", "donation__donor")
    if show == "issues":
        rows = rows.filter(is_issue=True, resolved=False)
    elif show == "resolved":
        rows = rows.filter(is_issue=True, resolved=True)
    return render(request, "donations/feedback.html", {
        "rows": rows[:200], "show": show, "summary": analytics.feedback_summary(),
    })


# --------------------------------------------------------------------------- map
def _active_by_area():
    groups = {}
    active = Donation.objects.filter(status__in=[Status.PENDING, Status.ASSIGNED, Status.IN_TRANSIT]).select_related("donor")
    for d in active:
        area = AREAS.get(d.effective_area)
        if not area:
            continue
        g = groups.setdefault(area.key, {"key": area.key, "label": f"{area.label}, {area.state}",
                                         "lat": area.lat, "lng": area.lng, "count": 0, "kg": 0.0, "items": []})
        g["count"] += 1
        g["kg"] += float(d.quantity_kg)
        if len(g["items"]) < 5:
            g["items"].append({"food": d.food_item, "kg": float(d.quantity_kg), "donor": d.donor_name, "status": d.status})
    for g in groups.values():
        g["kg"] = round(g["kg"], 1)
    return sorted(groups.values(), key=lambda g: -g["count"])


def map_data(request):
    """Aggregated by area only - never exposes street addresses."""
    from django.http import JsonResponse
    workflow.maybe_process_timeouts()
    return JsonResponse({"areas": _active_by_area()})


def live_map(request):
    workflow.maybe_process_timeouts()
    me = AREAS.get(request.user.area) if request.user.is_authenticated else None
    return render(request, "donations/map.html", {
        "areas": _active_by_area(),
        "me": {"label": area_label(me.key), "lat": me.lat, "lng": me.lng} if me else None,
    })



# --------------------------------------------------------------------------- donor
@role_required(User.Role.DONOR)
def donor_dashboard(request):
    workflow.maybe_process_timeouts()
    if request.method == "POST":
        action = request.POST.get("action", "list")
        if action == "cancel":
            _reason_response(request, DONOR_CANCEL_REASONS, workflow.cancel_listing,
                             request.POST.get("donation_id"), "Listing cancelled. Everyone involved has been told why.")
            return redirect("donations:donor_dashboard")
        form = DonationForm(request.POST)
        if form.is_valid():
            donation = form.save(commit=False)
            donation.donor = request.user
            donation.save()   # signal -> offers to nearby pantries + driver alerts
            messages.success(request, "Donation listed. Nearby pantries have been offered it.")
            return redirect("donations:donor_dashboard")
    else:
        form = DonationForm(initial={"pickup_area": request.user.area, "pickup_address": request.user.address})

    mine = list(Donation.objects.filter(donor=request.user).select_related("recipient", "driver"))
    delivered = [d for d in mine if d.status == Status.DELIVERED]
    terminal = len(delivered) + sum(1 for d in mine if d.status in (Status.CANCELLED, Status.EXPIRED))
    return render(request, "donations/donor_dashboard.html", {
        "form": form, "donations": mine,
        "cancel_form": ReasonForm(reasons=DONOR_CANCEL_REASONS),
        "stats": {
            "active": sum(1 for d in mine if d.status in (Status.PENDING, Status.ASSIGNED, Status.IN_TRANSIT)),
            "kg_delivered": round(sum(float(d.quantity_kg) for d in delivered), 1),
            "meals": analytics.meals_from_kg(sum(float(d.quantity_kg) for d in delivered)),
            "expiring": sum(1 for d in mine if d.expires_soon),
            "fulfilment": round(100 * len(delivered) / terminal) if terminal else None,
        },
    })


# --------------------------------------------------------------------------- recipient
@role_required(User.Role.RECIPIENT)
def recipient_dashboard(request):
    workflow.maybe_process_timeouts()
    if request.method == "POST":
        action = request.POST.get("action", "claim")
        did = request.POST.get("donation_id")
        if action == "claim":
            _run(request, workflow.claim, did, request.user,
                 success=lambda d: f"Claimed '{d.food_item}'. We're now finding a driver.")
        elif action == "decline":
            _run(request, workflow.decline_offer, request.POST.get("offer_id"), request.user,
                 success="Offer declined. We'll offer it to the next nearest pantry.")
        elif action == "release":
            _reason_response(request, RECIPIENT_RELEASE_REASONS, workflow.release_claim, did,
                             "Claim released. The donor has been told and we'll find another pantry.")
        return redirect("donations:recipient_dashboard")

    now = timezone.now()
    offers = list(Offer.objects.filter(
        user=request.user, role=Offer.Role.RECIPIENT, status=Offer.Status.OFFERED, expires_at__gt=now,
        donation__status=Status.PENDING).select_related("donation", "donation__donor"))
    offered_ids = {o.donation_id for o in offers}
    pool = Donation.objects.filter(
        status=Status.PENDING, recipient__isnull=True, recipient_pool_open=True).exclude(
        Q(expires_at__lte=now)).select_related("donor")
    available = analytics.annotate_for_recipient([d for d in pool if d.pk not in offered_ids], request.user)
    for o in offers:
        o.donation.km_away = o.distance_km
    my_claims = list(Donation.objects.filter(recipient=request.user).select_related("donor", "driver"))
    delivered = [d for d in my_claims if d.status == Status.DELIVERED]
    return render(request, "donations/recipient_dashboard.html", {
        "offers": offers, "available": available, "my_claims": my_claims,
        "release_form": ReasonForm(reasons=RECIPIENT_RELEASE_REASONS),
        "distance_class": analytics.distance_class,
        "stats": {
            "offers": len(offers),
            "active": sum(1 for d in my_claims if d.status in (Status.ASSIGNED, Status.IN_TRANSIT)),
            "kg_received": round(sum(float(d.quantity_kg) for d in delivered), 1),
            "meals": analytics.meals_from_kg(sum(float(d.quantity_kg) for d in delivered)),
            "nearest": available[0].km_away if available and available[0].km_away is not None else None,
        },
    })


# --------------------------------------------------------------------------- driver
@role_required(User.Role.DRIVER)
def driver_dashboard(request):
    workflow.maybe_process_timeouts()
    if request.method == "POST":
        action = request.POST.get("action")
        did = request.POST.get("donation_id")
        if action == "accept":
            _run(request, workflow.accept_pickup, did, request.user,
                 success=lambda d: f"You're now delivering '{d.food_item}'.")
        elif action == "delivered":
            _run(request, workflow.mark_delivered, did, request.user,
                 success=lambda d: f"Marked '{d.food_item}' as delivered. Thank you!")
        elif action == "decline":
            _run(request, workflow.decline_offer, request.POST.get("offer_id"), request.user,
                 success="Offer declined. We'll offer it to the next nearest driver.")
        elif action == "withdraw":
            _reason_response(request, DRIVER_WITHDRAW_REASONS, workflow.withdraw_pickup, did,
                             "You've been taken off that delivery. The donor and pantry have been told.")
        return redirect("donations:driver_dashboard")

    user = request.user
    now = timezone.now()
    ready = user.can_pick_up
    live_offers = list(Offer.objects.filter(
        user=user, role=Offer.Role.DRIVER, status=Offer.Status.OFFERED, expires_at__gt=now,
        donation__status=Status.ASSIGNED, donation__driver__isnull=True,
    ).select_related("donation", "donation__donor", "donation__recipient"))
    offered_ids = {o.donation_id for o in live_offers}

    waiting = Donation.objects.filter(status=Status.ASSIGNED, driver__isnull=True).exclude(
        Q(expires_at__lte=now)).select_related("donor", "recipient")
    visible = [d for d in waiting if d.driver_pool_open or d.pk in offered_ids]
    jobs = analytics.annotate_for_driver(visible, user)

    try:
        radius = float(request.GET.get("radius", ""))
    except ValueError:
        radius = None
    shown = [j for j in jobs if radius is None or (j.km_to_pickup is not None and j.km_to_pickup <= radius)]

    offer_jobs = [j for j in jobs if j.pk in offered_ids]
    by_id = {o.donation_id: o for o in live_offers}
    for j in offer_jobs:
        j.offer = by_id[j.pk]

    mine = list(Donation.objects.filter(driver=user, status=Status.IN_TRANSIT).select_related("donor", "recipient"))
    for d in mine:
        d.km_leg = distance_km(d.effective_area, d.recipient.area) if d.recipient_id else None
    completed = Donation.objects.filter(driver=user, status=Status.DELIVERED)[:10]
    return render(request, "donations/driver_dashboard.html", {
        "ready": ready, "offer_jobs": offer_jobs,
        "needs_driver": [j for j in shown if j.pk not in offered_ids],
        "my_deliveries": mine, "completed": completed,
        "withdraw_form": ReasonForm(reasons=DRIVER_WITHDRAW_REASONS),
        "snapshot": analytics.driver_snapshot(user, jobs),
        "radius": radius, "distance_class": analytics.distance_class,
        "missing": [n for n, ok in (("area", bool(user.area)), ("street address", bool(user.address.strip()))) if not ok],
    })


# --------------------------------------------------------------------------- admin
@login_required
def admin_dashboard(request):
    if not request.user.is_admin_role():
        raise PermissionDenied
    workflow.maybe_process_timeouts()
    donations = Donation.objects.select_related("donor", "recipient", "driver")
    now = timezone.now()
    return render(request, "donations/admin_dashboard.html", {
        "donations": donations[:200],
        "cancel_form": ReasonForm(reasons=None),
        "counts": {
            "pending": donations.filter(status=Status.PENDING).count(),
            "in_progress": donations.filter(status__in=[Status.ASSIGNED, Status.IN_TRANSIT]).count(),
            "delivered": donations.filter(status=Status.DELIVERED).count(),
            "expired": donations.filter(status=Status.EXPIRED).count(),
            "cancelled": donations.filter(status=Status.CANCELLED).count(),
            "live_offers": Offer.objects.filter(status=Offer.Status.OFFERED, expires_at__gt=now).count(),
            "donors": User.objects.filter(role=User.Role.DONOR).count(),
            "recipients": User.objects.filter(role=User.Role.RECIPIENT).count(),
            "drivers": User.objects.filter(role=User.Role.DRIVER).count(),
            "open_issues": Feedback.objects.filter(is_issue=True, resolved=False).count(),
            "awaiting_approval": User.objects.filter(approval_status=User.Approval.PENDING)
                                 .exclude(role=User.Role.ADMIN).exclude(is_staff=True).count(),
        },
        "pending_users": User.objects.filter(approval_status=User.Approval.PENDING)
                         .exclude(role=User.Role.ADMIN).exclude(is_staff=True).order_by("date_joined")[:5],
    })


@admin_required
def admin_cancel(request):
    """Admin cancels any open listing, recording the reason."""
    if request.method == "POST":
        form = ReasonForm(request.POST, reasons=None)
        if form.is_valid():
            _run(request, workflow.cancel_listing, request.POST.get("donation_id"), request.user,
                 form.cleaned_data["reason"], form.cleaned_data["note"],
                 success="Listing cancelled and everyone involved has been told why.")
        else:
            messages.error(request, "Please choose a reason.")
    return redirect("donations:admin_dashboard")


@login_required
def export_delivered_csv(request):
    """CSV report of delivered food, for food auditors."""
    if not request.user.is_admin_role():
        raise PermissionDenied

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="foodbridge_delivered_report.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Food item", "Quantity (kg)", "Donor", "Recipient", "Driver",
        "Pickup area", "Pickup address", "Date listed", "Delivered at",
    ])
    delivered = Donation.objects.filter(status=Status.DELIVERED).select_related("donor", "recipient", "driver")
    for d in delivered:
        writer.writerow([
            d.food_item, d.quantity_kg, d.donor_name,
            d.recipient.get_full_name() if d.recipient else "",
            d.driver.get_full_name() if d.driver else "",
            d.area_display, d.pickup_address,
            d.date_listed.strftime("%Y-%m-%d %H:%M"),
            d.delivered_at.strftime("%Y-%m-%d %H:%M") if d.delivered_at else "",
        ])
    return response


# --------------------------------------------------------------------------- reports
def _parse_period(request):
    """Reads ?range=7|30|90|all or ?from=&to= into (start_date, end_date, label)."""
    today = timezone.localdate()
    rng = request.GET.get("range", "30")
    frm, to = request.GET.get("from"), request.GET.get("to")
    if frm or to:
        try:
            start = date.fromisoformat(frm) if frm else None
            end = date.fromisoformat(to) if to else None
            return start, end, "custom", frm or "", to or ""
        except ValueError:
            pass
    if rng == "all":
        return None, None, "all", "", ""
    days = {"7": 7, "30": 30, "90": 90}.get(rng, 30)
    return today - timedelta(days=days - 1), today, str(days), "", ""


def _report_context(request, only=None):
    start_d, end_d, rng, frm, to = _parse_period(request)
    start, end = analytics.period_bounds(start_d, end_d)
    if only is None:
        summary = analytics.network_summary(start, end)
    else:
        summary = None
    return start, end, {
        "range": rng, "from": frm, "to": to,
        "period_label": "All time" if rng == "all" else (
            f"{start_d:%d %b %Y} - {end_d:%d %b %Y}" if start_d and end_d else "Custom period"),
        "summary": summary,
    }


@admin_required
def partnership_report(request):
    """Partnership Activity Report: how every donor, pantry and driver is contributing."""
    workflow.maybe_process_timeouts()
    start, end, ctx = _report_context(request)
    kind = request.GET.get("export")
    if kind:
        return _export_report(kind, start, end)
    ctx.update({
        "donors": analytics.donor_rows(start, end),
        "recipients": analytics.recipient_rows(start, end),
        "drivers": analytics.driver_rows(start, end),
        "partnerships": analytics.partnership_rows(start, end),
        "mine": False,
    })
    return render(request, "donations/reports.html", ctx)


@login_required
def my_activity_report(request):
    """A partner's own activity report (donor, pantry or driver)."""
    user = request.user
    if user.is_admin_role():
        return redirect("donations:partnership_report")
    start, end, ctx = _report_context(request, only=user)
    ctx.update({"mine": True, "partnerships": analytics.partnership_rows(start, end, only=user)})
    if user.role == User.Role.DONOR:
        ctx["donors"] = analytics.donor_rows(start, end, only=user)
    elif user.role == User.Role.RECIPIENT:
        ctx["recipients"] = analytics.recipient_rows(start, end, only=user)
    elif user.role == User.Role.DRIVER:
        ctx["drivers"] = analytics.driver_rows(start, end, only=user)
    kind = request.GET.get("export")
    if kind:
        return _export_report(kind, start, end, only=user)
    return render(request, "donations/reports.html", ctx)


def _export_report(kind, start, end, only=None):
    tables = {
        "donors": (["Donor", "Area", "Listings", "Kg listed", "Delivered", "Kg delivered", "Cancelled",
                    "Expired", "Fulfilment %", "Avg hours to claim", "Avg rating"],
                   lambda r: [r["name"], r["area"], r["listings"], r["kg_listed"], r["delivered"],
                              r["kg_delivered"], r["cancelled"], r["expired"], r["fulfilment"],
                              r["avg_hours_to_claim"], r["rating"]],
                   lambda: analytics.donor_rows(start, end, only=only if only and only.role == User.Role.DONOR else None)),
        "recipients": (["Pantry / recipient", "Area", "Claims", "Delivered", "Kg received", "Est. meals",
                        "Claims released", "Offers", "Offers accepted", "Offers timed out", "Response %", "Avg rating"],
                       lambda r: [r["name"], r["area"], r["claims"], r["delivered"], r["kg_received"], r["meals"],
                                  r["released"], r["offers"], r["offers_accepted"], r["offers_timed_out"],
                                  r["response_rate"], r["rating"]],
                       lambda: analytics.recipient_rows(start, end, only=only if only and only.role == User.Role.RECIPIENT else None)),
        "drivers": (["Driver", "Area", "Profile ready", "Jobs", "Delivered", "Kg moved", "Approx km",
                     "Withdrawn", "Avg hours to collect", "Avg rating"],
                    lambda r: [r["name"], r["area"], "yes" if r["profile_ready"] else "no", r["jobs"],
                               r["delivered"], r["kg_moved"], r["km"], r["withdrawn"], r["avg_hours_to_collect"], r["rating"]],
                    lambda: analytics.driver_rows(start, end, only=only if only and only.role == User.Role.DRIVER else None)),
        "partnerships": (["Donor", "Pantry / recipient", "Deliveries", "Kg", "Est. meals", "Last delivery"],
                         lambda r: [r["donor"], r["recipient"], r["deliveries"], r["kg"], r["meals"],
                                    r["last"].strftime("%Y-%m-%d") if r["last"] else ""],
                         lambda: analytics.partnership_rows(start, end, only=only)),
    }
    if kind not in tables:
        raise Http404("Unknown report")
    header, row_fn, rows_fn = tables[kind]
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="foodbridge_{kind}_activity.csv"'
    writer = csv.writer(response)
    writer.writerow(header)
    for r in rows_fn():
        writer.writerow(row_fn(r))
    return response
