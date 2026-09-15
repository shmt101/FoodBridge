from django.contrib import admin
from .models import Donation


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ("food_item", "quantity_kg", "donor", "recipient", "driver", "status", "date_listed")
    list_filter = ("status",)
    search_fields = ("food_item", "donor__username", "recipient__username")
