from django import forms
from django.contrib.auth import get_user_model

from . import services

User = get_user_model()


class MessageForm(forms.Form):
    body = forms.CharField(
        max_length=2000, label="Message",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "Write a message…"}),
    )

    def clean_body(self):
        body = self.cleaned_data["body"].strip()
        if not body:
            raise forms.ValidationError("Please write a message.")
        return body


class NewMessageForm(MessageForm):
    recipient = forms.ModelChoiceField(
        queryset=User.objects.none(), label="To",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    field_order = ["recipient", "body"]

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        # Only people this user is actually allowed to message directly: someone they
        # share a donation with (as donor/recipient/driver), or any Admin for support.
        # Admins themselves may message anyone - see services.messageable_users().
        self.fields["recipient"].queryset = services.messageable_users(user)
        self.fields["recipient"].label_from_instance = (
            lambda u: f"{u.display_name}: {u.get_role_display()}"
        )


class BroadcastForm(MessageForm):
    """Admin-only: send one message to every account at once."""
    body = forms.CharField(
        max_length=2000, label="Message to everyone",
        widget=forms.Textarea(attrs={
            "rows": 4, "class": "form-control",
            "placeholder": "Write an announcement every FoodBridge user will see…",
        }),
    )
