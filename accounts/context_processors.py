from foodbridge import media_library

from .models import User


def portal(request):
    """Admin nav badge (accounts waiting for approval) and the intro video for the auth pages."""
    ctx = {"intro_video": media_library.INTRO_VIDEO}
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_admin_role():
        ctx["pending_approvals"] = (
            User.objects.filter(approval_status=User.Approval.PENDING)
            .exclude(role=User.Role.ADMIN).exclude(is_staff=True).count()
        )
    return ctx
