from .models import Message, Notification


def unread_counts(request):
    """Adds unread notification / message counts for the nav badge on every page."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    notifications = Notification.objects.filter(user=user, read_at__isnull=True).count()
    messages_ = Message.objects.filter(recipient=user, read_at__isnull=True).count()
    return {
        "unread_notifications": notifications,
        "unread_messages": messages_,
        "unread_total": notifications + messages_,
    }
