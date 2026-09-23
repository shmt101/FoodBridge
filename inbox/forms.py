from django import forms
from django.contrib.auth import get_user_model

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
        qs = User.objects.filter(is_active=True).exclude(pk=user.pk).order_by("first_name", "username")
        self.fields["recipient"].queryset = qs
        self.fields["recipient"].label_from_instance = (
            lambda u: f"{u.display_name} — {u.get_role_display()}"
        )
