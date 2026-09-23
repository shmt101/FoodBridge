from django.urls import path

from . import views

app_name = "inbox"

urlpatterns = [
    path("", views.inbox_home, name="home"),
    path("new/", views.new_message, name="new"),
    path("with/<int:user_id>/", views.conversation, name="conversation"),
    path("notifications/<int:pk>/", views.notification_open, name="notification_open"),
    path("notifications/read-all/", views.mark_all_read, name="mark_all_read"),
    path("unread.json", views.unread_json, name="unread"),
]
