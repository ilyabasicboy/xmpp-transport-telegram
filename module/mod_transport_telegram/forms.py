from django import forms


class TransportTelegramForm(forms.Form):
    allowed_components = forms.CharField(
        required=False,
        widget=forms.Textarea,
        help_text="One allowed Telegram transport component domain per line.",
    )
