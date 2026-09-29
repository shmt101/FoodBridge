from django.urls import path
from . import views

app_name = "donations"

urlpatterns = [
    path("live/", views.live_donations, name="live"),
    path("dashboard/donor/", views.donor_dashboard, name="donor_dashboard"),
    path("dashboard/recipient/", views.recipient_dashboard, name="recipient_dashboard"),
    path("dashboard/driver/", views.driver_dashboard, name="driver_dashboard"),
    path("dashboard/admin/", views.admin_dashboard, name="admin_dashboard"),
    path("dashboard/admin/cancel/", views.admin_cancel, name="admin_cancel"),
    path("reports/delivered.csv", views.export_delivered_csv, name="export_delivered_csv"),
    path("reports/", views.partnership_report, name="partnership_report"),
    path("reports/mine/", views.my_activity_report, name="my_report"),
    path("feedback/", views.feedback_list, name="feedback"),
    path("map/", views.live_map, name="map"),
    path("map.json", views.map_data, name="map_data"),
    path("<int:pk>/rate/", views.submit_feedback, name="rate"),
    path("<int:pk>/", views.donation_detail, name="detail"),
]
