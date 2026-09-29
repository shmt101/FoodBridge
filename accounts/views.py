from django.contrib import messages
from django.contrib.auth import login
from django.core.cache import cache
from django.db.models import Q
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


MAX_LOGIN_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60


def _throttle_key(request, username):
    ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
          or request.META.get("REMOTE_ADDR", ""))
    return f"login-fail:{(username or '').lower()}:{ip}"


class RoleAwareLoginView(LoginView):
    """Login with a simple brute-force guard: 5 wrong passwords locks that user+IP for 15 minutes."""

    template_name = "accounts/login.html"

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "")
        if cache.get(_throttle_key(request, username), 0) >= MAX_LOGIN_FAILURES:
            form = self.get_form()
            form.full_clean()
            self.locked = True
            return self.render_to_response(self.get_context_data(form=form, locked=True), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = _throttle_key(self.request, self.request.POST.get("username", ""))
        cache.set(key, cache.get(key, 0) + 1, LOCKOUT_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_throttle_key(self.request, self.request.POST.get("username", "")))
        return super().form_valid(form)

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


ROLE_CHOICES = [User.Role.DONOR, User.Role.RECIPIENT, User.Role.DRIVER]


@admin_required
def manage_users(request):
    """Admin user management: search, deactivate / reactivate, change role, send back to pending."""
    if request.method == "POST":
        target = get_object_or_404(User, pk=request.POST.get("user_id"))
        action = request.POST.get("action")
        protected = target.is_superuser or target.is_admin_role()
        if target.pk == request.user.pk:
            messages.error(request, "You can't change your own account here.")
        elif protected and not request.user.is_superuser:
            messages.error(request, "Only a superuser can change admin accounts.")
        elif action == "deactivate":
            target.is_active = False
            target.save(update_fields=["is_active"])
            messages.success(request, f"Deactivated {target.display_name}. They can no longer log in.")
        elif action == "activate":
            target.is_active = True
            target.save(update_fields=["is_active"])
            messages.success(request, f"Reactivated {target.display_name}.")
        elif action == "role" and request.POST.get("role") in ROLE_CHOICES and not protected:
            target.role = request.POST["role"]
            target.save(update_fields=["role"])
            messages.success(request, f"{target.display_name} is now a {target.get_role_display().lower()}.")
        elif action == "pending" and not protected:
            target.approval_status = User.Approval.PENDING
            target.approved_at = None
            target.save(update_fields=["approval_status", "approved_at"])
            messages.success(request, f"{target.display_name} was sent back to the approval queue.")
        else:
            messages.error(request, "That change isn't allowed.")
        return redirect(request.get_full_path())

    users = User.objects.all().order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    state = request.GET.get("state", "")
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)
                             | Q(last_name__icontains=q) | Q(organisation_name__icontains=q))
    if role in User.Role.values:
        users = users.filter(role=role)
    if state == "inactive":
        users = users.filter(is_active=False)
    elif state in User.Approval.values:
        users = users.filter(approval_status=state)
    return render(request, "accounts/manage_users.html", {
        "users": users[:200], "q": q, "role": role, "state": state,
        "roles": ROLE_CHOICES, "role_filter": User.Role.choices,
        "approval_choices": User.Approval.choices,
    })
