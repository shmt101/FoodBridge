from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def role_required(*roles):
    """RBAC gate: only lets approved users with one of the given roles (or staff) through."""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_approved:
                return redirect("accounts:pending")
            if user.is_staff or user.role in roles:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("You don't have access to this page.")
        return _wrapped
    return decorator


def admin_required(view_func):
    """Only admin/auditor accounts (or staff)."""

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_admin_role():
            raise PermissionDenied("Admins only.")
        return view_func(request, *args, **kwargs)
    return _wrapped
