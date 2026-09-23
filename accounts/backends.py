from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Sign in with either a username or an email address."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if not username or password is None:
            return None

        login = username.strip()
        user = User.objects.filter(**{User.USERNAME_FIELD: login}).first()
        if user is None and '@' in login:
            matches = list(User.objects.filter(email__iexact=login)[:2])
            # Refuse if two accounts share this email, so we never guess.
            user = matches[0] if len(matches) == 1 else None

        if user is None:
            # Run the hasher anyway so response time doesn't reveal which accounts exist.
            User().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
