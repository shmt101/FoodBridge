"""Numbers behind the dashboards and the Partnership Activity Reports."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.areas import distance_km
from .models import Donation, DonationEvent, Offer

Status = Donation.Status


def _f(value):
    return float(value or 0)


def meals_from_kg(kg):
    return round(_f(kg) * getattr(settings, "MEALS_PER_KG", 2))


def co2e_from_kg(kg):
    return round(_f(kg) * getattr(settings, "CO2E_KG_PER_KG_FOOD", 1.0))


def _hours(delta):
    return round(delta.total_seconds() / 3600, 1)


def _avg(values):
    values = list(values)
    return round(sum(values) / len(values), 1) if values else None


# --------------------------------------------------------------------------- distances
def annotate_for_driver(donations, driver):
    """Attach km figures to donations for a driver: to pickup, the delivery leg, and total."""
    rows = []
    for d in donations:
        to_pickup = distance_km(driver.area, d.effective_area)
        leg = distance_km(d.effective_area, d.recipient.area) if d.recipient_id else None
        d.km_to_pickup = to_pickup
        d.km_leg = leg
        d.km_total = round(to_pickup + leg, 1) if to_pickup is not None and leg is not None else None
        rows.append(d)
    rows.sort(key=lambda d: (d.km_to_pickup is None, d.km_to_pickup or 0, d.date_listed))
    return rows


def annotate_for_recipient(donations, recipient):
    rows = []
    for d in donations:
        d.km_away = distance_km(recipient.area, d.effective_area)
        rows.append(d)
    rows.sort(key=lambda d: (d.km_away is None, d.km_away or 0, d.date_listed))
    return rows


def distance_class(km):
    if km is None:
        return "dist-unknown"
    if km <= 10:
        return "dist-near"
    if km <= 30:
        return "dist-mid"
    return "dist-far"


def driver_snapshot(driver, jobs):
    """Proximity analytics for the driver dashboard. `jobs` are annotated donations."""
    known = [j.km_to_pickup for j in jobs if j.km_to_pickup is not None]
    bands = [
        ("Within 10 km", sum(1 for k in known if k <= 10)),
        ("10-25 km", sum(1 for k in known if 10 < k <= 25)),
        ("25-50 km", sum(1 for k in known if 25 < k <= 50)),
        ("Over 50 km", sum(1 for k in known if k > 50)),
    ]
    top = max([n for _, n in bands] + [1])
    done = list(Donation.objects.filter(driver=driver, status=Status.DELIVERED).select_related("recipient", "donor"))
    km_done = 0.0
    for d in done:
        a = distance_km(driver.area, d.effective_area)
        b = distance_km(d.effective_area, d.recipient.area) if d.recipient_id else None
        km_done += (a or 0) + (b or 0)
    return {
        "open_jobs": len(jobs),
        "nearest_km": min(known) if known else None,
        "within_25": sum(1 for k in known if k <= 25),
        "bands": [{"label": l, "n": n, "pct": round(100 * n / top)} for l, n in bands],
        "deliveries": len(done),
        "kg_moved": round(sum(_f(d.quantity_kg) for d in done), 1),
        "km_driven": round(km_done, 1),
        "avg_km": round(km_done / len(done), 1) if done else None,
    }


# --------------------------------------------------------------------------- reports
def period_bounds(start_date=None, end_date=None):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(start_date, time.min), tz) if start_date else None
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz) if end_date else None
    return start, end


def _donations_in(start, end):
    qs = Donation.objects.select_related("donor", "recipient", "driver")
    if start:
        qs = qs.filter(date_listed__gte=start)
    if end:
        qs = qs.filter(date_listed__lt=end)
    return qs


def network_summary(start=None, end=None):
    donations = list(_donations_in(start, end))
    by_status = Counter(d.status for d in donations)
    delivered = [d for d in donations if d.status == Status.DELIVERED]
    kg_listed = sum(_f(d.quantity_kg) for d in donations)
    kg_delivered = sum(_f(d.quantity_kg) for d in delivered)
    terminal = by_status[Status.DELIVERED] + by_status[Status.CANCELLED] + by_status[Status.EXPIRED]
    cancel_reasons = Counter(
        d.get_cancel_reason_display() for d in donations if d.status == Status.CANCELLED and d.cancel_reason)
    top = max(list(cancel_reasons.values()) + [1])

    weekly = defaultdict(float)
    for d in delivered:
        when = timezone.localtime(d.delivered_at or d.date_updated).date()
        weekly[when - timedelta(days=when.weekday())] += _f(d.quantity_kg)
    weeks = sorted(weekly)[-8:]
    wtop = max([weekly[w] for w in weeks] + [1])

    offers = Offer.objects.all()
    if start:
        offers = offers.filter(created_at__gte=start)
    if end:
        offers = offers.filter(created_at__lt=end)
    offer_counts = Counter(offers.values_list("status", flat=True))
    answered = offer_counts["accepted"] + offer_counts["declined"] + offer_counts["timed_out"]

    return {
        "listings": len(donations),
        "kg_listed": round(kg_listed, 1),
        "kg_delivered": round(kg_delivered, 1),
        "meals": meals_from_kg(kg_delivered),
        "co2e": co2e_from_kg(kg_delivered),
        "delivered": by_status[Status.DELIVERED],
        "cancelled": by_status[Status.CANCELLED],
        "expired": by_status[Status.EXPIRED],
        "in_progress": by_status[Status.PENDING] + by_status[Status.ASSIGNED] + by_status[Status.IN_TRANSIT],
        "fulfilment_rate": round(100 * by_status[Status.DELIVERED] / terminal) if terminal else None,
        "avg_hours_to_claim": _avg(_hours(d.claimed_at - d.date_listed) for d in donations if d.claimed_at),
        "avg_hours_to_deliver": _avg(_hours(d.delivered_at - d.date_listed) for d in delivered if d.delivered_at),
        "cancel_reasons": [{"label": k, "n": v, "pct": round(100 * v / top)} for k, v in cancel_reasons.most_common()],
        "weekly": [{"label": w.strftime("%d %b"), "kg": round(weekly[w], 1), "pct": round(100 * weekly[w] / wtop)} for w in weeks],
        "offers_total": sum(offer_counts.values()),
        "offer_accept_rate": round(100 * offer_counts["accepted"] / answered) if answered else None,
        "offers_timed_out": offer_counts["timed_out"],
    }


def donor_rows(start=None, end=None, only=None):
    User = get_user_model()
    donations = list(_donations_in(start, end))
    by_donor = defaultdict(list)
    for d in donations:
        by_donor[d.donor_id].append(d)
    users = User.objects.filter(role=User.Role.DONOR).order_by("organisation_name", "username")
    if only:
        users = users.filter(pk=only.pk)
    rows = []
    for u in users:
        ds = by_donor.get(u.pk, [])
        delivered = [d for d in ds if d.status == Status.DELIVERED]
        terminal = len(delivered) + sum(1 for d in ds if d.status in (Status.CANCELLED, Status.EXPIRED))
        rows.append({
            "user": u, "name": u.display_name, "area": u.area_display,
            "listings": len(ds),
            "kg_listed": round(sum(_f(d.quantity_kg) for d in ds), 1),
            "delivered": len(delivered),
            "kg_delivered": round(sum(_f(d.quantity_kg) for d in delivered), 1),
            "cancelled": sum(1 for d in ds if d.status == Status.CANCELLED),
            "expired": sum(1 for d in ds if d.status == Status.EXPIRED),
            "fulfilment": round(100 * len(delivered) / terminal) if terminal else None,
            "avg_hours_to_claim": _avg(_hours(d.claimed_at - d.date_listed) for d in ds if d.claimed_at),
        })
    return [r for r in rows if r["listings"] or only]


def recipient_rows(start=None, end=None, only=None):
    User = get_user_model()
    donations = list(_donations_in(start, end).filter(recipient__isnull=False))
    by_rec = defaultdict(list)
    for d in donations:
        by_rec[d.recipient_id].append(d)
    released = Counter(DonationEvent.objects.filter(kind=DonationEvent.Kind.RELEASED)
                       .values_list("actor_id", flat=True))
    offers = Offer.objects.filter(role=Offer.Role.RECIPIENT)
    if start:
        offers = offers.filter(created_at__gte=start)
    if end:
        offers = offers.filter(created_at__lt=end)
    per_user = defaultdict(Counter)
    for uid, status in offers.values_list("user_id", "status"):
        per_user[uid][status] += 1
    users = User.objects.filter(role=User.Role.RECIPIENT).order_by("organisation_name", "username")
    if only:
        users = users.filter(pk=only.pk)
    rows = []
    for u in users:
        ds = by_rec.get(u.pk, [])
        delivered = [d for d in ds if d.status == Status.DELIVERED]
        oc = per_user.get(u.pk, Counter())
        answered = oc["accepted"] + oc["declined"] + oc["timed_out"]
        rows.append({
            "user": u, "name": u.display_name, "area": u.area_display,
            "claims": len(ds), "delivered": len(delivered),
            "kg_received": round(sum(_f(d.quantity_kg) for d in delivered), 1),
            "meals": meals_from_kg(sum(_f(d.quantity_kg) for d in delivered)),
            "released": released.get(u.pk, 0),
            "offers": sum(oc.values()), "offers_accepted": oc["accepted"],
            "offers_timed_out": oc["timed_out"],
            "response_rate": round(100 * oc["accepted"] / answered) if answered else None,
        })
    return [r for r in rows if r["claims"] or r["offers"] or only]


def driver_rows(start=None, end=None, only=None):
    User = get_user_model()
    donations = list(_donations_in(start, end).filter(driver__isnull=False))
    by_drv = defaultdict(list)
    for d in donations:
        by_drv[d.driver_id].append(d)
    withdrawn = Counter(DonationEvent.objects.filter(kind=DonationEvent.Kind.WITHDRAWN)
                        .values_list("actor_id", flat=True))
    users = User.objects.filter(role=User.Role.DRIVER).order_by("first_name", "username")
    if only:
        users = users.filter(pk=only.pk)
    rows = []
    for u in users:
        ds = by_drv.get(u.pk, [])
        delivered = [d for d in ds if d.status == Status.DELIVERED]
        km = 0.0
        for d in delivered:
            km += (distance_km(u.area, d.effective_area) or 0)
            km += (distance_km(d.effective_area, d.recipient.area) if d.recipient_id else 0) or 0
        rows.append({
            "user": u, "name": u.display_name, "area": u.area_display,
            "profile_ready": u.profile_complete,
            "jobs": len(ds), "delivered": len(delivered),
            "kg_moved": round(sum(_f(d.quantity_kg) for d in delivered), 1),
            "km": round(km, 1), "withdrawn": withdrawn.get(u.pk, 0),
            "avg_hours_to_collect": _avg(
                _hours(d.picked_up_at - d.claimed_at) for d in ds if d.picked_up_at and d.claimed_at),
        })
    return [r for r in rows if r["jobs"] or only]


def partnership_rows(start=None, end=None, only=None):
    """Donor <-> pantry pairs: who has actually supplied whom."""
    donations = _donations_in(start, end).filter(status=Status.DELIVERED, recipient__isnull=False)
    if only is not None:
        from django.db.models import Q
        donations = donations.filter(Q(donor=only) | Q(recipient=only) | Q(driver=only))
    pairs = {}
    for d in donations:
        key = (d.donor_id, d.recipient_id)
        row = pairs.setdefault(key, {
            "donor": d.donor_name, "recipient": d.recipient.display_name,
            "deliveries": 0, "kg": 0.0, "last": None,
        })
        row["deliveries"] += 1
        row["kg"] += _f(d.quantity_kg)
        when = d.delivered_at or d.date_updated
        row["last"] = max(row["last"], when) if row["last"] else when
    rows = list(pairs.values())
    for r in rows:
        r["kg"] = round(r["kg"], 1)
        r["meals"] = meals_from_kg(r["kg"])
    rows.sort(key=lambda r: (-r["kg"], r["donor"]))
    return rows
