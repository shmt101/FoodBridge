from django.urls import path
from . import views

app_name = "donations"

urlpatterns = [
    path("live/", views.live_donations, name="live"),
    path("dashboard/donor/", views.donor_dashboard, name="donor_dashboard"),
    path("dashboard/recipient/", views.recipient_dashboard, name="recipient_dashboard"),
    path("dashboard/driver/", views.driver_dashboard, name="driver_dashboard"),
    path("dashboard/admin/", views.admin_dashboard, name="admin_dashboard"),
    path("reports/delivered.csv", views.export_delivered_csv, name="export_delivered_csv"),
]
