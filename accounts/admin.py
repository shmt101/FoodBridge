from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm

from .areas import area_choices
from . import approvals
from .models import User


class UserAdminForm(UserChangeForm):
    """Keeps area restricted to a real area code - the plain CharField has no choices of
    its own, so without this, admin could type free text here that never matches a real
    area, silently breaking distance-based matching for that person."""

    area = forms.ChoiceField(choices=area_choices("No area set"), required=False)

    class Meta(UserChangeForm.Meta):
        model = User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    form = UserAdminForm
    list_display = ("username", "email", "role", "approval_status", "area", "is_staff", "date_joined")
    list_filter = ("role", "approval_status", "is_staff", "is_active")
    actions = ["approve_selected", "reject_selected"]
    fieldsets = UserAdmin.fieldsets + (
        ("FoodBridge profile", {
            "fields": ("role", "phone", "address", "area", "organisation_name", "bio", "avatar")
        }),
        ("Approval", {
            "fields": ("approval_status", "approved_at", "approved_by", "rejection_reason")
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("FoodBridge profile", {"fields": ("role", "email")}),
    )

    @admin.action(description="Approve selected accounts")
    def approve_selected(self, request, queryset):
        n = 0
        for user in queryset:
            approvals.approve_user(user, request.user)
            n += 1
        self.message_user(request, f"Approved {n} account(s).", messages.SUCCESS)

    @admin.action(description="Reject selected accounts")
    def reject_selected(self, request, queryset):
        n = 0
        for user in queryset:
            approvals.reject_user(user, request.user, "Rejected by an administrator")
            n += 1
        self.message_user(request, f"Rejected {n} account(s).", messages.SUCCESS)
