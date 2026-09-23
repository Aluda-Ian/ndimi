from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.cache import cache

User = get_user_model()

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True, help_text='Used to confirm your account and reset your password.')

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label='Username or email',
        max_length=254,
        widget=forms.TextInput(attrs={'autofocus': True, 'autocomplete': 'username'}),
    )
    remember_me = forms.BooleanField(required=False, initial=True, label='Keep me signed in')

    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': 'That username or email and password don\'t match. Passwords are case-sensitive.',
        'locked': 'Too many failed attempts. Try again in 15 minutes, or reset your password.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Django shortens this to the username length (150); emails can be longer.
        self.fields['username'].max_length = 254
        self.fields['username'].widget.attrs['maxlength'] = 254

    def _lock_key(self):
        ip = self.request.META.get('REMOTE_ADDR', '') if self.request else ''
        login = (self.data.get('username') or '').strip().lower()
        return f'login-failures:{ip}:{login}'

    def clean(self):
        key = self._lock_key()
        failures = cache.get(key, 0)
        if failures >= MAX_FAILED_LOGINS:
            raise forms.ValidationError(self.error_messages['locked'], code='locked')
        try:
            cleaned = super().clean()
        except forms.ValidationError:
            cache.set(key, failures + 1, LOCKOUT_SECONDS)
            raise
        cache.delete(key)
        return cleaned


class ProfileForm(forms.ModelForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Another account already uses this email.')
        return email


class DeleteAccountForm(forms.Form):
    password = forms.CharField(
        label='Your password', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )
    confirm = forms.BooleanField(label='I understand this permanently deletes my account and projects list.')

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user.check_password(password):
            raise forms.ValidationError('That password is incorrect.')
        return password
