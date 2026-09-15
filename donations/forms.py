from django import forms
from .models import Donation


class DonationForm(forms.ModelForm):
    """Used by donors to list a new surplus food donation."""

    class Meta:
        model = Donation
        fields = ["food_item", "quantity_kg", "pickup_address", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class DonationSearchForm(forms.Form):
    """Backs the live-donations search/filter bar."""

    q = forms.CharField(
        required=False, label="Search",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Food item or donor…"}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All statuses")] + list(Donation.Status.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
