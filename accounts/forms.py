from django import forms
from django.contrib.auth.forms import UserCreationForm

from .areas import area_choices
from .models import User


class SignUpForm(UserCreationForm):
    """Signup form with role selection — this is the RBAC entry point.

    New accounts are created in the 'pending' state; an admin has to approve them
    before they can use the portal.
    """

    role = forms.ChoiceField(
        choices=[c for c in User.Role.choices if c[0] != User.Role.ADMIN],
        widget=forms.RadioSelect,
        help_text="Choose how you'll use FoodBridge. This can't be changed later without an admin.",
    )
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=False)
    organisation_name = forms.CharField(max_length=150, required=False, label="Organisation")
    phone = forms.CharField(max_length=30, required=False)
    area = forms.ChoiceField(choices=area_choices(), required=False, label="Your area")
    address = forms.CharField(max_length=255, required=False, label="Street address")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "role",
                  "organisation_name", "phone", "area", "address", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "role":
                continue
            css = "form-select" if name == "area" else "form-control"
            field.widget.attrs.setdefault("class", css)
        self.fields["password1"].widget.attrs["data-strength"] = "1"

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = self.cleaned_data["role"]
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data.get("last_name", "")
        user.organisation_name = self.cleaned_data.get("organisation_name", "")
        user.phone = self.cleaned_data.get("phone", "")
        user.area = self.cleaned_data.get("area", "")
        user.address = self.cleaned_data.get("address", "")
        user.approval_status = User.Approval.PENDING
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    """The 'profile manager' — every role edits the same fields."""

    area = forms.ChoiceField(choices=area_choices(), required=False, label="Your area")

    class Meta:
        model = User
        fields = [
            "first_name", "last_name", "email",
            "organisation_name", "phone", "area", "address", "bio", "avatar",
        ]
        widgets = {"bio": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "avatar":
                continue
            css = "form-select" if name == "area" else "form-control"
            field.widget.attrs.setdefault("class", css)
        self.fields["address"].help_text = "Street address, e.g. 12 George St."
