from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from inbox import services as notes

from . import approvals
from .decorators import admin_required
from .forms import ProfileForm, SignUpForm
from .models import User


class RoleAwareLoginView(LoginView):
    template_name = "accounts/login.html"

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        form.fields["username"].widget.attrs.setdefault("placeholder", "Username")
        form.fields["password"].widget.attrs.setdefault("placeholder", "Password")
        return form


def signup(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            notes.notify_admins_new_signup(user)
            login(request, user)
            messages.success(
                request,
                f"Thanks {user.first_name}! Your account is created and waiting for admin approval.",
            )
            return redirect("accounts:pending")
    else:
        form = SignUpForm()
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def pending(request):
    """Status page for accounts that aren't approved yet (or were rejected)."""
    if request.user.is_approved:
        return redirect("accounts:dashboard")
    return render(request, "accounts/pending.html")


@login_required
def dashboard(request):
    """Single entry point that routes each role to its own dashboard (RBAC)."""
    user = request.user
    if not user.is_approved:
        return redirect("accounts:pending")
    if user.is_staff or user.is_superuser:
        return redirect("donations:admin_dashboard")
    role = user.role
    if role == User.Role.DONOR:
        return redirect("donations:donor_dashboard")
    if role == User.Role.RECIPIENT:
        return redirect("donations:recipient_dashboard")
    if role == User.Role.DRIVER:
        return redirect("donations:driver_dashboard")
    return redirect("donations:admin_dashboard")


@login_required
def profile(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)
    return render(request, "accounts/profile.html", {"form": form})


@admin_required
def approvals_queue(request):
    """Admin queue: approve or reject new Donor / Recipient / Driver accounts."""
    if request.method == "POST":
        target = get_object_or_404(User, pk=request.POST.get("user_id"))
        action = request.POST.get("action")
        if target.is_admin_role():
            messages.error(request, "Admin accounts don't need approval.")
        elif action == "approve":
            approvals.approve_user(target, request.user)
            messages.success(request, f"Approved {target.display_name}.")
        elif action == "reject":
            approvals.reject_user(target, request.user, request.POST.get("reason", ""))
            messages.success(request, f"Rejected {target.display_name}.")
        return redirect(f"{request.path}?tab={request.POST.get('tab', 'pending')}")

    tab = request.GET.get("tab", "pending")
    if tab not in ("pending", "approved", "rejected"):
        tab = "pending"
    base = User.objects.exclude(role=User.Role.ADMIN).exclude(is_staff=True)
    counts = dict(base.values_list("approval_status").annotate(n=Count("id")).order_by())
    users = base.filter(approval_status=tab).order_by("-date_joined")
    return render(request, "accounts/approvals.html", {
        "tab": tab, "users": users,
        "counts": {k: counts.get(k, 0) for k in ("pending", "approved", "rejected")},
    })
