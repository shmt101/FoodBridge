from django import template

register = template.Library()

_MAP = {
    "Pending": "status-pending",
    "Assigned": "status-assigned",
    "In transit": "status-transit",
    "Delivered": "status-delivered",
    "Cancelled": "status-cancelled",
}


@register.filter
def status_class(value):
    return _MAP.get(value, "status-pending")
