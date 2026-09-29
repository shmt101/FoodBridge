from django import template

register = template.Library()

_MAP = {
    "Pending": "status-pending",
    "Assigned": "status-assigned",
    "In transit": "status-transit",
    "Delivered": "status-delivered",
    "Cancelled": "status-cancelled",
    "Expired": "status-expired",
}


@register.filter
def status_class(value):
    return _MAP.get(value, "status-pending")


@register.filter
def km(value):
    """12.34 -> '12.3 km'; None -> ''."""
    if value is None or value == "":
        return ""
    return f"{float(value):g} km"


@register.filter
def dist_class(value):
    from donations.analytics import distance_class
    return distance_class(value)


@register.filter
def iso(value):
    return value.isoformat() if value else ""
