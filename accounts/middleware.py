from django.shortcuts import redirect

# Pages an unapproved (pending / rejected) user may still see.
_EXACT = {"/", "/home/", "/about/", "/privacy/", "/terms/"}
_PREFIXES = ("/static/", "/media/", "/admin/", "/accounts/", "/donations/live/", "/donations/map")


class ApprovalGateMiddleware:
    """Keeps pending/rejected accounts out of the portal until an admin approves them.

    Public pages, login/logout, the profile page and the 'awaiting approval' page stay open.
    Everything else (dashboards, inbox, donation actions, reports) redirects to the
    approval-status page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not user.is_approved:
            path = request.path_info
            if path not in _EXACT and not path.startswith(_PREFIXES):
                return redirect("accounts:pending")
        return self.get_response(request)
