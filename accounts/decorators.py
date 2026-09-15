from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """RBAC gate: only lets users with one of the given roles (or staff) through."""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.is_staff or request.user.role in roles:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("You don't have access to this page.")
        return _wrapped
    return decorator
