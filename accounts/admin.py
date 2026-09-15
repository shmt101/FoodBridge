from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "is_staff", "date_joined")
    list_filter = ("role", "is_staff", "is_active")
    fieldsets = UserAdmin.fieldsets + (
        ("FoodBridge profile", {
            "fields": ("role", "phone", "address", "organisation_name", "bio", "avatar")
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("FoodBridge profile", {"fields": ("role", "email")}),
    )
