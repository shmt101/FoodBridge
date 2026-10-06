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
    """Only Admin accounts (or staff) - approvals and user management, not reports."""

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_admin_role():
            raise PermissionDenied("Admins only.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def auditor_required(view_func):
    """Only the Auditor account (or a superuser, for troubleshooting) - never a plain Admin."""

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if request.user.is_superuser or request.user.is_auditor:
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("This page is only available to the auditor account.")
    return _wrapped


def admin_or_auditor_required(view_func):
    """Reports and CSV exports - Admins and the Auditor account (or a superuser), never a
    plain Donor/Recipient/Driver."""

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if user.is_superuser or user.is_auditor or user.is_admin_role():
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("This page is only available to admins and the auditor account.")
    return _wrapped
