from django.contrib import admin

from .models import Message, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "text", "created_at", "read_at")
    list_filter = ("kind",)
    search_fields = ("user__username", "text")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("sender", "recipient", "created_at", "read_at")
    search_fields = ("sender__username", "recipient__username", "body")
