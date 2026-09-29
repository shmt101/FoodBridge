from datetime import timedelta

from django import forms
from django.utils import timezone

from accounts.areas import STATES, area_choices, state_choices
from .models import Donation

DT_LOCAL = "%Y-%m-%dT%H:%M"

DONOR_CANCEL_REASONS = ["unavailable", "safety", "mistake", "no_pickup", "elsewhere", "other"]
RECIPIENT_RELEASE_REASONS = ["cant_collect", "no_storage", "not_needed", "other"]
DRIVER_WITHDRAW_REASONS = ["vehicle", "too_far", "schedule", "other"]


class DonationForm(forms.ModelForm):
    """Used by donors to list a new surplus food donation (with an expiry time)."""

    pickup_area = forms.ChoiceField(choices=area_choices("Select pickup area…"), label="Pickup area")
    expires_at = forms.DateTimeField(
        label="Expires / best before",
        input_formats=[DT_LOCAL, "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DT_LOCAL),
        help_text="After this time the listing closes automatically if it hasn't been collected.",
    )

    class Meta:
        model = Donation
        fields = ["food_item", "quantity_kg", "pickup_area", "pickup_address", "expires_at", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            css = "form-select" if name == "pickup_area" else "form-control"
            field.widget.attrs.setdefault("class", css)
        if not self.is_bound:
            default = timezone.localtime(timezone.now() + timedelta(hours=24)).replace(second=0, microsecond=0)
            self.fields["expires_at"].initial = default.strftime(DT_LOCAL)

    def clean_expires_at(self):
        value = self.cleaned_data["expires_at"]
        now = timezone.now()
        if value <= now + timedelta(minutes=15):
            raise forms.ValidationError("Choose an expiry time at least 15 minutes from now.")
        if value > now + timedelta(days=30):
            raise forms.ValidationError("Listings can be open for at most 30 days.")
        return value

    def clean_quantity_kg(self):
        qty = self.cleaned_data["quantity_kg"]
        if qty <= 0:
            raise forms.ValidationError("Quantity must be more than 0 kg.")
        return qty


class ReasonForm(forms.Form):
    """Cancel / release / withdraw with a recorded reason."""

    reason = forms.ChoiceField(widget=forms.Select(attrs={"class": "form-select"}))
    note = forms.CharField(
        required=False, max_length=255, label="Add a note (optional)",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Anything the others should know…"}),
    )

    def __init__(self, *args, reasons=None, **kwargs):
        super().__init__(*args, **kwargs)
        labels = dict(Donation.CancelReason.choices)
        keys = reasons or list(labels)
        self.fields["reason"].choices = [("", "Select a reason…")] + [(k, labels[k]) for k in keys]


class DonationSearchForm(forms.Form):
    """Backs the live-donations search / area filter."""

    RADIUS_CHOICES = [("", "Any distance"), ("10", "Within 10 km"), ("25", "Within 25 km"),
                      ("50", "Within 50 km"), ("100", "Within 100 km"), ("250", "Within 250 km")]

    q = forms.CharField(
        required=False, label="Search",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Food item or donor…"}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "Active listings")] + list(Donation.Status.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    state = forms.ChoiceField(
        required=False, choices=state_choices(), label="State",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    area = forms.ChoiceField(
        required=False, choices=area_choices("My area…"), label="Near",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    radius = forms.ChoiceField(
        required=False, choices=RADIUS_CHOICES, label="Distance",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
