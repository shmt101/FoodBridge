from django.contrib import admin
from .models import Donation, DonationEvent, Offer


class OfferInline(admin.TabularInline):
    model = Offer
    extra = 0
    fields = ("user", "role", "wave", "status", "distance_km", "expires_at")
    readonly_fields = fields


class EventInline(admin.TabularInline):
    model = DonationEvent
    extra = 0
    fields = ("created_at", "kind", "actor", "note")
    readonly_fields = fields


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ("food_item", "quantity_kg", "donor", "recipient", "driver", "status",
                    "pickup_area", "expires_at", "date_listed")
    list_filter = ("status", "cancel_reason")
    search_fields = ("food_item", "donor__username", "recipient__username")
    inlines = [OfferInline, EventInline]


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ("donation", "user", "role", "wave", "status", "distance_km", "expires_at")
    list_filter = ("role", "status")
