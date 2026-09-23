from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import MessageForm, NewMessageForm
from .models import Message, Notification

User = get_user_model()


def _conversations(user):
    """One row per person the user has chatted with: latest message + unread count."""
    recent = (
        Message.objects.filter(Q(sender=user) | Q(recipient=user))
        .select_related("sender", "recipient")
        .order_by("-created_at")[:500]
    )
    unread = dict(
        Message.objects.filter(recipient=user, read_at__isnull=True)
        .order_by().values_list("sender").annotate(n=Count("id"))
    )
    rows, seen = [], set()
    for msg in recent:
        other = msg.recipient if msg.sender_id == user.pk else msg.sender
        if other.pk in seen:
            continue
        seen.add(other.pk)
        rows.append({"other": other, "last": msg, "unread": unread.get(other.pk, 0)})
    return rows


@login_required
def inbox_home(request):
    return render(request, "inbox/inbox.html", {
        "notifications": request.user.notifications.select_related("donation")[:40],
        "conversations": _conversations(request.user),
    })


@login_required
def conversation(request, user_id):
    other = get_object_or_404(User, pk=user_id, is_active=True)
    if other.pk == request.user.pk:
        return redirect("inbox:home")

    if request.method == "POST":
        form = MessageForm(request.POST)
        if form.is_valid():
            Message.objects.create(sender=request.user, recipient=other, body=form.cleaned_data["body"])
            return redirect("inbox:conversation", user_id=other.pk)
    else:
        form = MessageForm()

    thread = list(
        Message.objects.filter(
            Q(sender=request.user, recipient=other) | Q(sender=other, recipient=request.user)
        ).order_by("-created_at")[:200]
    )[::-1]
    # Everything they sent me is now read (thread above still remembers what was new).
    Message.objects.filter(sender=other, recipient=request.user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return render(request, "inbox/conversation.html", {"other": other, "thread": thread, "form": form})


@login_required
def new_message(request):
    initial = {}
    to = request.GET.get("to", "")
    if to.isdigit():
        initial["recipient"] = int(to)
    form = NewMessageForm(request.POST or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        recipient = form.cleaned_data["recipient"]
        Message.objects.create(sender=request.user, recipient=recipient, body=form.cleaned_data["body"])
        return redirect("inbox:conversation", user_id=recipient.pk)
    return render(request, "inbox/new_message.html", {"form": form})


@login_required
def notification_open(request, pk):
    """Click-through from the inbox: mark the notification read, then go to the dashboard."""
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return redirect("accounts:dashboard")


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("inbox:home")


def unread_json(request):
    """Polled by the nav badge so new items appear without a full page reload."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "auth"}, status=401)
    n = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
    m = Message.objects.filter(recipient=request.user, read_at__isnull=True).count()
    return JsonResponse({"notifications": n, "messages": m, "total": n + m})
